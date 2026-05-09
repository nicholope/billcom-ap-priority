"""
FastAPI webhook listener for Bill.com bill lifecycle events.

Receives real-time event notifications, verifies the HMAC-SHA256 signature,
re-scores open bills, and updates the Google Sheet automatically.

Supported events:
  bill.created, bill.updated, bill.archived
  payment.updated, payment.failed
"""

import hashlib
import hmac
import logging
import base64
from datetime import datetime

from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.responses import JSONResponse

import config
from billcom_client import BillcomClient, BillcomAPIError, BillcomAuthError
from scoring_engine import ScoringEngine
from sheets_output import SheetsOutput
from mock_data import get_mock_bills

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Bill.com AP Priority — Webhook Listener",
    description="Real-time bill prioritization via Bill.com webhook events",
    version="1.0.0",
)

# In-memory state for the current priority queue
# In production this would be a database; for demo purposes memory is fine
_state: dict = {
    "last_updated": None,
    "vendor_count": 0,
    "bill_count": 0,
    "last_event": None,
}


def _verify_signature(payload: bytes, signature_header: str | None) -> bool:
    """
    Verify the HMAC-SHA256 signature Bill.com sends with every webhook.
    Bill.com signs the raw request body using the subscription securityKey.
    """
    secret = config.WEBHOOK_SECRET_KEY
    if not secret or not signature_header:
        logger.warning("Webhook signature verification skipped — no secret configured.")
        return True  # Allow through in dev; enforce in production

    expected = base64.b64encode(
        hmac.new(secret.encode(), payload, hashlib.sha256).digest()
    ).decode()

    return hmac.compare_digest(expected, signature_header)


def _run_priority_update(trigger: str = "webhook") -> dict:
    """
    Fetch open bills, score them, and push to Google Sheet.
    Returns a summary dict.
    """
    logger.info(f"Priority update triggered by: {trigger}")

    # Fetch live bills
    try:
        client = BillcomClient()
        bills = client.get_open_bills()
        if not bills:
            logger.warning("No open bills found — using mock data.")
            bills = get_mock_bills()
            source = "mock"
        else:
            source = "live"
    except (BillcomAuthError, BillcomAPIError) as e:
        logger.error(f"Bill.com API error: {e} — falling back to mock.")
        bills = get_mock_bills()
        source = "mock"

    # Score
    engine = ScoringEngine()
    scored_bills = engine.score_bills(bills)
    vendors = engine.score_vendors(scored_bills)
    scored_bills.sort(key=lambda b: b["bill_score"], reverse=True)

    # Push to Sheets
    sheet_url = ""
    if config.GOOGLE_SPREADSHEET_ID and config.GOOGLE_CREDENTIALS_FILE:
        try:
            sheets = SheetsOutput()
            sheets.update_vendor_sheet(vendors)
            sheets.update_bill_sheet(scored_bills)
            sheet_url = sheets.spreadsheet_url
        except Exception as e:
            logger.error(f"Sheets update failed: {e}")

    summary = {
        "trigger": trigger,
        "source": source,
        "vendors_ranked": len(vendors),
        "bills_scored": len(scored_bills),
        "overdue": sum(1 for b in scored_bills if b.get("is_overdue")),
        "approval_blocked": sum(1 for b in scored_bills if b.get("approval_blocked")),
        "sheet_url": sheet_url,
        "updated_at": datetime.now().isoformat(),
    }

    _state.update({
        "last_updated": summary["updated_at"],
        "vendor_count": summary["vendors_ranked"],
        "bill_count": summary["bills_scored"],
        "last_event": trigger,
    })

    logger.info(
        f"Update complete — {summary['vendors_ranked']} vendors, "
        f"{summary['bills_scored']} bills, {summary['overdue']} overdue."
    )
    return summary


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------

@app.get("/health")
async def health() -> JSONResponse:
    """Health check — confirms the listener is running."""
    return JSONResponse({"status": "ok", "state": _state})


@app.post("/webhook/billcom")
async def receive_webhook(
    request: Request,
    x_bill_signature: str | None = Header(default=None, alias="X-Bill-Signature"),
) -> JSONResponse:
    """
    Receive and process Bill.com webhook event notifications.

    Bill.com sends a POST with:
      - Body: JSON event payload
      - Header: X-Bill-Signature (HMAC-SHA256 base64, signed with securityKey)
    """
    raw_body = await request.body()

    # Verify signature
    if not _verify_signature(raw_body, x_bill_signature):
        logger.warning("Webhook signature mismatch — rejecting request.")
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event_type = payload.get("eventType") or payload.get("type", "unknown")
    entity_id = payload.get("data", {}).get("id", "")

    logger.info(f"Received event: {event_type} | entity: {entity_id}")

    # Events that should trigger a priority re-score
    TRIGGER_EVENTS = {
        "bill.created",
        "bill.updated",
        "bill.archived",
        "payment.updated",
        "payment.failed",
    }

    if event_type in TRIGGER_EVENTS:
        summary = _run_priority_update(trigger=event_type)
        return JSONResponse({"received": True, "processed": True, "summary": summary})

    # Acknowledge non-triggering events without re-scoring
    return JSONResponse({"received": True, "processed": False, "event": event_type})


@app.post("/refresh")
async def manual_refresh() -> JSONResponse:
    """
    Manually trigger a full priority queue refresh.
    Useful for scheduled runs or testing without a webhook event.
    """
    summary = _run_priority_update(trigger="manual")
    return JSONResponse(summary)
