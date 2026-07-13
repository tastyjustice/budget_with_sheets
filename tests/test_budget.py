"""
Unit tests for plaid-budget project.

Run with:
    python -m pytest test_budget.py -v
    # or without pytest:
    python -m unittest test_budget -v

These tests cover pure logic functions only — no real Plaid or Google Sheets
connections are made. External dependencies are mocked at the module level so
sync.py can be imported without live credentials.
"""

import sys
import unittest
from datetime import date
from unittest.mock import MagicMock


# ── Mock out all external dependencies before importing sync ──────────────────
# sync.py runs Plaid + Google auth at import time, so we patch those libraries
# before the import happens to prevent credential errors during testing.

plaid_mock = MagicMock()
plaid_mock.Environment.Sandbox = "sandbox"
plaid_mock.Environment.Production = "production"
sys.modules["plaid"] = plaid_mock
sys.modules["plaid.api"] = MagicMock()
sys.modules["plaid.api.plaid_api"] = MagicMock()
sys.modules["plaid.model.transactions_get_request"] = MagicMock()
sys.modules["plaid.model.transactions_get_request_options"] = MagicMock()
sys.modules["gspread"] = MagicMock()
sys.modules["google"] = MagicMock()
sys.modules["google.oauth2"] = MagicMock()
sys.modules["google.oauth2.service_account"] = MagicMock()
sys.modules["dotenv"] = MagicMock()

import sync
from sync import (
    append_new_transactions,
    generate_manual_id,
    get_sheet_index,
    transactions_to_rows,
    COL_TRANSACTION_ID,
)


# ── Shared test helpers ───────────────────────────────────────────────────────

HEADER_ROW = [
    "Date", "Name", "Merchant", "Category",
    "Amount", "Account", "Transaction ID", "Locked"
]


def make_sheet_mock(rows: list[list]) -> MagicMock:
    """
    Create a mock gspread Worksheet whose get_all_values() returns the given rows.
    The first row is treated as the header (skipped by get_sheet_index).
    """
    sheet = MagicMock()
    sheet.get_all_values.return_value = rows
    return sheet


def make_transaction(
        txn_id: str = "txn_001",
        name: str = "Coffee Shop",
        merchant_name: str = "Starbucks",
        amount: float = 5.50,
        account_id: str = "acct_12345678abcd",
        date_str: str = "2026-06-01",
        detailed_category: str = "FOOD_AND_DRINK_COFFEE",
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


# ── Tests: get_sheet_index ────────────────────────────────────────────────────

class TestGetSheetIndex(unittest.TestCase):

    def setUp(self):
        """Shared header row and a helper to build a sheet with one data row."""
        self.header = HEADER_ROW

    def _make_single_row_sheet(self, txn_id: str, locked_value: str = "") -> MagicMock:
        """Build a mock sheet with one data row for the given txn_id and locked value."""
        row = ["2026-06-01", "Shop", "Shop", "MISC", "5.00", "acct1234", txn_id, locked_value]
        return make_sheet_mock([self.header, row])

    # ── unlocked row ──

    def test_unlocked_row_is_in_index(self):
        """A row with an empty column H should appear in the index."""
        sheet = self._make_single_row_sheet("txn_001", "")
        index = get_sheet_index(sheet)
        self.assertIn("txn_001", index)

    def test_unlocked_row_has_locked_false(self):
        """A row with an empty column H should have locked=False."""
        sheet = self._make_single_row_sheet("txn_001", "")
        index = get_sheet_index(sheet)
        self.assertFalse(index["txn_001"]["locked"])

    # ── locked row ──

    def test_locked_row_is_in_index(self):
        """A row with 'LOCKED' in column H should appear in the index."""
        sheet = self._make_single_row_sheet("txn_002", "LOCKED")
        index = get_sheet_index(sheet)
        self.assertIn("txn_002", index)

    def test_locked_row_has_locked_true(self):
        """A row with 'LOCKED' in column H should have locked=True."""
        sheet = self._make_single_row_sheet("txn_002", "LOCKED")
        index = get_sheet_index(sheet)
        self.assertTrue(index["txn_002"]["locked"])

    # ── case insensitivity ──

    def test_locked_is_case_insensitive(self):
        """'locked', 'Locked', 'LOCKED', and padded variants should all be treated as locked."""
        for value in ["locked", "Locked", "LOCKED", "  LOCKED  "]:
            with self.subTest(value=value):
                sheet = self._make_single_row_sheet("txn_003", value)
                index = get_sheet_index(sheet)
                self.assertTrue(index["txn_003"]["locked"])

    # ── missing / short rows ──

    def test_row_without_transaction_id_is_skipped(self):
        """Rows with an empty transaction ID should not appear in the index."""
        row = ["2026-06-01", "Something", "", "MISC", "1.00", "acct1234", "", ""]
        sheet = make_sheet_mock([self.header, row])
        index = get_sheet_index(sheet)
        self.assertEqual(len(index), 0)

    def test_row_shorter_than_locked_column_is_in_index(self):
        """A row missing column H entirely should still appear in the index."""
        row = ["2026-06-01", "Shop", "Shop", "MISC", "5.00", "acct1234", "txn_004"]
        sheet = make_sheet_mock([self.header, row])
        index = get_sheet_index(sheet)
        self.assertIn("txn_004", index)

    def test_row_shorter_than_locked_column_defaults_to_unlocked(self):
        """A row missing column H entirely should default to locked=False."""
        row = ["2026-06-01", "Shop", "Shop", "MISC", "5.00", "acct1234", "txn_004"]
        sheet = make_sheet_mock([self.header, row])
        index = get_sheet_index(sheet)
        self.assertFalse(index["txn_004"]["locked"])

    # ── row numbers ──

    def test_first_data_row_has_row_number_two(self):
        """The first data row (after the header) should have row number 2."""
        row = ["2026-06-01", "A", "", "MISC", "1.00", "acct1", "txn_005", ""]
        sheet = make_sheet_mock([self.header, row])
        index = get_sheet_index(sheet)
        self.assertEqual(index["txn_005"]["row"], 2)

    def test_second_data_row_has_row_number_three(self):
        """The second data row should have row number 3."""
        rows = [
            self.header,
            ["2026-06-01", "A", "", "MISC", "1.00", "acct1", "txn_005", ""],
            ["2026-06-02", "B", "", "MISC", "2.00", "acct1", "txn_006", ""],
        ]
        sheet = make_sheet_mock(rows)
        index = get_sheet_index(sheet)
        self.assertEqual(index["txn_006"]["row"], 3)

    # ── empty sheet ──

    def test_empty_sheet_returns_empty_index(self):
        """A sheet with only a header row should return an empty index."""
        sheet = make_sheet_mock([self.header])
        index = get_sheet_index(sheet)
        self.assertEqual(index, {})

    # ── multiple rows ──

    def test_multiple_rows_count(self):
        """All valid rows should be indexed — count should match."""
        rows = [
            self.header,
            ["2026-06-01", "A", "", "MISC", "1.00", "acct1", "txn_010", ""],
            ["2026-06-02", "B", "", "MISC", "2.00", "acct1", "txn_011", "LOCKED"],
            ["2026-06-03", "C", "", "MISC", "3.00", "acct1", "txn_012", ""],
        ]
        sheet = make_sheet_mock(rows)
        index = get_sheet_index(sheet)
        self.assertEqual(len(index), 3)

    def test_multiple_rows_all_present_in_index(self):
        """Each transaction ID from multiple rows should be findable in the index."""
        rows = [
            self.header,
            ["2026-06-01", "A", "", "MISC", "1.00", "acct1", "txn_010", ""],
            ["2026-06-02", "B", "", "MISC", "2.00", "acct1", "txn_011", "LOCKED"],
            ["2026-06-03", "C", "", "MISC", "3.00", "acct1", "txn_012", ""],
        ]
        sheet = make_sheet_mock(rows)
        index = get_sheet_index(sheet)
        for txn_id in ["txn_010", "txn_011", "txn_012"]:
            with self.subTest(txn_id=txn_id):
                self.assertIn(txn_id, index)


# ── Tests: append_new_transactions ────────────────────────────────────────────

class TestAppendNewTransactions(unittest.TestCase):

    def setUp(self):
        """Fresh mock sheet and a helper row builder for every test."""
        self.sheet = MagicMock()

    def _make_row(self, txn_id: str) -> list:
        return ["2026-06-01", "Name", "Merchant", "MISC", 10.00, "acct1234", txn_id, ""]

    # ── new transactions ──

    def test_new_transaction_calls_append_rows(self):
        """A transaction not in the index should trigger append_rows."""
        rows = [self._make_row("txn_new")]
        append_new_transactions(self.sheet, rows, {})
        self.sheet.append_rows.assert_called_once()

    def test_new_transaction_returns_count_of_one(self):
        """Appending one new transaction should return 1."""
        rows = [self._make_row("txn_new")]
        count = append_new_transactions(self.sheet, rows, {})
        self.assertEqual(count, 1)

    # ── existing transactions ──

    def test_existing_transaction_does_not_call_append_rows(self):
        """A transaction ID already in the index should not trigger append_rows."""
        rows = [self._make_row("txn_exists")]
        existing_index = {"txn_exists": {"row": 2, "locked": False}}
        append_new_transactions(self.sheet, rows, existing_index)
        self.sheet.append_rows.assert_not_called()

    def test_existing_transaction_returns_zero(self):
        """Skipping an already-existing transaction should return 0."""
        rows = [self._make_row("txn_exists")]
        existing_index = {"txn_exists": {"row": 2, "locked": False}}
        count = append_new_transactions(self.sheet, rows, existing_index)
        self.assertEqual(count, 0)

    # ── locked transactions ──

    def test_locked_transaction_does_not_call_append_rows(self):
        """A locked transaction should not trigger append_rows."""
        rows = [self._make_row("txn_locked")]
        existing_index = {"txn_locked": {"row": 3, "locked": True}}
        append_new_transactions(self.sheet, rows, existing_index)
        self.sheet.append_rows.assert_not_called()

    def test_locked_transaction_returns_zero(self):
        """Skipping a locked transaction should return 0."""
        rows = [self._make_row("txn_locked")]
        existing_index = {"txn_locked": {"row": 3, "locked": True}}
        count = append_new_transactions(self.sheet, rows, existing_index)
        self.assertEqual(count, 0)

    # ── mixed batches ──

    def test_mixed_batch_only_appends_new_rows(self):
        """In a mixed batch, only rows with new transaction IDs should be passed to append_rows."""
        rows = [
            self._make_row("txn_old"),
            self._make_row("txn_new_1"),
            self._make_row("txn_new_2"),
        ]
        existing_index = {"txn_old": {"row": 2, "locked": False}}
        append_new_transactions(self.sheet, rows, existing_index)
        appended = self.sheet.append_rows.call_args[0][0]
        appended_ids = [r[COL_TRANSACTION_ID - 1] for r in appended]
        self.assertNotIn("txn_old", appended_ids)

    def test_mixed_batch_returns_correct_count(self):
        """Return value should equal only the number of new rows, not the total batch size."""
        rows = [
            self._make_row("txn_old"),
            self._make_row("txn_new_1"),
            self._make_row("txn_new_2"),
        ]
        existing_index = {"txn_old": {"row": 2, "locked": False}}
        count = append_new_transactions(self.sheet, rows, existing_index)
        self.assertEqual(count, 2)

    # ── empty input ──

    def test_empty_rows_does_not_call_append_rows(self):
        """An empty rows list should not trigger append_rows."""
        append_new_transactions(self.sheet, [], {})
        self.sheet.append_rows.assert_not_called()

    def test_empty_rows_returns_zero(self):
        """An empty rows list should return 0."""
        count = append_new_transactions(self.sheet, [], {})
        self.assertEqual(count, 0)


# ── Tests: transactions_to_rows ───────────────────────────────────────────────

class TestTransactionsToRows(unittest.TestCase):

    # ── category fallback logic ──

    def test_detailed_category_used_when_present(self):
        """detailed category should be preferred over primary."""
        txn = make_transaction(detailed_category="FOOD_AND_DRINK_COFFEE", primary_category="FOOD_AND_DRINK")
        row = transactions_to_rows([txn])[0]
        self.assertEqual(row[3], "FOOD_AND_DRINK_COFFEE")

    def test_falls_back_to_primary_when_detailed_is_none(self):
        """When detailed is None, should fall back to primary."""
        txn = make_transaction()
        txn["personal_finance_category"]["detailed"] = None
        row = transactions_to_rows([txn])[0]
        self.assertEqual(row[3], "FOOD_AND_DRINK")

    def test_empty_string_when_personal_finance_category_is_none(self):
        """When personal_finance_category itself is None, category should be empty string."""
        txn = make_transaction()
        txn["personal_finance_category"] = None
        row = transactions_to_rows([txn])[0]
        self.assertEqual(row[3], "")

    # ── merchant name ──

    def test_none_merchant_name_becomes_empty_string(self):
        """None merchant_name should become empty string, not the string 'None'."""
        txn = make_transaction(merchant_name=None)
        row = transactions_to_rows([txn])[0]
        self.assertEqual(row[2], "")

    # ── account id ──

    def test_account_id_truncated_to_8_chars(self):
        """account_id longer than 8 characters should be truncated."""
        txn = make_transaction(account_id="abcdefghijklmnop")
        row = transactions_to_rows([txn])[0]
        self.assertEqual(row[5], "abcdefgh")

    # ── locked column ──

    def test_locked_column_is_empty_string(self):
        """Column H (Locked) should always be empty string for Plaid-sourced rows."""
        txn = make_transaction()
        row = transactions_to_rows([txn])[0]
        self.assertEqual(row[7], "")

    # ── column order ──

    def test_date_is_column_0(self):
        row = transactions_to_rows([make_transaction(date_str="2026-06-15")])[0]
        self.assertEqual(row[0], "2026-06-15")

    def test_name_is_column_1(self):
        row = transactions_to_rows([make_transaction(name="Test Shop")])[0]
        self.assertEqual(row[1], "Test Shop")

    def test_merchant_is_column_2(self):
        row = transactions_to_rows([make_transaction(merchant_name="Test Merchant")])[0]
        self.assertEqual(row[2], "Test Merchant")

    def test_category_is_column_3(self):
        row = transactions_to_rows([make_transaction(detailed_category="SHOPPING")])[0]
        self.assertEqual(row[3], "SHOPPING")

    def test_amount_is_column_4(self):
        row = transactions_to_rows([make_transaction(amount=42.00)])[0]
        self.assertEqual(row[4], 42.00)

    def test_account_is_column_5(self):
        row = transactions_to_rows([make_transaction(account_id="acct_abcdefgh")])[0]
        self.assertEqual(row[5], "acct_abc")

    def test_transaction_id_is_column_6(self):
        row = transactions_to_rows([make_transaction(txn_id="txn_col_test")])[0]
        self.assertEqual(row[6], "txn_col_test")

    # ── multiple transactions ──

    def test_multiple_transactions_all_converted(self):
        """All transactions in the input list should produce a corresponding output row."""
        txns = [make_transaction(txn_id=f"txn_{i}") for i in range(5)]
        rows = transactions_to_rows(txns)
        self.assertEqual(len(rows), 5)


# ── Tests: generate_manual_id ─────────────────────────────────────────────────

class TestGenerateManualId(unittest.TestCase):

    def setUp(self):
        """Precompute today's date string used in all ID assertions."""
        self.today = date.today().strftime("%Y%m%d")

    def test_returns_001_when_no_existing_ids(self):
        """First ID generated should end in 001."""
        result = generate_manual_id(set())
        self.assertEqual(result, f"MANUAL_{self.today}_001")

    def test_increments_when_001_exists(self):
        """If _001 already exists, should return _002."""
        existing = {f"MANUAL_{self.today}_001"}
        result = generate_manual_id(existing)
        self.assertEqual(result, f"MANUAL_{self.today}_002")

    def test_skips_all_existing_ids(self):
        """Should keep incrementing until an unused ID is found."""
        existing = {f"MANUAL_{self.today}_{i:03d}" for i in range(1, 10)}
        result = generate_manual_id(existing)
        self.assertEqual(result, f"MANUAL_{self.today}_010")

    def test_result_is_never_in_existing_ids(self):
        """The returned ID should never collide with an existing ID."""
        existing = {f"MANUAL_{self.today}_{i:03d}" for i in range(1, 50)}
        result = generate_manual_id(existing)
        self.assertNotIn(result, existing)

    def test_id_matches_expected_format(self):
        """ID should match the MANUAL_YYYYMMDD_NNN format exactly."""
        result = generate_manual_id(set())
        self.assertRegex(result, r"^MANUAL_\d{8}_\d{3}$")

    def test_ids_from_different_date_do_not_affect_counter(self):
        """IDs from a different date should not cause the counter to skip."""
        existing = {"OTHER_20200101_001", "MANUAL_20200101_001"}
        result = generate_manual_id(existing)
        self.assertEqual(result, f"MANUAL_{self.today}_001")

    def test_ids_with_different_prefix_do_not_affect_counter(self):
        """IDs with a non-MANUAL prefix should not cause the counter to skip."""
        existing = {f"OTHER_{self.today}_001"}
        result = generate_manual_id(existing)
        self.assertEqual(result, f"MANUAL_{self.today}_001")


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)