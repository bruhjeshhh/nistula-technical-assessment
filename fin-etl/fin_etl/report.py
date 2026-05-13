"""REPORT: the error report for failed records, plus a human-readable run summary."""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

REPORT_COLUMNS = [
    "run_id", "source_file", "source_row", "txn_id", "error_codes", "error_details", "raw_record",
]


def write_reject_report(run_id: int, rejects: list[dict], report_dir: Path) -> Path:
    report_dir = Path(report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / f"rejects_run_{run_id}.csv"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(REPORT_COLUMNS)
        for r in sorted(rejects, key=lambda r: (r["source_file"], r["source_row"])):
            writer.writerow([
                run_id, r["source_file"], r["source_row"], r["txn_id"] or "",
                "|".join(r["error_codes"]), r["error_details"],
                json.dumps(r["raw_record"], ensure_ascii=False),
            ])
    return path


def reject_counts(rejects: list[dict]) -> Counter:
    """Rejected ROWS per error code (a row with two problems counts under both codes)."""
    counts: Counter = Counter()
    for r in rejects:
        counts.update(r["error_codes"])
    return counts
