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

BOOSTED_COLOR = {"red": 0.635, "green": 0.769, "blue": 0.953}  # cornflower blue

VENDOR_HEADERS = [
    "Rank", "Vendor Name", "Priority Band", "Boosted", "Total Score",
    "Exposure Score", "Urgency Score", "Concentration Score",
    "Total Unpaid ($)", "Unapplied Credits ($)", "Net Exposure ($)", "Open Bills", "Oldest Due Date",
    "Approval Blocked", "Invoice Numbers", "Vendor ID",
]

BILL_HEADERS = [
    "Rank", "Invoice #", "Vendor Name", "Priority Band", "Bill Score",
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

    def _format_vendor_columns(self, ws: gspread.Worksheet, num_rows: int) -> None:
        """Explicitly set number/currency formats on data columns.

        Runs after every write so stale column formatting from prior layouts
        can never bleed onto the wrong column (ws.clear() wipes values, not formats).

        Column map (1-based, post-Boosted-column addition):
          5-8  (E-H): score floats   → NUMBER  0.00
          9-11 (I-K): dollar amounts → CURRENCY $#,##0.00
          12   (L):   open bill count → INTEGER 0
        """
        if num_rows == 0:
            return

        def cell_range(col: int) -> dict:
            return {
                "sheetId": ws.id,
                "startRowIndex": 1,          # skip header row
                "endRowIndex": num_rows + 1,
                "startColumnIndex": col - 1,
                "endColumnIndex": col,
            }

        requests = []

        # Score columns E–H (5–8): plain number, 2 decimal places
        for col in range(5, 9):
            requests.append({
                "repeatCell": {
                    "range": cell_range(col),
                    "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "0.00"}}},
                    "fields": "userEnteredFormat.numberFormat",
                }
            })

        # Dollar columns I–K (9–11): currency
        for col in range(9, 12):
            requests.append({
                "repeatCell": {
                    "range": cell_range(col),
                    "cell": {"userEnteredFormat": {"numberFormat": {"type": "CURRENCY", "pattern": "\"$\"#,##0.00"}}},
                    "fields": "userEnteredFormat.numberFormat",
                }
            })

        # Open Bills L (12): integer
        requests.append({
            "repeatCell": {
                "range": cell_range(12),
                "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "0"}}},
                "fields": "userEnteredFormat.numberFormat",
            }
        })

        self._spreadsheet.batch_update({"requests": requests})

    def _color_boosted_column(
        self, ws: gspread.Worksheet, col_index: int, rows: list[list[Any]]
    ) -> None:
        """Highlight boosted cells cornflower blue; explicitly reset all others to plain white.

        ws.clear() wipes values but not cell formatting, so stale colors from a prior run
        survive the rewrite. Explicitly setting every data row on each run prevents ghost
        highlights from sticking to vendors that are no longer in vendor_overrides.
        """
        requests = []
        for row_num, row in enumerate(rows, start=2):
            is_boosted = row[col_index - 1] == "\u2b06 Yes"
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
                            "backgroundColor": BOOSTED_COLOR if is_boosted else {"red": 1.0, "green": 1.0, "blue": 1.0},
                            "textFormat": {"bold": is_boosted},
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor,textFormat)",
                }
            })
        if requests:
            self._spreadsheet.batch_update({"requests": requests})

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
                "\u2b06 Yes" if v.get("vendor_id", "") in config.VENDOR_OVERRIDES else "\u2014",
                v.get("vendor_score", 0),
                v.get("exposure_score", 0),
                v.get("urgency_score", 0),
                v.get("concentration_score", 0),
                round(v.get("total_unpaid", 0) + v.get("unapplied_credit", 0), 2),  # gross
                round(v.get("unapplied_credit", 0), 2),
                round(v.get("total_unpaid", 0), 2),  # net after credits
                v.get("open_bill_count", 0),
                v.get("oldest_due_date", ""),
                "Yes" if v.get("approval_blocked") else "No",
                v.get("bill_ids", ""),
                v.get("vendor_id", ""),
            ])
        ws.update([VENDOR_HEADERS] + rows, value_input_option="USER_ENTERED")
        self._apply_header_format(ws, len(VENDOR_HEADERS))
        self._format_vendor_columns(ws, num_rows=len(rows))
        self._color_priority_column(ws, col_index=3, rows=rows)
        self._color_boosted_column(ws, col_index=4, rows=rows)
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
                str(b.get("invoiceNumber") or b.get("invoice_number") or b.get("id") or ""),
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
