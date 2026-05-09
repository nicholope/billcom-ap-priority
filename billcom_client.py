"""
Bill.com API client — session-based auth using API Sync Token + devKey.

Auth flow:
  POST /login with devKey, orgId, and API token (replaces username/password)
  → returns sessionId used for all subsequent requests
"""

import logging
import uuid
from typing import Optional

import requests

import config

logger = logging.getLogger(__name__)


class BillcomAuthError(Exception):
    """Raised when Bill.com authentication fails."""


class BillcomAPIError(Exception):
    """Raised when a Bill.com API call returns an error."""


class BillcomClient:
    """Client for the Bill.com v3 AP API."""

    def __init__(self) -> None:
        self._session_id: Optional[str] = None
        self._http = requests.Session()

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def login(self) -> str:
        """
        Authenticate with Bill.com using API Sync Token flow.
        Returns the sessionId for subsequent requests.
        """
        if not all([config.BILLCOM_DEV_KEY, config.BILLCOM_API_TOKEN,
                     config.BILLCOM_USERNAME, config.BILLCOM_ORG_ID]):
            raise BillcomAuthError(
                "BILLCOM_DEV_KEY, BILLCOM_API_TOKEN, BILLCOM_USERNAME, and "
                "BILLCOM_ORG_ID must all be set in .env"
            )

        url = f"{config.BILLCOM_BASE_URL}/login"
        headers = {"Content-Type": "application/json"}
        payload = {
            "devKey": config.BILLCOM_DEV_KEY,
            "organizationId": config.BILLCOM_ORG_ID,
            "username": config.BILLCOM_USERNAME,
            # API Sync Token replaces password for automation
            "password": config.BILLCOM_API_TOKEN,
        }

        resp = self._http.post(url, json=payload, headers=headers, timeout=30)

        if resp.status_code != 200:
            raise BillcomAuthError(
                f"Login failed [{resp.status_code}]: {resp.text}"
            )

        data = resp.json()
        session_id = data.get("sessionId") or (data.get("data") or {}).get("sessionId")

        if not session_id:
            raise BillcomAuthError(f"No sessionId in login response: {data}")

        self._session_id = session_id
        logger.info("Bill.com session established.")
        return session_id

    def _headers(self) -> dict:
        """Return auth headers for API calls."""
        if not self._session_id:
            self.login()
        return {
            "devKey": config.BILLCOM_DEV_KEY,
            "sessionId": self._session_id,
            "Content-Type": "application/json",
        }

    def _get(self, path: str, params: dict | None = None) -> dict:
        """Make an authenticated GET request, refreshing session on 401."""
        url = f"{config.BILLCOM_BASE_URL}{path}"
        resp = self._http.get(url, headers=self._headers(), params=params, timeout=30)

        if resp.status_code == 401:
            logger.warning("Session expired — re-authenticating.")
            self.login()
            resp = self._http.get(url, headers=self._headers(), params=params, timeout=30)

        if resp.status_code != 200:
            raise BillcomAPIError(f"GET {path} failed [{resp.status_code}]: {resp.text}")

        return resp.json()

    # ------------------------------------------------------------------
    # Bills
    # ------------------------------------------------------------------

    def get_open_bills(self, max_pages: int = 20) -> list[dict]:
        """
        Fetch all open (unpaid/partial) bills using the Bill.com v2 List API.

        The v2 API supports server-side filtering by paymentStatus:
          1 = Unpaid (open, nothing paid yet)
          2 = Partial (partially paid, dueAmount > 0)
        Sorted by dueDate ascending so most urgent bills come first.
        """
        import json as _json

        if not self._session_id:
            self.login()

        V2_URL = "https://api.bill.com/api/v2/List/Bill.json"
        all_bills: list[dict] = []

        for status_val in ["1", "2"]:  # 1=Unpaid, 2=Partial
            start = 0
            per_page = 100
            page_num = 0

            for _ in range(max_pages):
                payload = {
                    "devKey": config.BILLCOM_DEV_KEY,
                    "sessionId": self._session_id or "",
                    "data": _json.dumps({
                        "filters": [
                            {"field": "paymentStatus", "op": "=", "value": status_val},
                            {"field": "isActive", "op": "=", "value": "1"},
                            {"field": "dueDate", "op": ">=", "value": "2025-01-01"},
                        ],
                        "start": start,
                        "max": per_page,
                        "sort": [{"field": "dueDate", "asc": True}],
                    }),
                }
                resp = self._http.post(V2_URL, data=payload, timeout=30)

                if resp.status_code == 401:
                    logger.warning("Session expired — re-authenticating.")
                    self.login()
                    payload["sessionId"] = self._session_id
                    resp = self._http.post(V2_URL, data=payload, timeout=30)

                if resp.status_code != 200:
                    raise BillcomAPIError(
                        f"v2 List/Bill failed [{resp.status_code}]: {resp.text}"
                    )

                body = resp.json()
                if body.get("response_status") != 0:
                    raise BillcomAPIError(
                        f"v2 List/Bill error: {body.get('response_data', {}).get('error_message')}"
                    )

                bills = body.get("response_data", [])
                page_num += 1

                # Keep only bills with actual outstanding balance
                open_bills = [
                    b for b in bills
                    if float(b.get("dueAmount") or 0) > 0
                    and b.get("isActive") == "1"
                ]
                all_bills.extend(open_bills)
                logger.info(
                    f"status={status_val} page={page_num}: {len(bills)} fetched, "
                    f"{len(open_bills)} with balance (total: {len(all_bills)})"
                )

                if len(bills) < per_page:
                    break
                start += per_page

        # Enrich with vendor names, payment method, and unapplied credits
        if all_bills:
            vendor_map = self._get_vendor_map()
            credit_map = self._get_vendor_credit_map()
            for bill in all_bills:
                vid = bill.get("vendorId", "")
                vendor_info = vendor_map.get(vid, {"name": "Unknown Vendor", "payment_method": "UNKNOWN"})
                bill["vendorName"] = vendor_info["name"]
                bill["paymentMethodType"] = vendor_info["payment_method"]
                bill["vendorUnappliedCredit"] = credit_map.get(vid, 0.0)

        logger.info(f"Total open bills with balance: {len(all_bills)}")
        return all_bills

    def _get_vendor_map(self) -> dict[str, str]:
        """Return a dict of {vendorId: vendorName} using the v2 List API."""
        import json as _json

        V2_URL = "https://api.bill.com/api/v2/List/Vendor.json"
        vendor_map: dict[str, str] = {}
        start = 0

        while True:
            payload = {
                "devKey": config.BILLCOM_DEV_KEY,
                "sessionId": self._session_id,
                "data": _json.dumps({"start": start, "max": 100}),
            }
            resp = self._http.post(V2_URL, data=payload, timeout=30)
            vendors = resp.json().get("response_data", [])
            if not vendors:
                break
            for v in vendors:
                vid = v["id"]
                pref = str(v.get("prefPmtMethod") or "0")
                vendor_map[vid] = {
                    "name": v.get("name", "Unknown Vendor"),
                    "payment_method": self._PREF_PMT_METHOD_MAP.get(pref, "UNKNOWN"),
                }
            if len(vendors) < 100:
                break
            start += 100

        logger.info(f"Loaded {len(vendor_map)} vendors.")
        return vendor_map

    def _get_vendor_credit_map(self) -> dict[str, float]:
        """Return {vendorId: unapplied_credit_amount} from VendorCredit records."""
        import json as _json

        V2_URL = "https://api.bill.com/api/v2/List/VendorCredit.json"
        credit_map: dict[str, float] = {}
        start = 0

        while True:
            payload = {
                "devKey": config.BILLCOM_DEV_KEY,
                "sessionId": self._session_id,
                "data": _json.dumps({
                    "filters": [{"field": "isActive", "op": "=", "value": "1"}],
                    "start": start,
                    "max": 100,
                }),
            }
            resp = self._http.post(V2_URL, data=payload, timeout=30)
            credits = resp.json().get("response_data", [])
            if not credits:
                break
            for vc in credits:
                vid = vc.get("vendorId", "")
                total = float(vc.get("amount") or 0)
                applied = float(vc.get("appliedAmount") or 0)
                unapplied = max(0.0, total - applied)
                if unapplied > 0:
                    credit_map[vid] = credit_map.get(vid, 0.0) + unapplied
            if len(credits) < 100:
                break
            start += 100

        total_credits = sum(credit_map.values())
        logger.info(f"Loaded vendor credits for {len(credit_map)} vendors (${total_credits:,.2f} unapplied).")
        return credit_map

    # Bill.com prefPmtMethod codes → normalized payment method string
    _PREF_PMT_METHOD_MAP: dict[str, str] = {
        "0": "UNKNOWN",
        "1": "CHECK",
        "2": "ACH",
        "3": "CARD",
        "4": "CARD",   # PayPal/virtual card
        "5": "WIRE",
        "6": "WIRE",
        "7": "ACH",   # ePayment / ACH network
        "8": "WIRE",
    }

    def get_bill(self, bill_id: str) -> dict:
        """Fetch a single bill by ID."""
        data = self._get(f"/bills/{bill_id}")
        return data.get("data") or data

    def get_vendors(self) -> list[dict]:
        """Fetch all vendors for name enrichment."""
        all_vendors: list[dict] = []
        page = 1
        while True:
            data = self._get("/vendors", params={"page": page, "per_page": 100})
            vendors = data if isinstance(data, list) else (data.get("data") or [])
            if not vendors:
                break
            all_vendors.extend(vendors)
            if len(vendors) < 100:
                break
            page += 1
        return all_vendors

    # ------------------------------------------------------------------
    # Webhook subscription management
    # ------------------------------------------------------------------

    def create_webhook_subscription(self, notification_url: str) -> dict:
        """
        Register a webhook subscription for bill and payment lifecycle events.
        Returns the subscription object including the one-time securityKey.
        """
        url = f"{config.BILLCOM_EVENTS_BASE_URL}/subscriptions"
        payload = {
            "name": "AP Priority Queue — Bill Lifecycle",
            "status": {"enabled": True},
            "events": [
                {"type": "bill.created",  "version": "1"},
                {"type": "bill.updated",  "version": "1"},
                {"type": "bill.archived", "version": "1"},
                {"type": "payment.updated", "version": "1"},
                {"type": "payment.failed",  "version": "1"},
            ],
            "notificationUrl": notification_url,
        }
        headers = {
            **self._headers(),
            "X-Idempotent-Key": str(uuid.uuid4()),
        }
        resp = self._http.post(url, json=payload, headers=headers, timeout=30)

        if resp.status_code not in (200, 201):
            raise BillcomAPIError(
                f"Webhook subscription failed [{resp.status_code}]: {resp.text}"
            )

        result = resp.json()
        logger.info(f"Webhook subscription created: {result.get('id')}")
        return result
