"""VALIDATE (the Python half): decide which records may proceed and which get quarantined.

Three kinds of check, in this order:

1. Row-level   - looks at one record in isolation.
                 MISSING_FIELD, INVALID_DATE, FUTURE_DATE, INVALID_AMOUNT,
                 INVALID_CURRENCY, INVALID_TXN_TYPE, SIGN_TYPE_MISMATCH
2. Batch-level - compares records that arrived in the same run (possibly from different files).
                 DUPLICATE_IN_BATCH   same txn_id, identical values -> keep the first, reject the rest
                 CONFLICTING_DUPLICATE same txn_id, different values -> reject ALL of them,
                                       because we cannot know which one is right
3. Control totals - a JSON file can declare its own record count / total; we recompute and compare.
                 These produce warnings about the file, not rejected records.

The SQL half (comparing against what is already in the database: CONFLICT_WITH_DB)
lives in load.py, because it needs the database.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

import pandas as pd

from . import config
from .extract import BatchMeta

REQUIRED = ["txn_id", "account_id", "txn_date", "amount", "currency", "txn_type"]
# What "the same transaction" means. Description is free text and is deliberately not part of it.
BUSINESS_FIELDS = ["account_id", "txn_date", "amount", "currency", "txn_type"]


def _blank(df: pd.DataFrame, col: str) -> pd.Series:
    """True where the ORIGINAL text was empty/whitespace."""
    return df["_raw"].map(lambda raw: raw[col].strip() == "")


def validate(
    df: pd.DataFrame,
    today: date | None = None,
    allowed_currencies=config.ALLOWED_CURRENCIES,
) -> tuple[pd.DataFrame, list[dict]]:
    """Return (valid rows, list of reject records)."""
    today = today or date.today()
    errors: dict[int, list[tuple[str, str]]] = defaultdict(list)

    def flag(mask: pd.Series, code: str, detail: str) -> None:
        for idx in df.index[mask]:
            errors[idx].append((code, detail))

    # ---- 1. row-level ----------------------------------------------------
    for col in REQUIRED:
        flag(_blank(df, col), "MISSING_FIELD", f"{col} is empty")

    def unparseable(col: str) -> pd.Series:
        return df[col].isna() & ~_blank(df, col)

    flag(unparseable("txn_date"), "INVALID_DATE",
         "txn_date is not a valid ISO date (YYYY-MM-DD)")
    flag(df["txn_date"].map(lambda d: pd.notna(d) and d > today), "FUTURE_DATE",
         "txn_date is in the future")
    flag(unparseable("amount"), "INVALID_AMOUNT",
         "amount is not a valid number with at most 2 decimal places")
    flag(df["amount"].map(lambda a: pd.notna(a) and a == 0), "INVALID_AMOUNT",
         "amount is zero")
    flag(df["currency"].notna() & ~df["currency"].isin(allowed_currencies), "INVALID_CURRENCY",
         f"currency is not one of {sorted(allowed_currencies)}")
    flag(unparseable("txn_type"), "INVALID_TXN_TYPE",
         "txn_type is not recognised (expected DEBIT/CREDIT or D/C)")

    def sign_mismatch(amount, txn_type) -> bool:
        if pd.isna(amount) or pd.isna(txn_type) or amount == 0:
            return False
        return (txn_type == "CREDIT" and amount < 0) or (txn_type == "DEBIT" and amount > 0)

    flag(
        pd.Series([sign_mismatch(a, t) for a, t in zip(df["amount"], df["txn_type"])],
                  index=df.index),
        "SIGN_TYPE_MISMATCH",
        "amount sign contradicts txn_type (credits must be positive, debits negative)",
    )

    # ---- 2. batch-level (only among rows that passed step 1) --------------
    clean = df.loc[~df.index.isin(list(errors))]
    for txn_id, group in clean.groupby("txn_id", sort=False):
        if len(group) == 1:
            continue
        keys = [tuple(row) for row in group[BUSINESS_FIELDS].itertuples(index=False)]
        where = [f"{s} row {r}" for s, r in zip(group["_source"], group["_row"])]
        if len(set(keys)) == 1:
            for idx in group.index[1:]:
                errors[idx].append(
                    ("DUPLICATE_IN_BATCH", f"exact duplicate of {where[0]} (kept)")
                )
        else:
            differing = [c for c in BUSINESS_FIELDS if group[c].nunique() > 1]
            for idx in group.index:
                errors[idx].append((
                    "CONFLICTING_DUPLICATE",
                    f"txn_id appears {len(group)} times with different {differing}: "
                    + ", ".join(where),
                ))

    # ---- split ------------------------------------------------------------
    rejects = []
    for idx in sorted(errors):
        row = df.loc[idx]
        codes = list(dict.fromkeys(code for code, _ in errors[idx]))
        rejects.append({
            "source_file": row["_source"],
            "source_row": int(row["_row"]),
            "txn_id": row["_raw"]["txn_id"].strip() or None,
            "error_codes": codes,
            "error_details": "; ".join(detail for _, detail in errors[idx]),
            "raw_record": row["_raw"],
        })
    valid = df.drop(index=list(errors))
    return valid, rejects


# ---- 3. control totals -------------------------------------------------------
def check_batches(metas: list[BatchMeta], df: pd.DataFrame) -> list[dict]:
    """Compare what a file says about itself with what it actually contains."""
    warnings = []
    for meta in metas:
        rows = df[df["_source"] == meta.source_file]
        if meta.declared_count is not None and meta.declared_count != len(rows):
            warnings.append({
                "source_file": meta.source_file,
                "code": "RECORD_COUNT_MISMATCH",
                "detail": f"file declares {meta.declared_count} records but contains {len(rows)}",
            })
        if meta.declared_total is not None:
            amounts = rows["amount"].dropna()
            actual = sum(amounts, Decimal("0"))
            if actual != meta.declared_total:
                skipped = len(rows) - len(amounts)
                note = f" ({skipped} rows with unreadable amounts not summed)" if skipped else ""
                warnings.append({
                    "source_file": meta.source_file,
                    "code": "CONTROL_TOTAL_MISMATCH",
                    "detail": (f"file declares total {meta.declared_total} but amounts sum to "
                               f"{actual} (difference {meta.declared_total - actual}){note}"),
                })
    return warnings
