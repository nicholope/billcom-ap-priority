"""
Scoring engine — computes bill-level urgency and vendor-level priority scores.

Bill.com field mapping:
  id, vendorId, vendorName, dueDate, amount, amountDue,
  paymentStatus, approvalStatus, paymentMethodType
"""

import logging
from datetime import date, datetime
from typing import Optional

import config

logger = logging.getLogger(__name__)


def _parse_date(value: Optional[str]) -> Optional[date]:
    """Parse an ISO date string into a date object."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (ValueError, AttributeError):
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None


def _urgency_from_days(days_until_due: int) -> float:
    """Convert days-until-due into a 0–100 urgency score."""
    if days_until_due < 0:   return 100.0
    if days_until_due == 0:  return 95.0
    if days_until_due <= 2:  return 85.0
    if days_until_due <= 5:  return 70.0
    if days_until_due <= 10: return 50.0
    if days_until_due <= 20: return 30.0
    return 10.0


def _lead_time_for_method(method: Optional[str]) -> int:
    key = (method or "UNKNOWN").upper()
    return config.PAYMENT_LEAD_TIMES.get(key, config.PAYMENT_LEAD_TIMES["UNKNOWN"])


def _normalize(values: list[float]) -> list[float]:
    """Min-max normalize to [0, 100]."""
    if not values:
        return values
    lo, hi = min(values), max(values)
    if hi == lo:
        return [50.0 for _ in values]
    return [(v - lo) / (hi - lo) * 100 for v in values]


# Bill.com paymentStatus codes
_PAID_STATUSES = {"1", "paid", "partialPayment"}

# Bill.com v2 approvalStatus codes
# 0=Unassigned, 1=Assigned, 2=Approving, 3=Approved, 4=Denied
_APPROVED_STATUSES = {"3", "approved"}


class ScoringEngine:
    """Computes payment priority scores for bills and vendors."""

    def classify_priority(self, score: float) -> str:
        if score >= config.PRIORITY_CRITICAL_THRESHOLD: return "CRITICAL"
        if score >= config.PRIORITY_HIGH_THRESHOLD:     return "HIGH"
        if score >= config.PRIORITY_MEDIUM_THRESHOLD:   return "MEDIUM"
        return "LOW"

    def score_bills(self, bills: list[dict]) -> list[dict]:
        """
        Normalize Bill.com bill fields, compute derived fields and bill_score.
        Returns the same list with added scoring keys.
        """
        today = date.today()
        raw_exposure: list[float] = []

        for bill in bills:
            # --- Normalize Bill.com fields ---
            bill["vendor_name"] = (
                bill.get("vendorName")
                or bill.get("vendor_name")
                or "Unknown Vendor"
            )
            bill["vendor_id"] = bill.get("vendorId") or bill.get("vendor_id") or "UNKNOWN"

            # Amount: dueAmount minus any vendor credits applied
            # creditAmount should reduce the effective balance, not add to it
            due = float(bill.get("dueAmount") or bill.get("amountDue") or bill.get("amount") or 0)
            credit = float(bill.get("creditAmount") or 0)
            unpaid = max(0.0, due - credit)
            bill["unpaid_amount"] = unpaid
            raw_exposure.append(unpaid)

            # Due date
            due = _parse_date(bill.get("dueDate") or bill.get("due_date"))
            if due:
                delta = (due - today).days
                bill["days_until_due"] = delta
                bill["days_past_due"] = max(0, -delta)
                bill["is_overdue"] = delta < 0
                bill["due_date"] = due.isoformat()
            else:
                bill["days_until_due"] = 999
                bill["days_past_due"] = 0
                bill["is_overdue"] = False
                bill["due_date"] = ""

            # Payment method
            method = (
                bill.get("paymentMethodType")
                or bill.get("payment_method")
                or "UNKNOWN"
            ).upper()
            bill["payment_method_normalized"] = method
            lead = _lead_time_for_method(method)
            bill["payment_method_risk"] = 0 <= bill["days_until_due"] <= lead

            # Approval status
            approval_raw = str(bill.get("approvalStatus") or "0")
            bill["approval_blocked"] = approval_raw not in _APPROVED_STATUSES
            bill["approval_status"] = approval_raw

            # Urgency
            adjusted = bill["days_until_due"] - lead
            bill["urgency_score"] = _urgency_from_days(adjusted)
            if bill["approval_blocked"] and bill["days_until_due"] <= 5:
                bill["urgency_score"] = min(100.0, bill["urgency_score"] + 10.0)

        # Normalize exposure
        norm_exp = _normalize(raw_exposure)
        for bill, exp in zip(bills, norm_exp):
            bill["exposure_score_raw"] = round(exp, 2)
            bill["bill_score"] = round(
                0.60 * bill["urgency_score"] + 0.40 * exp, 2
            )
            bill["priority_band"] = self.classify_priority(bill["bill_score"])

        logger.info(f"Scored {len(bills)} bills.")
        return bills

    def score_vendors(self, scored_bills: list[dict]) -> list[dict]:
        """Roll up scored bills to vendor-level priority records."""
        vendors: dict[str, dict] = {}

        for bill in scored_bills:
            vid = bill["vendor_id"]
            if vid not in vendors:
                vendors[vid] = {
                    "vendor_id": vid,
                    "vendor_name": bill["vendor_name"],
                    "bills": [],
                    "total_unpaid": 0.0,
                    "approval_blocked": False,
                }
            v = vendors[vid]
            v["bills"].append(bill)
            v["total_unpaid"] += bill["unpaid_amount"]
            if bill["approval_blocked"]:
                v["approval_blocked"] = True

        vendor_list = list(vendors.values())
        unpaid_vals = [v["total_unpaid"] for v in vendor_list]
        norm_unpaid = _normalize(unpaid_vals)

        for vendor, exp_score in zip(vendor_list, norm_unpaid):
            bills = vendor["bills"]
            gross_total = vendor["total_unpaid"]  # sum of bill-level unpaid amounts (pre-vendor-credit)

            # Subtract unapplied vendor credits from total exposure for display/exposure only.
            # IMPORTANT: urgency and concentration denominators must use gross_total so that
            # the weighted-average and ratio calculations stay bounded at 0–100.
            # Using net_total as the denominator while numerators remain gross causes both
            # scores to exceed 100 when unapplied credits are significant.
            unapplied_credit = sum(
                float(b.get("vendorUnappliedCredit") or 0) for b in bills[:1]
            )  # credit is per-vendor, stored on first bill
            vendor["unapplied_credit"] = round(unapplied_credit, 2)
            net_total = max(0.0, gross_total - unapplied_credit)
            vendor["total_unpaid"] = round(net_total, 2)

            vendor["exposure_score"] = round(exp_score, 2)

            # Urgency: amount-weighted average using gross bill amounts as weights.
            # Denominator must also be gross_total to keep the result in [0, 100].
            vendor["urgency_score"] = round(
                sum(b["urgency_score"] * b["unpaid_amount"] for b in bills) / gross_total
                if gross_total > 0
                else sum(b["urgency_score"] for b in bills) / len(bills),
                2,
            )

            # Concentration: largest overdue bill as % of gross vendor exposure.
            # Using gross_total as denominator keeps this in [0, 100].
            overdue = [b for b in bills if b["is_overdue"]]
            largest_overdue = max((b["unpaid_amount"] for b in overdue), default=0.0)
            vendor["concentration_score"] = round(
                (largest_overdue / gross_total * 100) if gross_total > 0 else 0.0, 2
            )

            # Per-vendor weight overrides — fall back to global weights if not set.
            _ov = config.VENDOR_OVERRIDES.get(vid, {})
            w_exp = _ov.get("weight_exposure",      config.WEIGHT_EXPOSURE)
            w_urg = _ov.get("weight_urgency",       config.WEIGHT_URGENCY)
            w_con = _ov.get("weight_concentration", config.WEIGHT_CONCENTRATION)
            multiplier = _ov.get("score_multiplier", 1.0)
            raw_score = round(
                min(
                    100.0,
                    (
                        w_exp * vendor["exposure_score"]
                        + w_urg * vendor["urgency_score"]
                        + w_con * vendor["concentration_score"]
                    ) * multiplier,
                ),
                2,
            )
            # Vendors in VENDOR_OVERRIDES are guaranteed at least HIGH priority.
            # The score floor ensures the displayed score always matches the band.
            # Vendors that naturally score higher (HIGH or CRITICAL) are unaffected.
            if vid in config.VENDOR_OVERRIDES:
                raw_score = max(raw_score, config.PRIORITY_HIGH_THRESHOLD)
            vendor["vendor_score"] = round(raw_score, 2)
            vendor["priority_band"] = self.classify_priority(vendor["vendor_score"])
            vendor["open_bill_count"] = len(bills)
            vendor["oldest_due_date"] = min(
                (b.get("due_date", "9999-12-31") for b in bills), default="N/A"
            )
            vendor["bill_ids"] = ", ".join(
                str(b.get("invoiceNumber") or b.get("invoice_number") or b.get("id", "")) for b in bills
            )

        vendor_list.sort(key=lambda v: v["vendor_score"], reverse=True)
        logger.info(f"Scored {len(vendor_list)} vendors.")
        return vendor_list
