from datetime import date
from decimal import Decimal

import pytest

from fin_etl.transform import (clean_text, clean_upper, parse_amount, parse_date,
                               parse_txn_type)


@pytest.mark.parametrize("text, expected", [
    ("2025-02-11", date(2025, 2, 11)),
    ("2025-02-11T09:30:00Z", date(2025, 2, 11)),
    ("2025-02-11 09:30", date(2025, 2, 11)),
    ("2025-02-11T23:30:00-05:00", date(2025, 2, 11)),   # date as written, no tz shifting
    ("  2025-02-11  ", date(2025, 2, 11)),
    ("10/03/2025", None),     # ambiguous DD/MM vs MM/DD -> refuse to guess
    ("2025-13-45", None),
    ("2025-02-30", None),     # regex ok, calendar says no
    ("yesterday", None),
    ("", None),
])
def test_parse_date(text, expected):
    assert parse_date(text) == expected


@pytest.mark.parametrize("text, expected", [
    ("100", Decimal("100.00")),
    ("-75", Decimal("-75.00")),
    ("1250.5", Decimal("1250.50")),
    ("+10.00", Decimal("10.00")),
    ("100.500", Decimal("100.50")),        # trailing zero is not a third decimal place
    ("0.00", Decimal("0.00")),
    ("-10.555", None),                      # would need rounding -> reject, never round money
    ("12abc", None),
    ("1,250.50", None),
    ("1e3", None),
    ("NaN", None),
    ("Infinity", None),
    ("", None),
    ("99999999999999999.00", None),          # too big for NUMERIC(18,2)
])
def test_parse_amount(text, expected):
    result = parse_amount(text)
    assert result == expected
    if expected is not None:
        assert isinstance(result, Decimal)


def test_no_float_precision_loss():
    # The classic float trap: 0.1 + 0.2 != 0.3. Decimal gets it right.
    assert parse_amount("0.10") + parse_amount("0.20") == parse_amount("0.30")


@pytest.mark.parametrize("text, expected", [
    ("DEBIT", "DEBIT"), ("debit", "DEBIT"), ("D", "DEBIT"), ("dr", "DEBIT"),
    ("Credit", "CREDIT"), ("C", "CREDIT"), ("CR", "CREDIT"),
    ("TRANSFER", None), ("", None), ("X", None),
])
def test_parse_txn_type(text, expected):
    assert parse_txn_type(text) == expected


def test_clean_text_and_upper():
    assert clean_text("  hello ") == "hello"
    assert clean_text("   ") is None
    assert clean_upper(" inr ") == "INR"
    assert clean_upper("") is None
