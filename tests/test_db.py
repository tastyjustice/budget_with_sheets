from _pytest.tmpdir import tmp_path
import pytest
import db
from tests.helpers import make_transaction, get_row


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    yield c
    c.close()


def test_upsert_twice_keeps_one_row(conn):
    db.upsert_transactions(conn, [make_transaction()])
    db.upsert_transactions(conn, [make_transaction()])
    assert len(db.get_all_transactions(conn)) == 1


def test_empty_database_returns_empty_list(conn):
    assert db.get_all_transactions(conn) == []


def test_data_survives_reconnect(tmp_path):
    path = tmp_path / "test.db"

    first = db.connect(path)
    db.upsert_transactions(first, [make_transaction()])
    first.close()

    second = db.connect(path)
    assert len(db.get_all_transactions(second)) == 1
    second.close()

def test_values_land_correct_columns(conn):
    db.upsert_transactions(conn, [make_transaction()])
    row = get_row(conn, "txn_001")
    assert row["transaction_id"] == "txn_001"
    assert row["name"] ==  "Coffee Shop"
    assert row["amount_cents"] ==  550
    assert row["account_id"] == "acct_12345678abcd"
    assert row["date"] == "2026-06-01"
    assert row["plaid_primary"] == "FOOD_AND_DRINK"
    assert row["plaid_detailed"] == "COFFEE"
    assert row["category"] is None