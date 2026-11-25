# tests/helpers.py
def make_transaction(
        txn_id: str = "txn_001",
        name: str = "Coffee Shop",
        merchant_name: str = "Starbucks",
        amount: float = 5.50,
        account_id: str = "acct_12345678abcd",
        date_str: str = "2026-06-01",
        detailed_category: str = "COFFEE",
        primary_category: str = "FOOD_AND_DRINK",
) -> dict:
    """Build a minimal Plaid-style transaction dict for testing."""
    return {
        "transaction_id": txn_id,
        "name": name,
        "merchant_name": merchant_name,
        "amount": amount,
        "account_id": account_id,
        "date": date_str,
        "personal_finance_category": {
            "detailed": detailed_category,
            "primary": primary_category,
        },
    }

def get_row(conn, txn_id):
    return conn.execute("SELECT * FROM transactions WHERE transaction_id = ?", (txn_id,)).fetchone()