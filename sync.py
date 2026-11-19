"""
Plaid → Google Sheets Budget Sync
Fetches transactions from all linked accounts and appends them to a Google Sheet.

Tabs:
  - Transactions : auto-populated by Plaid sync. Column H = "LOCKED" protects a row from sync.
  - Manual       : hand-entered cash transactions. Uses MANUAL_YYYYMMDD_NNN IDs.
"""

import os
import json
from datetime import date, timedelta
from pathlib import Path
from dotenv import load_dotenv

import plaid
from plaid import ApiException
from plaid.api.plaid_api import PlaidApi
from plaid.model.item_get_request import ItemGetRequest
from plaid.model.transactions_get_request import TransactionsGetRequest
from plaid.model.transactions_get_request_options import TransactionsGetRequestOptions

import gspread
from google.oauth2.service_account import Credentials


# ── Plaid setup ────────────────────────────────────────────────────────────────

ENV_MAP = {
    "sandbox":    plaid.Environment.Sandbox,
    "production": plaid.Environment.Production,
}

# ── Google Sheets setup ────────────────────────────────────────────────────────

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
CREDS_FILE = Path(__file__).parent / "google_credentials.json"

# Column indices (1-based for gspread)
COL_TRANSACTION_ID = 7   # column G
COL_LOCKED         = 8   # column H

HEADERS = ["Date", "Name", "Merchant", "Category", "Amount ($)", "Account", "Transaction ID", "Locked"]

# ── Helpers ───────────────────────────────────────────────────────────────────

def open_spreadsheet(sheet_id: str) -> gspread.Spreadsheet:
    creds = Credentials.from_service_account_file(CREDS_FILE, scopes=SCOPES)
    return gspread.authorize(creds).open_by_key(sheet_id)

def build_plaid_client() -> PlaidApi:
    env_name = os.getenv("PLAID_ENV", "sandbox").lower()
    if env_name not in ENV_MAP:
        raise SystemExit(f"Unsupported PLAID_ENV '{env_name}'. Use one of: {', '.join(ENV_MAP)}.")
    configuration = plaid.Configuration(
        host=ENV_MAP[env_name],
        api_key={
            "clientId": os.environ["PLAID_CLIENT_ID"],
            "secret":   os.environ["PLAID_SECRET"],
        },
    )
    return PlaidApi(plaid.ApiClient(configuration))

def load_access_tokens() -> list[str]:
    """Load saved Plaid access tokens from tokens.json."""
    try:
        with open(Path(__file__).parent / "tokens.json") as f:
            return json.load(f).get("access_tokens", [])
    except FileNotFoundError:
        print("No tokens.json found — run link.py first to connect your accounts.")
        return []


def fetch_transactions(plaid_client: PlaidApi, access_token: str, days_back: int = 30) -> list[dict]:
    """Fetch transactions for the last `days_back` days from Plaid."""
    start = date.today() - timedelta(days=days_back)
    end   = date.today()

    request = TransactionsGetRequest(
        access_token=access_token,
        start_date=start,
        end_date=end,
        options=TransactionsGetRequestOptions(
            count=500,
            include_personal_finance_category=True
        ),
    )
    response = plaid_client.transactions_get(request)

    # Convert to plain dict to handle date serialization on Python 3.14
    all_transactions = json.loads(json.dumps(response.to_dict(), default=str))["transactions"]
    return [t for t in all_transactions if not t["pending"]]


def transactions_to_rows(txns: list[dict]) -> list[list]:
    """Convert Plaid transaction dicts to spreadsheet rows (no Locked column — left blank)."""
    rows = []
    for t in txns:
        pfc      = t.get("personal_finance_category") or {}
        category = pfc.get("detailed") or pfc.get("primary") or ""
        rows.append([
            t["date"],
            t["name"],
            t["merchant_name"] or "",
            category,
            t["amount"],            # positive = money out, negative = money in
            t["account_id"][:8],    # abbreviated for readability
            t["transaction_id"],
            "",                     # Locked column — empty by default
        ])
    return rows


def get_sheet_index(sheet) -> dict[str, dict]:
    """
    Read the Transactions sheet and return a dict keyed by transaction_id.
    Each value contains the row number (1-based) and whether it is locked.

    Example: { "abc123": { "row": 3, "locked": True } }
    """
    all_values = sheet.get_all_values()
    index = {}
    for row_num, row in enumerate(all_values[1:], start=2):  # skip header, rows are 1-based
        if len(row) >= COL_TRANSACTION_ID:
            txn_id = row[COL_TRANSACTION_ID - 1]              # list is 0-based
            locked = len(row) >= COL_LOCKED and row[COL_LOCKED - 1].strip().upper() == "LOCKED"
            if txn_id:
                index[txn_id] = {"row": row_num, "locked": locked}
    return index


def append_new_transactions(sheet, rows: list[list], existing_index: dict[str, dict]) -> int:
    """
    Append only rows whose transaction_id is not already in the sheet.
    Rows that exist AND are LOCKED are left completely untouched.
    Rows that exist and are NOT locked are also left alone (already synced, user hasn't edited).
    """
    #TODO: make sure the integer amounts are converted to floats before uploading into the db
    new_rows = [r for r in rows if r[COL_TRANSACTION_ID - 1] not in existing_index]
    if not new_rows:
        print("  No new transactions to add.")
        return 0
    sheet.append_rows(new_rows, value_input_option="USER_ENTERED")
    print(f"  Added {len(new_rows)} new transactions.")
    return len(new_rows)


def setup_transactions_tab(spreadsheet, tab_name) -> gspread.Worksheet:
    """Get or create the Transactions tab with correct headers."""
    try:
        sheet = spreadsheet.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(tab_name, rows=5000, cols=10)
        sheet.append_row(HEADERS, value_input_option="USER_ENTERED")
        print(f"Created new tab: {tab_name}")
    return sheet


def setup_manual_tab(spreadsheet, manual_tab_name: str) -> gspread.Worksheet:
    """
    Get or create the Manual tab for hand-entered cash transactions.
    On first creation, adds headers and usage instructions.
    """
    try:
        sheet = spreadsheet.worksheet(manual_tab_name)
    except gspread.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(manual_tab_name, rows=1000, cols=10)
        sheet.append_row(HEADERS, value_input_option="USER_ENTERED")
        # Add usage instructions in row 2 as a comment row
        sheet.append_row(
            [
                "# HOW TO USE THIS TAB",
                "Enter cash or other manual transactions here.",
                "",
                "Use any category you like.",
                "Positive amount = money out. Negative = money in.",
                "Use MANUAL_ prefix for IDs.",
                f"Example ID: MANUAL_{date.today().strftime('%Y%m%d')}_001",
                "Leave Locked blank — not used for manual rows.",
            ],
            value_input_option="USER_ENTERED",
        )
        print(f"Created new tab: {manual_tab_name}")
    return sheet


def generate_manual_id(existing_ids: set[str]) -> str:
    """
    Generate a unique manual transaction ID in the format MANUAL_YYYYMMDD_NNN.
    Increments the counter until it finds one not already in the sheet.
    """
    today = date.today().strftime("%Y%m%d")
    counter = 1
    while True:
        candidate = f"MANUAL_{today}_{counter:03d}"
        if candidate not in existing_ids:
            return candidate
        counter += 1

def describe_plaid_error(e: ApiException, account_num: int, account_name: str) -> str:
    """Turn a Plaid API error into a short, actionable message."""
    try:
        body = json.loads(e.body)
    except (TypeError, ValueError):
        return f"Plaid error (HTTP {e.status}): {e.reason}"

    code = body.get("error_code")
    if code == "ITEM_LOGIN_REQUIRED":
        return f"Account {account_num} {account_name} needs to be re-linked. Run link.py, then remove the old token."
    return f"Plaid error {code}: {body.get('error_message')}"

def get_institution_name(plaid_client: PlaidApi, access_token: str, fallback: str) -> str:
    """Look up the bank's name for a token, or return the fallback if unavailable."""
    try:
        item = plaid_client.item_get(ItemGetRequest(access_token=access_token))["item"]
        return item["institution_name"] or fallback
    except Exception:
        return fallback

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    load_dotenv(dotenv_path=Path(__file__).parent / ".env")
    sheet_id        = os.environ["GOOGLE_SHEET_ID"]
    tab_name        = os.getenv("SHEET_TAB_NAME", "Transactions")
    manual_tab_name = os.getenv("MANUAL_TAB_NAME", "Manual")

    print("Opening Google Sheet…")
    spreadsheet  = open_spreadsheet(sheet_id)
    plaid_client = build_plaid_client()

    sheet        = setup_transactions_tab(spreadsheet, tab_name)
    manual_sheet = setup_manual_tab(spreadsheet, manual_tab_name)

    # Build index of existing transactions {txn_id: {row, locked}}
    existing_index = get_sheet_index(sheet)
    locked_count   = sum(1 for v in existing_index.values() if v["locked"])
    print(f"Sheet has {len(existing_index)} existing transactions ({locked_count} locked).\n")

    tokens = load_access_tokens()
    if not tokens:
        return

    total_added = 0
    for i, token in enumerate(tokens, 1):
        name = get_institution_name(plaid_client, token, fallback=f"account {i}")
        print(f"Fetching from account {name} ({i} of {len(tokens)})…")
        try:
            txns  = fetch_transactions(plaid_client, token)
            rows  = transactions_to_rows(txns)
            added = append_new_transactions(sheet, rows, existing_index)
            total_added += added
            # Update index so duplicate IDs across multiple tokens are caught
            for r in rows:
                existing_index[r[COL_TRANSACTION_ID - 1]] = {"row": -1, "locked": False}
        except ApiException as e:
            print(f"  {describe_plaid_error(e, i, name)}")
        except Exception as e:
            print(f"  Error: {e}")

    print(f"\nDone! {total_added} new transactions added to '{tab_name}'.")
    print(f"Manual entries tab '{manual_tab_name}' is ready for cash transactions.")


if __name__ == "__main__":
    main()
