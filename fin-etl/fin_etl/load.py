"""LOAD (and the SQL half of validation): write the validated rows into PostgreSQL.

The whole run is ONE database transaction. Either everything below commits
(the run record, the loaded transactions, the rejects) or nothing does.

Steps inside the transaction:
  1. Insert a load_runs row to get a run_id.
  2. Bulk-copy the validated rows into a TEMP staging table (COPY is the fast path).
  3. SQL check: staging rows whose txn_id already exists in `transactions`
       - same values     -> skipped (this is what makes re-running safe: idempotency)
       - different values -> rejected as CONFLICT_WITH_DB
  4. INSERT the genuinely new rows (ON CONFLICT DO NOTHING is a safety net for a
     concurrent run inserting the same txn_id between our check and our insert).
  5. Self-check that every row is accounted for, store the rejects, close the run.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass

import pandas as pd
import psycopg2
from psycopg2.extras import Json, execute_values

from . import config

_COLS = ["txn_id", "account_id", "txn_date", "amount", "currency", "txn_type", "description"]
_BUSINESS = ["account_id", "txn_date", "amount", "currency", "txn_type"]
_ROW_DIFFERS = "(s.account_id, s.txn_date, s.amount, s.currency, s.txn_type)"
_ROW_STORED = "(t.account_id, t.txn_date, t.amount, t.currency, t.txn_type)"


@dataclass
class LoadResult:
    run_id: int
    rows_read: int
    rows_loaded: int
    rows_skipped: int
    rows_rejected: int
    rejects: list[dict]


def connect(url: str | None = None):
    return psycopg2.connect(url or config.DATABASE_URL)


def apply_schema(conn) -> None:
    with conn, conn.cursor() as cur:
        cur.execute(config.SCHEMA_FILE.read_text())


def _staging_csv(valid: pd.DataFrame) -> io.StringIO:
    buf = io.StringIO()
    writer = csv.writer(buf)
    # to_dict, not itertuples: pandas renames columns that start with "_" in itertuples.
    for rec in valid.to_dict("records"):
        writer.writerow([
            rec["txn_id"], rec["account_id"], rec["txn_date"].isoformat(), str(rec["amount"]),
            rec["currency"], rec["txn_type"], rec["description"] or "",
            rec["_source"], int(rec["_row"]),
        ])
    buf.seek(0)
    return buf


def load(conn, valid: pd.DataFrame, rejects: list[dict], source_files: list[str],
         rows_read: int, batch_warnings: list[dict]) -> LoadResult:
    rejects = list(rejects)
    with conn:  # commit on success, roll back on ANY exception
        with conn.cursor() as cur:
            cur.execute("INSERT INTO load_runs (source_files) VALUES (%s) RETURNING run_id",
                        (source_files,))
            run_id = cur.fetchone()[0]

            cur.execute("""
                CREATE TEMP TABLE stg (
                    txn_id text, account_id text, txn_date date, amount numeric(18,2),
                    currency char(3), txn_type text, description text,
                    source_file text, source_row int
                ) ON COMMIT DROP
            """)
            if len(valid):
                cur.copy_expert(
                    "COPY stg (txn_id, account_id, txn_date, amount, currency, txn_type, "
                    "description, source_file, source_row) FROM STDIN WITH (FORMAT csv)",
                    _staging_csv(valid),
                )

            # 3a. same txn_id, different values -> conflict with what is already stored
            cur.execute(f"""
                SELECT s.txn_id, s.source_file, s.source_row,
                       s.account_id, s.txn_date::text, s.amount::text, s.currency, s.txn_type,
                       t.account_id, t.txn_date::text, t.amount::text, t.currency, t.txn_type
                FROM stg s JOIN transactions t ON t.txn_id = s.txn_id
                WHERE {_ROW_DIFFERS} IS DISTINCT FROM {_ROW_STORED}
                ORDER BY s.source_file, s.source_row
            """)
            raw_by_row = {(r["_source"], int(r["_row"])): r["_raw"]
                          for r in valid.to_dict("records")}
            conflicts = cur.fetchall()
            for row in conflicts:
                txn_id, src, src_row = row[0], row[1], row[2]
                incoming, stored = row[3:8], row[8:13]
                diffs = [f"{name}: incoming {a} vs stored {b}"
                         for name, a, b in zip(_BUSINESS, incoming, stored) if a != b]
                rejects.append({
                    "source_file": src, "source_row": src_row, "txn_id": txn_id,
                    "error_codes": ["CONFLICT_WITH_DB"],
                    "error_details": "txn_id already stored with different values (" +
                                     "; ".join(diffs) + ")",
                    "raw_record": raw_by_row[(src, src_row)],
                })

            # 3b. same txn_id, identical values -> already loaded, skip
            cur.execute(f"""
                SELECT count(*) FROM stg s JOIN transactions t ON t.txn_id = s.txn_id
                WHERE {_ROW_DIFFERS} IS NOT DISTINCT FROM {_ROW_STORED}
            """)
            skipped = cur.fetchone()[0]

            # 4. insert what is new
            cur.execute("""
                INSERT INTO transactions
                    (txn_id, account_id, txn_date, amount, currency, txn_type,
                     description, source_file, run_id)
                SELECT s.txn_id, s.account_id, s.txn_date, s.amount, s.currency, s.txn_type,
                       s.description, s.source_file, %s
                FROM stg s
                WHERE NOT EXISTS (SELECT 1 FROM transactions t WHERE t.txn_id = s.txn_id)
                ON CONFLICT (txn_id) DO NOTHING
            """, (run_id,))
            loaded = cur.rowcount

            # 5. every row read must be accounted for exactly once
            if len(valid) != loaded + skipped + len(conflicts):
                raise RuntimeError(
                    f"row accounting failed: {len(valid)} valid rows but "
                    f"{loaded} loaded + {skipped} skipped + {len(conflicts)} conflicts"
                )
            if rows_read != len(valid) + (len(rejects) - len(conflicts)):
                raise RuntimeError("row accounting failed: read != valid + rejected")

            if rejects:
                execute_values(cur, """
                    INSERT INTO rejected_records
                        (run_id, source_file, source_row, txn_id, error_codes,
                         error_details, raw_record)
                    VALUES %s
                """, [(run_id, r["source_file"], r["source_row"], r["txn_id"],
                       r["error_codes"], r["error_details"], Json(r["raw_record"]))
                      for r in rejects])

            cur.execute("""
                UPDATE load_runs SET finished_at = now(), rows_read = %s, rows_loaded = %s,
                       rows_skipped = %s, rows_rejected = %s, batch_warnings = %s
                WHERE run_id = %s
            """, (rows_read, loaded, skipped, len(rejects), Json(batch_warnings), run_id))

    return LoadResult(run_id, rows_read, loaded, skipped, len(rejects), rejects)
