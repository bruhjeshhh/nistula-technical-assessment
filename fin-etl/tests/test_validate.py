"""Unit tests for validation. No database needed: build a DataFrame, validate it."""
from datetime import date
from decimal import Decimal

import pandas as pd

from fin_etl import extract, transform, validate

TODAY = date(2025, 6, 1)
GOOD = {"txn_id": "T-1", "account_id": "ACC-1", "txn_date": "2025-03-01", "amount": "-10.00",
        "currency": "INR", "txn_type": "DEBIT", "description": "x"}


def frame(*records, source="a.csv"):
    rows = []
    for i, rec in enumerate(records, start=1):
        rows.append({**GOOD, **rec, "_source": source, "_row": i})
    df = pd.DataFrame(rows)
    df["_raw"] = df[extract.CANONICAL_COLS].to_dict("records")
    return transform.normalize(df.astype(object))


def codes(rejects):
    return [r["error_codes"] for r in rejects]


def test_clean_row_passes():
    valid, rejects = validate.validate(frame({}), today=TODAY)
    assert len(valid) == 1 and rejects == []


def test_messy_but_valid_row_is_cleaned_not_rejected():
    df = frame({"currency": " inr ", "txn_type": "d", "account_id": "acc-1"})
    valid, rejects = validate.validate(df, today=TODAY)
    assert rejects == []
    row = valid.iloc[0]
    assert (row["currency"], row["txn_type"], row["account_id"]) == ("INR", "DEBIT", "ACC-1")


def test_missing_vs_invalid_are_distinguished():
    _, rejects = validate.validate(
        frame({"amount": ""}, {"txn_id": "T-2", "amount": "abc"}), today=TODAY)
    assert codes(rejects) == [["MISSING_FIELD"], ["INVALID_AMOUNT"]]


def test_each_row_level_rule():
    cases = {
        "INVALID_DATE": {"txn_date": "10/03/2025"},
        "FUTURE_DATE": {"txn_date": "2025-12-31"},
        "INVALID_AMOUNT": {"amount": "-10.555"},
        "INVALID_CURRENCY": {"currency": "XXX"},
        "INVALID_TXN_TYPE": {"txn_type": "TRANSFER"},
        "SIGN_TYPE_MISMATCH": {"amount": "10.00", "txn_type": "DEBIT"},
    }
    for expected, override in cases.items():
        _, rejects = validate.validate(frame(override), today=TODAY)
        assert codes(rejects) == [[expected]], expected


def test_zero_amount_is_invalid_and_not_also_a_sign_mismatch():
    _, rejects = validate.validate(frame({"amount": "0.00"}), today=TODAY)
    assert codes(rejects) == [["INVALID_AMOUNT"]]


def test_one_row_can_collect_several_errors():
    _, rejects = validate.validate(
        frame({"txn_date": "2025-02-30", "amount": "abc"}), today=TODAY)
    assert codes(rejects) == [["INVALID_DATE", "INVALID_AMOUNT"]]


def test_exact_duplicate_keeps_first_rejects_rest():
    valid, rejects = validate.validate(frame({}, {}, {}), today=TODAY)
    assert len(valid) == 1
    assert [r["source_row"] for r in rejects] == [2, 3]
    assert codes(rejects) == [["DUPLICATE_IN_BATCH"]] * 2


def test_conflicting_duplicate_rejects_every_copy():
    valid, rejects = validate.validate(frame({}, {"amount": "-11.00"}), today=TODAY)
    assert len(valid) == 0
    assert codes(rejects) == [["CONFLICTING_DUPLICATE"]] * 2
    assert "amount" in rejects[0]["error_details"]


def test_description_difference_alone_is_not_a_conflict():
    valid, rejects = validate.validate(frame({}, {"description": "other memo"}), today=TODAY)
    assert len(valid) == 1
    assert codes(rejects) == [["DUPLICATE_IN_BATCH"]]


def test_invalid_row_does_not_poison_a_valid_row_with_the_same_id():
    # The second copy is garbage (bad currency); the first, valid one must still load.
    valid, rejects = validate.validate(frame({}, {"currency": "XXX"}), today=TODAY)
    assert len(valid) == 1
    assert codes(rejects) == [["INVALID_CURRENCY"]]


def test_missing_txn_id_rows_are_not_grouped_as_duplicates():
    _, rejects = validate.validate(frame({"txn_id": ""}, {"txn_id": ""}), today=TODAY)
    assert codes(rejects) == [["MISSING_FIELD"]] * 2


def test_batch_check_control_total_and_count():
    df = frame({"txn_id": "A", "amount": "100.00", "txn_type": "CREDIT"},
               {"txn_id": "B", "amount": "-40.00"})
    ok = extract.BatchMeta("a.csv", declared_count=2, declared_total=Decimal("60"))
    bad = extract.BatchMeta("a.csv", declared_count=3, declared_total=Decimal("70.50"))
    assert validate.check_batches([ok], df) == []
    found = {w["code"] for w in validate.check_batches([bad], df)}
    assert found == {"RECORD_COUNT_MISMATCH", "CONTROL_TOTAL_MISMATCH"}
