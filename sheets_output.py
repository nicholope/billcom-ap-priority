"""
Google Sheets output — writes ranked vendor and bill data to a spreadsheet.
Reuses the same service account from the Ramp project.
"""

import logging
from datetime import datetime
from typing import Any

import gspread
from google.oauth2.service_account import Credentials

import config

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

PRIORITY_COLORS = {
    "CRITICAL": {"red": 1.0,   "green": 0.267, "blue": 0.267},
    "HIGH":     {"red": 1.0,   "green": 0.549, "blue": 0.0},
    "MEDIUM":   {"red": 1.0,   "green": 0.843, "blue": 0.0},
    "LOW":      {"red": 0.565, "green": 0.933, "blue": 0.565},
}

VENDOR_HEADERS = [
    "Rank", "Vendor Name", "Priority Band", "Total Score",
    "Exposure Score", "Urgency Score", "Concentration Score",
    "Total Unpaid ($)", "Open Bills", "Oldest Due Date",
    "Approval Blocked", "Bill IDs",
]

BILL_HEADERS = [
    "Rank", "Bill ID", "Vendor Name", "Priority Band", "Bill Score",
    "Due Date", "Days Until Due", "Unpaid Amount ($)", "Payment Method",
    "Approval Status", "Approval Blocked", "Overdue", "Notes",
]


class SheetsOutput:
    """Writes priority data to a Google Spreadsheet."""

    def __init__(self) -> None:
        creds = Credentials.from_service_account_file(
            config.GOOGLE_CREDENTIALS_FILE, scopes=SCOPES
        )
        self._gc = gspread.authorize(creds)
        self._spreadsheet = self._gc.open_by_key(config.GOOGLE_SPREADSHEET_ID)
        self.spreadsheet_url = (
            f"https://docs.google.com/spreadsheets/d/{config.GOOGLE_SPREADSHEET_ID}"
        )

    def _get_or_create_sheet(self, title: str) -> gspread.Worksheet:
        try:
            return self._spreadsheet.worksheet(title)
        except gspread.WorksheetNotFound:
            return self._spreadsheet.add_worksheet(title=title, rows=500, cols=20)

    def _apply_header_format(self, ws: gspread.Worksheet, num_cols: int) -> None:
        ws.freeze(rows=1)
        ws.format(
            f"A1:{chr(64 + num_cols)}1",
            {
                "textFormat": {
                    "bold": True,
                    "foregroundColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                },
                "backgroundColor": {"red": 0.22, "green": 0.22, "blue": 0.22},
                "horizontalAlignment": "CENTER",
            },
        )

    def _color_priority_column(
        self, ws: gspread.Worksheet, col_index: int, rows: list[list[Any]]
    ) -> None:
        requests = []
        for row_num, row in enumerate(rows, start=2):
            band = row[col_index - 1]
            color = PRIORITY_COLORS.get(band, {"red": 1.0, "green": 1.0, "blue": 1.0})
            requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": ws.id,
                        "startRowIndex": row_num - 1,
                        "endRowIndex": row_num,
                        "startColumnIndex": col_index - 1,
                        "endColumnIndex": col_index,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": color,
                            "textFormat": {"bold": True},
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat)",
                }
            })
        if requests:
            self._spreadsheet.batch_update({"requests": requests})

    def update_vendor_sheet(self, vendors: list[dict]) -> None:
        ws = self._get_or_create_sheet("Vendor Priority")
        ws.clear()
        rows: list[list[Any]] = []
        for rank, v in enumerate(vendors, start=1):
            rows.append([
                rank,
                v.get("vendor_name", ""),
                v.get("priority_band", ""),
                v.get("vendor_score", 0),
                v.get("exposure_score", 0),
                v.get("urgency_score", 0),
                v.get("concentration_score", 0),
                round(v.get("total_unpaid", 0), 2),
                v.get("open_bill_count", 0),
                v.get("oldest_due_date", ""),
                "Yes" if v.get("approval_blocked") else "No",
                v.get("bill_ids", ""),
            ])
        ws.update([VENDOR_HEADERS] + rows, value_input_option="USER_ENTERED")
        self._apply_header_format(ws, len(VENDOR_HEADERS))
        self._color_priority_column(ws, col_index=3, rows=rows)
        ws.update_cell(len(rows) + 3, 1, f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"Vendor Priority updated — {len(vendors)} vendors.")

    def update_bill_sheet(self, bills: list[dict]) -> None:
        ws = self._get_or_create_sheet("Bill Queue")
        ws.clear()
        rows: list[list[Any]] = []
        for rank, b in enumerate(bills, start=1):
            notes = []
            if b.get("is_overdue"):          notes.append("OVERDUE")
            if b.get("approval_blocked"):    notes.append("Needs approval")
            if b.get("payment_method_risk"): notes.append("Payment lead-time risk")
            rows.append([
                rank,
                str(b.get("id") or ""),
                b.get("vendor_name", ""),
                b.get("priority_band", ""),
                b.get("bill_score", 0),
                b.get("due_date", ""),
                b.get("days_until_due", ""),
                round(b.get("unpaid_amount", 0), 2),
                b.get("payment_method_normalized", ""),
                b.get("approval_status", ""),
                "Yes" if b.get("approval_blocked") else "No",
                "Yes" if b.get("is_overdue") else "No",
                " | ".join(notes),
            ])
        ws.update([BILL_HEADERS] + rows, value_input_option="USER_ENTERED")
        self._apply_header_format(ws, len(BILL_HEADERS))
        self._color_priority_column(ws, col_index=4, rows=rows)
        ws.update_cell(len(rows) + 3, 1, f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"Bill Queue updated — {len(bills)} bills.")
