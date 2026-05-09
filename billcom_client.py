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
        Fetch all open (unpaid) bills from Bill.com.

        Bill.com v3 uses cursor-based pagination via nextPage/prevPage tokens.
        Filters client-side for unpaid bills.
        """
        PAID_STATUSES = {"1", "paid"}

        all_bills: list[dict] = []
        next_cursor: str | None = None
        page_num = 0

        for _ in range(max_pages):
            params: dict = {"limit": 100}
            if next_cursor:
                params["page"] = next_cursor

            data = self._get("/bills", params=params)
            # Bill.com v3 returns results in 'results' key
            bills = (
                data if isinstance(data, list)
                else (data.get("results") or data.get("data") or [])
            )

            if not bills:
                break

            page_num += 1
            open_bills = [
                b for b in bills
                if b.get("paymentStatus", "").upper() not in PAID_STATUSES
                and float(b.get("dueAmount") or 0) > 0
                and not b.get("archived", False)
            ]
            all_bills.extend(open_bills)
            logger.info(
                f"Page {page_num}: {len(bills)} fetched, {len(open_bills)} open "
                f"(total: {len(all_bills)})"
            )

            # Bill.com v3 cursor-based pagination via 'nextPage'
            next_cursor = data.get("nextPage") if isinstance(data, dict) else None
            if not next_cursor or len(bills) < 100:
                break

        logger.info(f"Total open bills: {len(all_bills)}")
        return all_bills

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
