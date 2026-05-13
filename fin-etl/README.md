# Financial Data Validation Pipeline

A small ETL pipeline: reads financial transactions from CSV and JSON files, normalises them to one schema,
validates them with Python and SQL, loads the clean rows into PostgreSQL, and writes an error report for the
rows that failed. A read-only REST API (FastAPI) exposes the results.

Built on **synthetic sample data**. It is a personal learning project, not a production system.

**New to this? Read `STUDY.md` first.** It explains the domain, every file, every decision, and likely interview questions.

## Quick start

Requires Python 3.10+ and PostgreSQL 14+.

```bash
pip install -r requirements.txt

# create a role and two databases (one for real runs, one for tests)
psql -U postgres -c "CREATE USER fin_etl WITH PASSWORD 'fin_etl';"
psql -U postgres -c "CREATE DATABASE fin_etl OWNER fin_etl;"
psql -U postgres -c "CREATE DATABASE fin_etl_test OWNER fin_etl;"

python -m fin_etl.pipeline --init-db        # creates tables, runs on ./data
python -m fin_etl.pipeline                  # run again: loads 0, skips 61 (idempotent)
python -m fin_etl.pipeline --data-dir data_extra   # late arrivals: 1 loaded, 1 skipped, 1 conflict
```

Different database? `export DATABASE_URL=postgresql://user:pass@host:5432/dbname`
(PowerShell: `$env:DATABASE_URL="..."`).

Expected output of the first run on `./data`:

```
Run 1 complete
  rows read         81
  rows loaded       61
  rows skipped       0   (already stored, identical)
  rows rejected     20
```

The error report is written to `reports/rejects_run_<id>.csv` and also stored in the `rejected_records` table.

## Tests

```bash
pytest          # 56 tests; the integration tests use the fin_etl_test database and wipe it
```

## API

```bash
uvicorn fin_etl.api:app --reload      # then open http://127.0.0.1:8000/docs
```

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | liveness + DB check |
| GET | `/runs`, `/runs/{id}` | run history and counts |
| GET | `/runs/{id}/rejects?error_code=` | the error report for a run |
| GET | `/transactions?account_id=` | loaded data (amounts as strings) |
| POST | `/runs` | run the pipeline on the configured data directory |

## Layout

```
schema.sql              tables + constraints
fin_etl/extract.py      read CSV/JSON as text into one DataFrame
fin_etl/transform.py    parse and normalise fields (never rejects)
fin_etl/validate.py     row-level, duplicate/conflict and control-total checks (Python)
fin_etl/load.py         staging table, SQL conflict check, insert, rejects, run record (SQL)
fin_etl/report.py       error-report CSV
fin_etl/pipeline.py     glue + command line
fin_etl/api.py          REST API
generate_sample_data.py regenerates data/ and data_extra/ (deterministic)
tests/                  unit tests + Postgres integration tests
```
