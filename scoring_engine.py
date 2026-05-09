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

# Bill.com approvalStatus codes that mean blocked
_BLOCKED_STATUSES = {"0", "1", "unassigned", "assigned", "approving"}
_APPROVED_STATUSES = {"4", "approved"}


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

            # Amount: Bill.com v3 uses dueAmount for unpaid balance
            unpaid = float(
                bill.get("dueAmount")
                or bill.get("amountDue")
                or bill.get("amount")
                or 0
            )
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
            total = vendor["total_unpaid"]

            vendor["exposure_score"] = round(exp_score, 2)
            vendor["urgency_score"] = round(
                sum(b["urgency_score"] * b["unpaid_amount"] for b in bills) / total
                if total > 0
                else sum(b["urgency_score"] for b in bills) / len(bills),
                2,
            )
            overdue = [b for b in bills if b["is_overdue"]]
            largest_overdue = max((b["unpaid_amount"] for b in overdue), default=0.0)
            vendor["concentration_score"] = round(
                (largest_overdue / total * 100) if total > 0 else 0.0, 2
            )
            vendor["vendor_score"] = round(
                config.WEIGHT_EXPOSURE * vendor["exposure_score"]
                + config.WEIGHT_URGENCY * vendor["urgency_score"]
                + config.WEIGHT_CONCENTRATION * vendor["concentration_score"],
                2,
            )
            vendor["priority_band"] = self.classify_priority(vendor["vendor_score"])
            vendor["open_bill_count"] = len(bills)
            vendor["oldest_due_date"] = min(
                (b.get("due_date", "9999-12-31") for b in bills), default="N/A"
            )
            vendor["bill_ids"] = ", ".join(
                str(b.get("id") or b.get("bill_id", "")) for b in bills
            )

        vendor_list.sort(key=lambda v: v["vendor_score"], reverse=True)
        logger.info(f"Scored {len(vendor_list)} vendors.")
        return vendor_list
