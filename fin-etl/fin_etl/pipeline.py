"""Glue: extract -> transform -> validate -> load -> report.

Usage:
    python -m fin_etl.pipeline --init-db          # create tables (safe to repeat), then run
    python -m fin_etl.pipeline                    # run on ./data
    python -m fin_etl.pipeline --data-dir data_extra
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

from . import config, extract, load, report, transform, validate


@dataclass
class RunResult:
    run_id: int
    rows_read: int
    rows_loaded: int
    rows_skipped: int
    rows_rejected: int
    rejected_by_code: dict
    batch_warnings: list
    report_path: str | None

    def to_dict(self) -> dict:
        return asdict(self)


def run(data_dir: Path | None = None, conn=None, today: date | None = None,
        report_dir: Path | None = None) -> RunResult:
    owns_conn = conn is None
    conn = conn or load.connect()
    try:
        raw, metas, files = extract.read_sources(data_dir or config.DATA_DIR)
        normalized = transform.normalize(raw)
        valid, rejects = validate.validate(normalized, today=today)
        warnings = validate.check_batches(metas, normalized)
        result = load.load(conn, valid, rejects, files, len(raw), warnings)
        path = report.write_reject_report(
            result.run_id, result.rejects, report_dir or config.REPORT_DIR
        ) if result.rejects else None
        return RunResult(
            run_id=result.run_id, rows_read=result.rows_read, rows_loaded=result.rows_loaded,
            rows_skipped=result.rows_skipped, rows_rejected=result.rows_rejected,
            rejected_by_code=dict(report.reject_counts(result.rejects)),
            batch_warnings=warnings, report_path=str(path) if path else None,
        )
    finally:
        if owns_conn:
            conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Financial data validation pipeline")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--init-db", action="store_true", help="create tables first")
    args = parser.parse_args(argv)
    try:
        conn = load.connect()
        try:
            if args.init_db:
                load.apply_schema(conn)
            result = run(args.data_dir, conn=conn)
        finally:
            conn.close()
    except extract.ExtractError as exc:
        print(f"FAILED (source file problem): {exc}", file=sys.stderr)
        return 1
    print(f"Run {result.run_id} complete")
    print(f"  rows read      {result.rows_read:>5}")
    print(f"  rows loaded    {result.rows_loaded:>5}")
    print(f"  rows skipped   {result.rows_skipped:>5}   (already stored, identical)")
    print(f"  rows rejected  {result.rows_rejected:>5}")
    if result.rejected_by_code:
        print("Rejected rows by error code:")
        for code, n in sorted(result.rejected_by_code.items()):
            print(f"  {code:<22} {n:>3}")
    for w in result.batch_warnings:
        print(f"Batch warning [{w['code']}] {w['source_file']}: {w['detail']}")
    if result.report_path:
        print(f"Error report: {result.report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
