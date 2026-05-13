"""TRANSFORM: turn messy text into one consistent, typed schema.

This module never rejects anything. If a value cannot be parsed it becomes
None, and validate.py decides what that means (missing vs invalid).

Rules for each field:
  txn_id, account_id   trim whitespace; account_id upper-cased
  txn_date             ISO 8601 only (YYYY-MM-DD, or a datetime like 2025-02-11T09:30:00Z).
                       We take the calendar date exactly as written, no timezone shifting.
                       Other formats such as 10/03/2025 are NOT guessed: DD/MM vs MM/DD is
                       ambiguous and a wrong guess silently corrupts data.
  amount               exact Decimal, at most 2 decimal places, within NUMERIC(18,2).
                       We never round: 10.555 is rejected, not turned into 10.56.
  currency             trim + upper-case
  txn_type             D/DR/DEBIT -> DEBIT, C/CR/CREDIT -> CREDIT (case-insensitive)
  description          trim; empty becomes None
"""
from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

import pandas as pd

TYPE_MAP = {
    "DEBIT": "DEBIT", "D": "DEBIT", "DR": "DEBIT",
    "CREDIT": "CREDIT", "C": "CREDIT", "CR": "CREDIT",
}

_DATE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?)?$"
)
_AMOUNT_RE = re.compile(r"^[+-]?\d+(\.\d+)?$")
_TWO_PLACES = Decimal("0.01")
MAX_ABS_AMOUNT = Decimal("9999999999999999.99")  # what NUMERIC(18,2) can hold


def clean_text(value: str) -> str | None:
    s = value.strip()
    return s or None


def clean_upper(value: str) -> str | None:
    s = value.strip().upper()
    return s or None


def parse_date(value: str) -> date | None:
    s = value.strip()
    if not _DATE_RE.match(s):
        return None
    try:
        return date.fromisoformat(s[:10])  # also rejects 2025-02-30, 2025-13-45
    except ValueError:
        return None


def parse_amount(value: str) -> Decimal | None:
    s = value.strip()
    if not _AMOUNT_RE.match(s):
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    if -d.normalize().as_tuple().exponent > 2:  # more than 2 decimal places
        return None
    d = d.quantize(_TWO_PLACES)
    if abs(d) > MAX_ABS_AMOUNT:
        return None
    return d


def parse_txn_type(value: str) -> str | None:
    return TYPE_MAP.get(value.strip().upper())


def _apply(series: pd.Series, func) -> pd.Series:
    # dtype=object keeps real Python date/Decimal/None values (no NaN coercion).
    return pd.Series([func(v) for v in series], index=series.index, dtype=object)


def normalize(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["txn_id"] = _apply(df["txn_id"], clean_text)
    out["account_id"] = _apply(df["account_id"], clean_upper)
    out["txn_date"] = _apply(df["txn_date"], parse_date)
    out["amount"] = _apply(df["amount"], parse_amount)
    out["currency"] = _apply(df["currency"], clean_upper)
    out["txn_type"] = _apply(df["txn_type"], parse_txn_type)
    out["description"] = _apply(df["description"], clean_text)
    return out
