# BILL.com AP Priority

A Python-based accounts payable prioritization engine that connects to the [BILL API](https://developer.bill.com), scores open bills by payment urgency, and publishes a live ranked priority queue to Google Sheets — giving finance teams a single daily view of what to approve and pay first.

Built as a portfolio project demonstrating practical finance automation with live API integrations, explainable scoring models, real-time webhook processing, and automated reporting.

---

## The Problem

AP teams managing dozens of open bills across multiple vendors have no consistent way to rank payment urgency. Sorting by due date alone misses large-dollar exposure. Simple averages understate risk when one vendor has a large overdue invoice buried under many small current ones. Vendor credits sitting unapplied inflate the apparent balance.

This tool solves that with a weighted, explainable priority model that accounts for all of it.

---

## How It Works

1. **Authenticates** with the BILL API using API Sync Token (no password stored)
2. **Pulls open bills** (unpaid + partial) from 2025 onward via the BILL v2 List API
3. **Fetches vendor records** to enrich bills with preferred payment method
4. **Fetches vendor credits** and subtracts unapplied balances from net exposure
5. **Scores each bill** on an urgency curve based on due date, payment method lead time, and approval status
6. **Rolls up to vendor level** using a weighted composite score
7. **Assigns priority bands** — 🔴 CRITICAL / 🟠 HIGH / 🟡 MEDIUM / 🟢 LOW
8. **Writes to Google Sheets** with color-coded formatting, frozen headers, and a run timestamp
9. **Listens for webhooks** (FastAPI) to re-score in real time when bills change in BILL

---

## Scoring Model

### Bill-Level Urgency (0–100)

| Days Until Due | Urgency Score |
|---|---|
| Overdue | 100 |
| Due today | 95 |
| 1–2 days | 85 |
| 3–5 days | 70 |
| 6–10 days | 50 |
| 11–20 days | 30 |
| 20+ days | 10 |

Payment method lead times shift urgency earlier — ACH = 3 days, Wire = 1 day, Check = 5 days — so a check due in 4 days scores as urgently as an ACH due tomorrow.

### Vendor-Level Score

```
vendor_score = 0.50 × exposure + 0.35 × urgency + 0.15 × concentration
```

| Component | Definition |
|---|---|
| **Exposure** | Normalized net unpaid (after credits) across all open bills |
| **Urgency** | Amount-weighted average bill urgency — avoids underweighting large late invoices |
| **Concentration** | Largest overdue bill as % of total vendor exposure |

### Vendor Credits

Unapplied vendor credit balances are fetched from the BILL `VendorCredit` entity and subtracted from each vendor's gross exposure before scoring. The sheet shows gross unpaid, unapplied credits, and net exposure separately for full transparency.

---

## Output

**Console summary on each run:**
```
============================================================
  BILL.COM AP PRIORITY — RUN COMPLETE
============================================================
  Vendors ranked  : 49
  Total bills     : 185
  Total exposure  : $2,880,998.38
  Overdue bills   : 150
  Approval blocked: 4

  Vendor priority breakdown:
    🔴 CRITICAL : 1
    🟠 HIGH     : 1
    🟡 MEDIUM   : 32
    🟢 LOW      : 15

  Top 3 vendors to pay first:
    1. Vendor A — CRITICAL (score: 86.0) $1,300,525.89
    2. Vendor B — HIGH (score: 76.56) $1,055,466.22
    3. Vendor C — MEDIUM (score: 52.84) $75,150.78

  📊 Google Sheet: https://docs.google.com/spreadsheets/d/...
============================================================
```

**Google Sheet (auto-updated on each run or webhook event):**

| Tab | Contents |
|---|---|
| **Vendor Priority** | Ranked vendors with composite score, gross unpaid, unapplied credits, net exposure, approval flag |
| **Bill Queue** | Every open bill ranked by urgency with invoice number, due date, days until due, payment method, blocker flags |

Priority band cells are color-coded (red → orange → yellow → green) for at-a-glance review.

---

## Setup

### Requirements
- Python 3.11+
- BILL account with API access (AP module)
- Google Cloud project with Sheets + Drive APIs enabled

### 1. Clone and install

```bash
git clone https://github.com/nicholope/billcom-ap-priority.git
cd billcom-ap-priority
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. BILL API credentials

1. Log in to BILL → **Settings → Developer → Manage Developer Keys** → generate a devKey
2. Go to **Settings → Tokens → Create New Token** → copy the API Sync Token
3. Find your **Organization ID** in Settings → Organization

### 3. Google Sheets setup

1. Go to [Google Cloud Console](https://console.cloud.google.com) → create a project
2. Enable **Google Sheets API** and **Google Drive API**
3. Create a **Service Account** under IAM → download the JSON key → save as `credentials.json`
4. Create a blank Google Sheet → copy the ID from the URL
5. Share the sheet with the service account email (`client_email` in the JSON) as **Editor**

### 4. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:
```env
BILLCOM_DEV_KEY=your_dev_key
BILLCOM_API_TOKEN=your_api_sync_token
BILLCOM_USERNAME=your_bill_login_email
BILLCOM_ORG_ID=your_organization_id
BILLCOM_ENVIRONMENT=production   # or sandbox

GOOGLE_CREDENTIALS_FILE=credentials.json
GOOGLE_SPREADSHEET_ID=your_sheet_id

WEBHOOK_SECRET_KEY=              # populated after webhook registration
```

---

## Usage

```bash
# Live BILL data → Google Sheet
python main.py

# Demo mode with mock data (no BILL API required)
python main.py --mock

# Console output only, skip Sheets
python main.py --mock --no-sheets

# Start the FastAPI webhook listener
python main.py --serve

# Register a BILL webhook subscription
python main.py --subscribe https://your-public-url.com/webhook/billcom
```

---

## Webhook Setup

The webhook listener (FastAPI) receives real-time bill lifecycle events from BILL and automatically re-scores and updates the Google Sheet.

**Supported events:** `bill.created`, `bill.updated`, `bill.archived`, `payment.updated`, `payment.failed`

### Local testing with ngrok

```bash
# Terminal 1 — start the listener
python main.py --serve

# Terminal 2 — expose it publicly
ngrok http 8000

# Terminal 3 — register the subscription (use the ngrok URL)
python main.py --subscribe https://abc123.ngrok.io/webhook/billcom
```

After registration, copy the `securityKey` from the output into your `.env` as `WEBHOOK_SECRET_KEY`. BILL will then POST signed event notifications to your endpoint whenever a bill changes.

### Production deployment

Deploy to any cloud provider (Railway, Render, Fly.io) and register your permanent URL as the webhook endpoint. The app verifies each webhook's HMAC-SHA256 signature before processing.

---

## Project Structure

```
billcom-ap-priority/
├── main.py              # Orchestrator — fetch, score, output, serve
├── billcom_client.py    # BILL API client (v2 + v3, session auth, credits)
├── scoring_engine.py    # Bill-level and vendor-level scoring logic
├── sheets_output.py     # Google Sheets writer with formatting
├── webhook.py           # FastAPI webhook listener
├── mock_data.py         # Realistic demo data
├── config.py            # Environment config and scoring constants
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## Configurable Parameters

All scoring weights and thresholds are in `config.py`:

| Parameter | Default | Description |
|---|---|---|
| `WEIGHT_EXPOSURE` | 0.50 | Vendor exposure weight |
| `WEIGHT_URGENCY` | 0.35 | Urgency weight |
| `WEIGHT_CONCENTRATION` | 0.15 | Concentration weight |
| `PRIORITY_CRITICAL_THRESHOLD` | 80 | Minimum score for CRITICAL band |
| `PRIORITY_HIGH_THRESHOLD` | 60 | Minimum score for HIGH band |
| `PRIORITY_MEDIUM_THRESHOLD` | 40 | Minimum score for MEDIUM band |

---

## Security

- All credentials are stored in `.env` — excluded from version control via `.gitignore`
- API Sync Token used instead of password — can be revoked independently
- Webhook payloads verified with HMAC-SHA256 signature before processing
- Read-only BILL integration — the tool never creates, modifies, or pays bills
- All payment decisions remain human-controlled

---

## Roadmap

- [ ] Scheduled daily refresh (cron / cloud scheduler)
- [ ] Slack / email daily digest
- [ ] Claude-generated natural language summaries ("Why is this vendor critical?")
- [ ] Cash-constrained payment optimizer
- [ ] Historical score tracking and trend visualization
- [ ] Multi-entity / multi-org support

---

*Built by [Nick Lopez](https://github.com/nicholope) as a finance automation portfolio project.*
