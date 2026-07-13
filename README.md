# Plaid → Google Sheets Budget Sync

Automatically fetch bank/credit card transactions via Plaid and append them
to a Google Sheet — with duplicate detection so you can run it on a schedule safely.

---

## Setup

*We reccomend using a virtual environment
```bash
python -m venv venv
```

#### Mac/Linux
source venv/bin/activate

#### Windows
venv\Scripts\activate

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Plaid
1. Sign up at https://dashboard.plaid.com (free)
2. Create an app → copy your **Client ID** and **Secret** (Sandbox first)
3. Paste them into `.env`

### 3. Configure Google Sheets API
1. Go to https://console.cloud.google.com → create a project
2. Enable **Google Sheets API** and **Google Drive API**
3. Go to Credentials → Create Credentials → **Service Account**
4. Download the JSON key → save as `google_credentials.json` in this folder
5. Open your Google Sheet → Share → paste the service account email → give Editor access
6. Copy the Sheet ID from the URL and paste into `.env`

### 4. Configure your ENV
PLAID_CLIENT_ID=your_client_id
PLAID_SECRET=your_secret
PLAID_ENV=sandbox
GOOGLE_SHEET_ID=your_sheet_id
SHEET_TAB_NAME=Transactions
MANUAL_TAB_NAME=Manual

### 5. Link your bank accounts
Run this once per bank or credit card:
```bash
python link.py
```
A browser will open with Plaid's secure login UI. Your bank credentials
never touch this script — Plaid handles all authentication.

Tokens are saved to `tokens.json`. Keep this file private (it's in .gitignore).

### 6. Sync transactions
```bash
python sync.py
```
This fetches the last 30 days of transactions and appends any new ones to your sheet.
Duplicate transactions are automatically skipped.

---

## Automate with a schedule

### macOS / Linux (cron)
Run `crontab -e` and add:
```
# Sync every morning at 7 AM
0 7 * * * cd /path/to/plaid-budget && python sync.py >> sync.log 2>&1
```

### Windows (Task Scheduler)
Create a task that runs `python sync.py` daily.

---

## Sheet columns
| Date | Name | Merchant | Category | Amount ($) | Account | Transaction ID |

- **Amount**: positive = money out, negative = money in (Plaid convention)
- **Transaction ID**: used to detect duplicates; don't edit this column

---

## Moving to production (real accounts)
1. In the Plaid dashboard, request **Development** access (free, up to 100 real items)
2. Change `PLAID_ENV=development` in `.env`
3. Re-run `link.py` to re-link accounts in the new environment
4. For wider use, apply for **Production** access

---

## Security notes
- `tokens.json` and `google_credentials.json` are listed in `.gitignore`
- Never commit these files or share them
- Plaid tokens are read-only by default — they cannot move money
- All credentials stay on your local machine

## Running Tests
pytest tests/ -v
