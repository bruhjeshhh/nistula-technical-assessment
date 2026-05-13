"""A small REST API over the pipeline's results.

    GET  /health
    GET  /runs                          list of runs, newest first
    GET  /runs/{run_id}                 one run's counts and batch warnings
    GET  /runs/{run_id}/rejects         the error report for that run (?error_code= to filter)
    GET  /transactions                  loaded data (?account_id= to filter)
    POST /runs                          run the pipeline on the configured data directory

Money is returned as a STRING ("-125.50"), not a JSON number. JSON numbers are
parsed as binary floats by most clients (JavaScript always does), which is
exactly the problem NUMERIC in the database was chosen to avoid.

Run it:  uvicorn fin_etl.api:app --reload      then open http://127.0.0.1:8000/docs
"""
from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Query
from psycopg2.extras import RealDictCursor

from . import extract, load, pipeline

app = FastAPI(title="Financial Data Validation Pipeline")


def get_conn():
    conn = load.connect()
    try:
        yield conn
    finally:
        conn.close()


def _query(conn, sql: str, params: tuple = ()) -> list[dict]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


@app.get("/health")
def health(conn=Depends(get_conn)):
    _query(conn, "SELECT 1")
    return {"status": "ok"}


@app.get("/runs")
def list_runs(limit: int = Query(20, ge=1, le=200), conn=Depends(get_conn)):
    return _query(conn, "SELECT * FROM load_runs ORDER BY run_id DESC LIMIT %s", (limit,))


@app.get("/runs/{run_id}")
def get_run(run_id: int, conn=Depends(get_conn)):
    rows = _query(conn, "SELECT * FROM load_runs WHERE run_id = %s", (run_id,))
    if not rows:
        raise HTTPException(404, f"run {run_id} not found")
    return rows[0]


@app.get("/runs/{run_id}/rejects")
def get_rejects(run_id: int, error_code: str | None = None,
                limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
                conn=Depends(get_conn)):
    if not _query(conn, "SELECT 1 FROM load_runs WHERE run_id = %s", (run_id,)):
        raise HTTPException(404, f"run {run_id} not found")
    return _query(conn, """
        SELECT source_file, source_row, txn_id, error_codes, error_details, raw_record
        FROM rejected_records
        WHERE run_id = %s AND (%s::text IS NULL OR %s = ANY(error_codes))
        ORDER BY source_file, source_row LIMIT %s OFFSET %s
    """, (run_id, error_code, error_code, limit, offset))


@app.get("/transactions")
def list_transactions(account_id: str | None = None,
                      limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0),
                      conn=Depends(get_conn)):
    return _query(conn, """
        SELECT txn_id, account_id, txn_date::text AS txn_date, amount::text AS amount,
               currency, txn_type, description, source_file, run_id
        FROM transactions
        WHERE (%s::text IS NULL OR account_id = %s)
        ORDER BY txn_date, txn_id LIMIT %s OFFSET %s
    """, (account_id, account_id, limit, offset))


@app.post("/runs", status_code=201)
def trigger_run(conn=Depends(get_conn)):
    """Runs synchronously on the configured DATA_DIR. Fine at this scale; a real
    system would queue the job and return immediately."""
    try:
        return pipeline.run(conn=conn).to_dict()
    except extract.ExtractError as exc:
        raise HTTPException(422, str(exc)) from exc
