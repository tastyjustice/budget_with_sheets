import sqlite3
from decimal import Decimal
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).parent / "budget.db"

SCHEMA = """
         CREATE TABLE IF NOT EXISTS transactions (
            transaction_id TEXT PRIMARY KEY NOT NULL,
            date TEXT NOT NULL, 
            merchant_name TEXT, 
            amount_cents INTEGER NOT NULL,
            account_id TEXT NOT NULL, 
            plaid_primary TEXT,
            plaid_detailed TEXT,
            category TEXT,
            name TEXT NOT NULL
         );
         """

def connect(path=DEFAULT_DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn

def dollars_to_cents(amount: float) -> int:
    return int(round(Decimal(str(amount)) * 100))

def cents_to_dollars(cents: int) -> Decimal:
    return Decimal(cents) / 100

def to_db_row(t: dict) -> dict:
    #This is needed because dict keys must match the placeholder names exactly for sql upsert, and Plaid’s dicts
    # don’t match. the category is nested, and there’s no plaid_category key
    pfc = t.get("personal_finance_category") or {}
    return {
        "transaction_id": t["transaction_id"],
        "date":           t["date"],
        "name":           t["name"],
        "merchant_name":  t.get("merchant_name"),
        "amount_cents":   dollars_to_cents(t["amount"]),
        "account_id":     t["account_id"],
        "plaid_primary":  pfc.get("primary"),
        "plaid_detailed": pfc.get("detailed"),
    }

def upsert_transactions(connection, transactions: list[dict]) -> int:
    sql = """
      INSERT INTO transactions
      (transaction_id, date, name, merchant_name, amount_cents, account_id, plaid_primary, plaid_detailed)
          VALUES
              (:transaction_id, :date, :name, :merchant_name, :amount_cents, :account_id, :plaid_primary, :plaid_detailed)
              ON CONFLICT(transaction_id) DO UPDATE SET
                 date           = excluded.date,
                 name           = excluded.name,
                 merchant_name  = excluded.merchant_name,
                 amount_cents   = excluded.amount_cents,
                 account_id     = excluded.account_id,
                 plaid_primary  = excluded.plaid_primary,
                 plaid_detailed = excluded.plaid_detailed\
        """
    rows = [to_db_row(t) for t in transactions]
    with connection:
        connection.executemany(sql, rows)
    return len(rows)

def get_all_transactions(conn) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM transactions ORDER BY date DESC, transaction_id"
    ).fetchall()