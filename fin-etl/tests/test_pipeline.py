"""Integration tests. They need a running PostgreSQL.

They use a SEPARATE database (fin_etl_test) and wipe it before every test, so
they never touch the data you loaded by hand. Override with TEST_DATABASE_URL.
"""
import os
import shutil
from pathlib import Path

import psycopg2
import pytest

from fin_etl import config, extract, load, pipeline

TEST_URL = os.environ.get("TEST_DATABASE_URL",
                          "postgresql://fin_etl:fin_etl@localhost:5432/fin_etl_test")
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def conn():
    try:
        c = load.connect(TEST_URL)
    except psycopg2.OperationalError:
        pytest.skip("PostgreSQL test database not available")
    load.apply_schema(c)
    with c, c.cursor() as cur:
        cur.execute("TRUNCATE rejected_records, transactions, load_runs RESTART IDENTITY CASCADE")
    yield c
    c.close()


def count(conn, table):
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) FROM {table}")
        return cur.fetchone()[0]


def run(conn, tmp_path, data_dir=None):
    return pipeline.run(data_dir or ROOT / "data", conn=conn, report_dir=tmp_path)


def test_sample_data_counts_match_the_seeded_defects(conn, tmp_path):
    r = run(conn, tmp_path)
    assert (r.rows_read, r.rows_loaded, r.rows_skipped, r.rows_rejected) == (81, 61, 0, 20)
    assert r.rejected_by_code == {
        "MISSING_FIELD": 4, "INVALID_DATE": 4, "FUTURE_DATE": 1, "INVALID_AMOUNT": 4,
        "INVALID_CURRENCY": 1, "INVALID_TXN_TYPE": 2, "SIGN_TYPE_MISMATCH": 1,
        "DUPLICATE_IN_BATCH": 2, "CONFLICTING_DUPLICATE": 2,
    }
    assert [w["code"] for w in r.batch_warnings] == ["CONTROL_TOTAL_MISMATCH"]
    assert count(conn, "transactions") == 61
    assert count(conn, "rejected_records") == 20
    assert Path(r.report_path).exists()


def test_rerunning_the_same_files_is_idempotent(conn, tmp_path):
    run(conn, tmp_path)
    r = run(conn, tmp_path)
    assert (r.rows_loaded, r.rows_skipped) == (0, 61)
    assert count(conn, "transactions") == 61           # no duplicates created


def test_conflict_with_existing_database_row(conn, tmp_path):
    run(conn, tmp_path)
    r = run(conn, tmp_path, ROOT / "data_extra")
    assert (r.rows_loaded, r.rows_skipped, r.rows_rejected) == (1, 1, 1)
    assert r.rejected_by_code == {"CONFLICT_WITH_DB": 1}
    with conn.cursor() as cur:
        cur.execute("SELECT amount::text FROM transactions WHERE txn_id = 'T-1001'")
        assert cur.fetchone()[0] == "-70.56"           # the original value was NOT overwritten


def test_conflicting_ids_are_never_loaded(conn, tmp_path):
    run(conn, tmp_path)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM transactions WHERE txn_id = 'T-1020'")
        assert cur.fetchone()[0] == 0


def test_failure_mid_load_rolls_everything_back(conn, tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("simulated crash after rows were inserted")
    monkeypatch.setattr(load, "execute_values", boom)  # reject insert happens after the txn insert
    with pytest.raises(RuntimeError):
        run(conn, tmp_path)
    assert count(conn, "transactions") == 0
    assert count(conn, "load_runs") == 0               # not even the run record survives


def test_database_constraints_are_a_real_backstop(conn):
    with conn.cursor() as cur:
        cur.execute("INSERT INTO load_runs (source_files) VALUES ('{x}') RETURNING run_id")
        run_id = cur.fetchone()[0]
        with pytest.raises(psycopg2.errors.CheckViolation):
            cur.execute("""INSERT INTO transactions
                (txn_id, account_id, txn_date, amount, currency, txn_type, source_file, run_id)
                VALUES ('X', 'A', '2025-01-01', 10.00, 'INR', 'DEBIT', 'f', %s)""", (run_id,))
    conn.rollback()


def test_broken_source_file_fails_loudly(conn, tmp_path):
    bad = tmp_path / "in"
    bad.mkdir()
    (bad / "x.csv").write_text("only,two\n1,2\n")
    with pytest.raises(extract.ExtractError):
        run(conn, tmp_path, bad)
    assert count(conn, "load_runs") == 0


def test_api_endpoints(conn, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from fin_etl import api
    monkeypatch.setattr(config, "DATABASE_URL", TEST_URL)
    monkeypatch.setattr(config, "DATA_DIR", ROOT / "data")
    monkeypatch.setattr(config, "REPORT_DIR", tmp_path)
    client = TestClient(api.app)

    assert client.get("/health").json() == {"status": "ok"}
    created = client.post("/runs")
    assert created.status_code == 201 and created.json()["rows_loaded"] == 61
    run_id = created.json()["run_id"]

    assert client.get(f"/runs/{run_id}").json()["rows_rejected"] == 20
    rej = client.get(f"/runs/{run_id}/rejects", params={"error_code": "MISSING_FIELD"}).json()
    assert len(rej) == 4
    txns = client.get("/transactions", params={"account_id": "ACC-001", "limit": 5}).json()
    assert len(txns) == 5 and isinstance(txns[0]["amount"], str)   # money is a string
    assert client.get("/runs/9999").status_code == 404
