"""
Configuration and constants for the Bill.com AP Priority Tool.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# --- Bill.com API ---
BILLCOM_DEV_KEY: str = os.getenv("BILLCOM_DEV_KEY", "")
BILLCOM_API_TOKEN: str = os.getenv("BILLCOM_API_TOKEN", "")
BILLCOM_USERNAME: str = os.getenv("BILLCOM_USERNAME", "")
BILLCOM_ORG_ID: str = os.getenv("BILLCOM_ORG_ID", "")
BILLCOM_ENVIRONMENT: str = os.getenv("BILLCOM_ENVIRONMENT", "production")

BILLCOM_BASE_URLS = {
    "production": "https://gateway.prod.bill.com/connect/v3",
    "sandbox":    "https://gateway.stage.bill.com/connect/v3",
}
BILLCOM_BASE_URL: str = BILLCOM_BASE_URLS.get(
    BILLCOM_ENVIRONMENT, BILLCOM_BASE_URLS["production"]
)

BILLCOM_EVENTS_BASE_URLS = {
    "production": "https://gateway.prod.bill.com/connect-events/v3",
    "sandbox":    "https://gateway.stage.bill.com/connect-events/v3",
}
BILLCOM_EVENTS_BASE_URL: str = BILLCOM_EVENTS_BASE_URLS.get(
    BILLCOM_ENVIRONMENT, BILLCOM_EVENTS_BASE_URLS["production"]
)

# --- Webhook ---
WEBHOOK_SECRET_KEY: str = os.getenv("WEBHOOK_SECRET_KEY", "")
WEBHOOK_PORT: int = int(os.getenv("WEBHOOK_PORT", "8000"))

# --- Google Sheets ---
GOOGLE_CREDENTIALS_FILE: str = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
GOOGLE_SPREADSHEET_ID: str = os.getenv("GOOGLE_SPREADSHEET_ID", "")

# --- Scoring Weights ---
WEIGHT_EXPOSURE: float = 0.50
WEIGHT_URGENCY: float = 0.35
WEIGHT_CONCENTRATION: float = 0.15

# --- Priority Bands ---
PRIORITY_CRITICAL_THRESHOLD: float = 80.0
PRIORITY_HIGH_THRESHOLD: float = 60.0
PRIORITY_MEDIUM_THRESHOLD: float = 40.0

# --- Payment Method Lead Times (business days) ---
PAYMENT_LEAD_TIMES: dict[str, int] = {
    "ACH": 3,
    "WIRE": 1,
    "CHECK": 5,
    "CARD": 0,
    "UNKNOWN": 2,
}
