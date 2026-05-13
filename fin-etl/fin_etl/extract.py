"""EXTRACT: read the CSV and JSON files into one DataFrame with one set of column names.

Two deliberate decisions in this file:

1. Everything is read as TEXT. If we let pandas guess types it would turn
   amounts into float64 (binary floats cannot represent 0.10 exactly) and
   would quietly strip leading zeros, parse "NA" as missing, and so on. We
   keep the original text and convert on purpose in transform.py.
2. A file that is structurally broken (missing columns, not valid JSON) fails
   the whole run loudly. A single bad *record* is not structural; it is handled
   later by quarantining that record.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import pandas as pd

CANONICAL_COLS = [
    "txn_id", "account_id", "txn_date", "amount", "currency", "txn_type", "description",
]

# The JSON feed names its fields differently. This map is the whole "schema mapping" for it.
JSON_FIELD_MAP = {
    "transactionId": "txn_id",
    "accountNumber": "account_id",
    "postedAt": "txn_date",
    "amount": "amount",
    "currencyCode": "currency",
    "direction": "txn_type",
    "memo": "description",
}


class ExtractError(Exception):
    """The file itself is unusable (as opposed to one bad record inside it)."""


@dataclass
class BatchMeta:
    """What a source file *claims* about itself (only JSON envelopes do this)."""
    source_file: str
    declared_count: int | None = None
    declared_total: Decimal | None = None


def _to_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, Decimal):
        return format(value, "f")  # plain notation, never 1E+2
    return str(value)


def read_csv(path: Path) -> pd.DataFrame:
    try:
        df = pd.read_csv(
            path, dtype=str, keep_default_na=False, encoding="utf-8-sig",
            on_bad_lines="error",
        )
    except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as exc:
        raise ExtractError(f"{path.name}: cannot parse CSV ({exc})") from exc
    missing = [c for c in CANONICAL_COLS if c not in df.columns]
    if missing:
        raise ExtractError(f"{path.name}: missing required columns {missing}")
    return df[CANONICAL_COLS].fillna("").astype(object)


def read_json(path: Path) -> tuple[pd.DataFrame, BatchMeta]:
    try:
        with open(path, encoding="utf-8") as fh:
            # parse_float=Decimal: JSON numbers like 125.50 become exact Decimals,
            # so a float never touches money, even for numeric JSON values.
            doc = json.load(fh, parse_float=Decimal)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ExtractError(f"{path.name}: cannot parse JSON ({exc})") from exc

    meta = BatchMeta(source_file=path.name)
    if isinstance(doc, list):
        records = doc
    elif isinstance(doc, dict) and isinstance(doc.get("records"), list):
        records = doc["records"]
        if doc.get("record_count") is not None:
            meta.declared_count = int(doc["record_count"])
        if doc.get("control_total") is not None:
            meta.declared_total = Decimal(str(doc["control_total"]))
    else:
        raise ExtractError(f"{path.name}: expected a list or an object with a 'records' list")

    rows = []
    for rec in records:
        rec = rec if isinstance(rec, dict) else {}
        rows.append({
            canonical: _to_text(rec.get(src)) for src, canonical in JSON_FIELD_MAP.items()
        })
    df = pd.DataFrame(rows, columns=CANONICAL_COLS, dtype=object)
    return df, meta


def read_sources(data_dir: Path) -> tuple[pd.DataFrame, list[BatchMeta], list[str]]:
    """Read every *.csv and *.json in data_dir (sorted by name, so order is deterministic).

    Returns (all rows, per-file metadata, file names). Every row carries:
      _source  file name
      _row     1-based position of the record inside its file
      _raw     the original text values, kept for the error report
    """
    data_dir = Path(data_dir)
    files = sorted(p for p in data_dir.iterdir() if p.suffix.lower() in {".csv", ".json"})
    if not files:
        raise ExtractError(f"no .csv or .json files found in {data_dir}")

    frames, metas = [], []
    for path in files:
        if path.suffix.lower() == ".csv":
            df, meta = read_csv(path), BatchMeta(source_file=path.name)
        else:
            df, meta = read_json(path)
        df = df.reset_index(drop=True)
        df["_source"] = path.name
        df["_row"] = df.index + 1
        frames.append(df)
        metas.append(meta)

    allrows = pd.concat(frames, ignore_index=True)
    allrows["_raw"] = allrows[CANONICAL_COLS].to_dict("records")
    return allrows, metas, [p.name for p in files]
