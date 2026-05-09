"""
Bill.com AP Priority Tool — entry point.

Modes:
  python main.py              — one-shot run: fetch, score, push to Sheets
  python main.py --mock       — same but using mock data
  python main.py --no-sheets  — console output only
  python main.py --serve      — start the FastAPI webhook listener
  python main.py --subscribe <url>  — register a Bill.com webhook subscription
"""

import logging
import sys

import uvicorn

import config
from billcom_client import BillcomClient, BillcomAPIError, BillcomAuthError
from scoring_engine import ScoringEngine
from sheets_output import SheetsOutput
from mock_data import get_mock_bills

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def fetch_bills(use_mock: bool = False) -> list[dict]:
    if use_mock:
        logger.info("Using mock bill data.")
        return get_mock_bills()
    try:
        client = BillcomClient()
        bills = client.get_open_bills()
        if not bills:
            logger.warning("No open bills in Bill.com — falling back to mock data.")
            return get_mock_bills()
        logger.info(f"Loaded {len(bills)} live bills from Bill.com.")
        return bills
    except BillcomAuthError as e:
        logger.error(f"Auth error: {e}")
        logger.info("Falling back to mock data.")
        return get_mock_bills()
    except BillcomAPIError as e:
        logger.error(f"API error: {e}")
        logger.info("Falling back to mock data.")
        return get_mock_bills()


def print_summary(vendors: list[dict], bills: list[dict], sheet_url: str) -> None:
    band_counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for v in vendors:
        band_counts[v.get("priority_band", "LOW")] += 1

    total_exposure = sum(v.get("total_unpaid", 0) for v in vendors)
    overdue = sum(1 for b in bills if b.get("is_overdue"))
    blocked = sum(1 for b in bills if b.get("approval_blocked"))

    print("\n" + "=" * 60)
    print("  BILL.COM AP PRIORITY — RUN COMPLETE")
    print("=" * 60)
    print(f"  Vendors ranked  : {len(vendors)}")
    print(f"  Total bills     : {len(bills)}")
    print(f"  Total exposure  : ${total_exposure:,.2f}")
    print(f"  Overdue bills   : {overdue}")
    print(f"  Approval blocked: {blocked}")
    print()
    print("  Vendor priority breakdown:")
    print(f"    🔴 CRITICAL : {band_counts['CRITICAL']}")
    print(f"    🟠 HIGH     : {band_counts['HIGH']}")
    print(f"    🟡 MEDIUM   : {band_counts['MEDIUM']}")
    print(f"    🟢 LOW      : {band_counts['LOW']}")
    print()
    print("  Top 3 vendors to pay first:")
    for i, v in enumerate(vendors[:3], 1):
        print(
            f"    {i}. {v['vendor_name']} — "
            f"{v['priority_band']} (score: {v['vendor_score']}) "
            f"${v['total_unpaid']:,.2f}"
        )
    print()
    if sheet_url:
        print(f"  📊 Google Sheet: {sheet_url}")
    print("=" * 60 + "\n")


def run_once(use_mock: bool = False, skip_sheets: bool = False) -> None:
    bills = fetch_bills(use_mock=use_mock)
    engine = ScoringEngine()
    scored_bills = engine.score_bills(bills)
    vendors = engine.score_vendors(scored_bills)
    scored_bills.sort(key=lambda b: b["bill_score"], reverse=True)

    sheet_url = ""
    if not skip_sheets:
        if not config.GOOGLE_SPREADSHEET_ID:
            logger.warning("GOOGLE_SPREADSHEET_ID not set — skipping Sheets output.")
        else:
            try:
                sheets = SheetsOutput()
                sheets.update_vendor_sheet(vendors)
                sheets.update_bill_sheet(scored_bills)
                sheet_url = sheets.spreadsheet_url
            except Exception as e:
                logger.error(f"Sheets update failed: {e}")

    print_summary(vendors, scored_bills, sheet_url)


def register_webhook(notification_url: str) -> None:
    """Register a Bill.com webhook subscription and print the security key."""
    try:
        client = BillcomClient()
        result = client.create_webhook_subscription(notification_url)
        security_key = result.get("securityKey", "")
        sub_id = result.get("id", "")
        print(f"\n✅ Webhook subscription created!")
        print(f"   Subscription ID : {sub_id}")
        print(f"   Notification URL: {notification_url}")
        print(f"\n⚠️  Add this to your .env file immediately (shown once only):")
        print(f"   WEBHOOK_SECRET_KEY={security_key}\n")
    except Exception as e:
        logger.error(f"Webhook registration failed: {e}")
        sys.exit(1)


def main() -> None:
    args = sys.argv[1:]
    use_mock = "--mock" in args
    skip_sheets = "--no-sheets" in args

    if "--serve" in args:
        logger.info(f"Starting webhook listener on port {config.WEBHOOK_PORT}...")
        from webhook import app
        uvicorn.run(app, host="0.0.0.0", port=config.WEBHOOK_PORT, log_level="info")

    elif "--subscribe" in args:
        idx = args.index("--subscribe")
        if idx + 1 >= len(args):
            print("Usage: python main.py --subscribe https://your-public-url.com/webhook/billcom")
            sys.exit(1)
        register_webhook(args[idx + 1])

    else:
        run_once(use_mock=use_mock, skip_sheets=skip_sheets)


if __name__ == "__main__":
    main()
