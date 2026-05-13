# STUDY.md — Financial Data Validation Pipeline

This document assumes you know nothing about data engineering. It is long on purpose. Read it in order once,
then keep the code open next to it for the second pass.

**Suggested plan (about 3 hours):**

| Time | What |
|---|---|
| 30 min | Parts 0–2: framing, concepts, big picture |
| 60 min | Parts 3–6 with the code open in your editor |
| 45 min | Part 9: run it and do the experiments (this is where it sticks) |
| 30 min | Parts 7, 8, 10, 11 |
| 30 min | Part 12: say the answers out loud without looking |

---

## Part 0 — Read this first: what this project is and isn't

**What it is.** A small, working ETL pipeline (about 760 lines of Python plus a 56-line SQL schema, plus 56 tests)
that reads financial transactions from CSV and JSON files, cleans them into one format, checks them, loads the
good ones into PostgreSQL, and quarantines the bad ones with a report explaining why. A small REST API lets you
query the results.

**What it isn't.**
- It is a personal project built on **synthetic data** (made up by `generate_sample_data.py`). It has not processed
  real financial records and has not run at a company.
- It is small: 81 input rows. It has no performance numbers, and you should not invent any.
- It uses no Airflow, dbt, Spark, Kafka, cloud services or data warehouse. "Lightweight" is the accurate word.

**Why this matters for you.** Everything an interviewer can ask is answerable from this folder, and the honest answer
to "did you do X?" is whatever is true of this folder. If you describe it as a personal project on sample data, you can
defend every line. If you describe it as something bigger, the first follow-up question ("how many records a day?",
"what broke in production?") has no answer, and the rest of your resume gets doubted too.

### Your resume claims vs the code

| Resume claim | Where it lives | Honest strength |
|---|---|---|
| "lightweight ETL pipeline" | `pipeline.py` wires `extract → transform → validate → load → report` | Real, runnable. A script, not an orchestrated system. |
| "ingest financial records from JSON/CSV sources" | `extract.py` | Real. Two feeds with *different* field names/formats. |
| "transform fields into a consistent schema" | `transform.py` | Real. Dates, amounts, currency, debit/credit codes, field names. |
| "load validated data into PostgreSQL" | `load.py`, `schema.sql` | Real. Staging table + `COPY` + one transaction. |
| "SQL and Python-based validation" | Python: `validate.py`. SQL: `load.py` (conflict check) and `schema.sql` (constraints) | Real, and the split has a reason (Part 5). |
| "detect duplicate records" | `validate.py` (in the batch) and `load.py` (against the DB) | Real. |
| "missing fields, invalid values" | `validate.py` | Real. 7 distinct row-level rules. |
| "data mismatches" | Sign vs type, cross-file conflicts, DB conflicts, control totals | Real, but vague wording. Know the four concrete meanings (Part 6). |
| "generating error reports for failed records" | `report.py` + `rejected_records` table | Real. |
| "REST APIs" (tech line) | `api.py` (FastAPI) | Real but modest: a query API plus a "run now" endpoint. The pipeline does not *consume* an external API. Say that plainly. |

### Optional: a tighter resume line

If you want the resume to match the folder exactly, this is accurate and still reads fine:

> **Financial Data Validation Pipeline** (personal project) | Python, PostgreSQL, Pandas, FastAPI
> – Built an ETL pipeline that ingests transactions from CSV/JSON feeds, normalises them to one schema, and loads
>   validated rows into PostgreSQL; re-runs are idempotent and each run is a single transaction.
> – Implemented Python and SQL validation (missing/invalid fields, duplicates, conflicting records, control-total
>   mismatches); rejected records are quarantined with reasons and exported as an error report. 56 automated tests.

Keep or change it as you like. The "56 automated tests" and "idempotent" claims are things you can demonstrate live.

---

## Part 1 — The concepts you need (plain English)

### 1.1 Data pipeline, ETL, and why this exists
Companies receive data from many sources (bank exports, partner feeds, internal systems), each in its own format.
Before anyone can use it — reporting, analytics, reconciling accounts — it has to be pulled in, standardised,
checked, and stored somewhere reliable. A program that does this is a **data pipeline**.

**ETL** = **E**xtract (read from sources), **T**ransform (clean and reshape), **L**oad (write to the destination).
This project is ETL. (ELT is the variant where you load raw data first and transform inside the database/warehouse.
Not needed to know in depth; just know it exists.)

**Batch vs streaming.** This is *batch*: it processes a pile of files in one go when you run it. Streaming handles
records continuously as they arrive (Kafka, etc.). You did batch.

### 1.2 Why validate financial data specifically
- Money must be **exact**. 0.1 + 0.2 must equal 0.3. (Part 4.3 explains why computers get this wrong by default.)
- Nothing may disappear **silently**. If a record is rejected, someone must be able to see which one and why. This
  is the "audit trail" idea, and it drives the quarantine design.
- Loading the same file twice must not **double-count** money.
- Wrong data that looks plausible is worse than an error. So the pipeline *refuses to guess* (for example it will not
  guess whether `10/03/2025` is 10 March or 3 October).

### 1.3 The data quality vocabulary (this is what "validation" is made of)
| Dimension | Question it asks | Your example |
|---|---|---|
| Completeness | Is anything missing? | `MISSING_FIELD` |
| Validity | Is each value legal? | `INVALID_DATE`, `INVALID_AMOUNT`, `INVALID_CURRENCY`, `INVALID_TXN_TYPE` |
| Uniqueness | Is anything repeated? | `DUPLICATE_IN_BATCH` |
| Consistency | Do values agree with each other / other sources? | `SIGN_TYPE_MISMATCH`, `CONFLICTING_DUPLICATE`, `CONFLICT_WITH_DB`, control totals |

Say "my checks cover completeness, validity, uniqueness and consistency" and you sound like you know the field,
because the table above is literally how the field organises this topic.

### 1.4 Glossary
- **Transaction (business sense):** one money movement, e.g. "₹1,500 credited to account ACC-002 on 14 Feb".
  Not to be confused with a *database transaction* (below). The overlap in names is a trap; know both.
- **Debit / credit:** money out / money in. In this project debits are **negative** amounts, credits **positive**.
- **Account:** who the money belongs to (`ACC-001`).
- **Currency / ISO 4217:** the 3-letter code (INR, USD, EUR, GBP).
- **Schema:** the agreed shape of the data: which fields exist, their names, types, rules.
- **Source of truth:** the one place considered correct when things disagree. Here, once loaded, it's the database.
- **Staging table:** a temporary holding table where data lands *before* being checked/merged into the real table.
- **Quarantine (a.k.a. dead-letter):** a place for records that failed checks, kept with reasons instead of dropped.
- **Idempotent:** doing it twice has the same effect as doing it once. Re-running the pipeline on the same files
  loads nothing new. This is the single most valued property of a pipeline.
- **Atomic / database transaction / ACID:** a group of database operations that either *all* happen or *none* do.
  If the pipeline crashes halfway, the database is left exactly as it was. ACID = Atomicity, Consistency, Isolation,
  Durability; you only need to explain atomicity well.
- **Primary key (PK):** a column that uniquely identifies each row; the database refuses duplicates. Here `txn_id`.
- **Constraint:** a rule the database enforces itself (NOT NULL, CHECK, PRIMARY KEY, FOREIGN KEY).
- **Index:** a lookup structure that makes searches fast (like a book index). Costs some write speed and space.
- **Reconciliation / control total:** checking that two independent sources of the same numbers agree. A file says
  "I contain 25 records totalling 12,291.07"; you recompute and compare.
- **Defence in depth:** several independent layers of checks, so a bug in one doesn't cause bad data. Here:
  Python validation, SQL checks, and database constraints.

### 1.5 Python and pandas things used here (just enough)
- **DataFrame:** a table in memory (rows × named columns). **Series:** one column of it.
- **dtype:** a column's data type. We force `object` (Python objects) so a `Decimal` stays a `Decimal`.
- **Boolean mask:** `df["x"] == 5` gives a True/False Series; `df[mask]` keeps only the True rows.
- **`groupby`:** split rows into groups sharing a value (here: same `txn_id`) and look at each group.
- **`.map(func)`:** apply a function to every value in a column.
- **`Decimal`:** Python's exact decimal number type (from the `decimal` module). Used for all money.
- **Regex:** a pattern language for text matching. We use two to reject garbage early.
- **`@dataclass`:** a short way to define a class that mostly holds data.
- **Type hints** (`str | None`): documentation the tools can check; they don't change behaviour.
- **`with` blocks (context managers):** run setup, run your code, and always run cleanup — even on errors. Used for
  files and for database transactions.
- **`yield` in `api.py`:** a FastAPI "dependency" that opens a DB connection for a request and closes it afterwards.

---

## Part 2 — The big picture

```
 data/transactions_a.csv ─┐                                    ┌─► transactions   (clean rows)
                          ├─► EXTRACT ─► TRANSFORM ─► VALIDATE ─┤
 data/transactions_b.json ┘   read as     normalise   Python     │   LOAD (one DB transaction)
   (own field names,          text,       types &     checks:    ├─► staging table ─► SQL conflict check
    declares its own          one table   formats     row-level, │                    ─► INSERT new rows
    count + total)                                    duplicates │
                                                      control    ├─► rejected_records (quarantine + reasons)
                                                      totals     │
                                                                 ├─► load_runs        (one row per run)
                                                                 └─► reports/rejects_run_N.csv (error report)

 REST API (FastAPI) ─── reads load_runs / rejected_records / transactions, and can trigger a run
```

**One run, in words:**
1. Read every `.csv` and `.json` in the data folder as text into one big table. Remember where each row came from.
2. Normalise each field into a proper type (dates, Decimals, uppercase currency...). Unparseable values become "nothing".
3. Check each row alone. Then check rows against each other (duplicates, conflicts). Then check each file's totals.
4. Open one database transaction. Create a run record. Copy the surviving rows into a temporary table.
5. In SQL, compare them to what's already stored: identical → skip; different → reject; unseen → insert.
6. Store all rejects, update the run's counts, commit. Write the CSV error report.

### Project map

| File | Lines | Job |
|---|---|---|
| `schema.sql` | 56 | Tables, constraints, indexes |
| `fin_etl/config.py` | 15 | Settings (DB URL, folders, allowed currencies) |
| `fin_etl/extract.py` | 130 | Read CSV/JSON → one DataFrame |
| `fin_etl/transform.py` | 93 | Parse/normalise fields |
| `fin_etl/validate.py` | 146 | Python validation + control totals |
| `fin_etl/load.py` | 160 | Staging, SQL validation, insert, run record |
| `fin_etl/report.py` | 35 | Error report CSV |
| `fin_etl/pipeline.py` | 91 | Glue + command line |
| `fin_etl/api.py` | 93 | REST API |
| `generate_sample_data.py` | 123 | Makes the synthetic data with known defects |
| `tests/` | 306 | 56 tests |

---

## Part 3 — The code, file by file

Open each file as you read its section.

### 3.1 `extract.py` — getting data in

**Job:** turn two different file formats into one table with the same seven columns:
`txn_id, account_id, txn_date, amount, currency, txn_type, description`.

**Decision 1: read everything as text.**
```python
pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8-sig", on_bad_lines="error")
```
- `dtype=str`: don't let pandas guess types. Guessing would turn `-75.10` into a float (binary, inexact), strip leading
  zeros from IDs, and so on. We want the original text, then we convert on purpose in `transform.py`.
- `keep_default_na=False`: pandas treats text like `NA` or `null` as missing by default. A description of "NA" is not
  missing data. Turn that off.
- `encoding="utf-8-sig"`: Excel often writes a hidden BOM marker at the start of CSV files; this handles it.
- `on_bad_lines="error"`: a row with the wrong number of fields makes the *file* fail loudly instead of being
  silently skipped. Silent dropping is exactly what this project is trying to avoid.

**Decision 2: file-level problems vs record-level problems.**
A file with missing columns or invalid JSON raises `ExtractError` and the whole run stops (exit code 1; nothing is
written to the database). A single bad *record* inside a good file is handled later by quarantining it. Reasoning: if
the file's structure is wrong, we can't trust any row in it; if one row is bad, the others are still fine.

**Decision 3: JSON numbers never become floats.**
```python
doc = json.load(fh, parse_float=Decimal)
```
`json.load` normally turns `125.50` into the float `125.5`. With `parse_float=Decimal` it becomes `Decimal("125.50")`.
This is the detail that makes "we never use floats for money" actually true instead of mostly true.

**The JSON feed differs on purpose.** It uses other field names (`transactionId`, `accountNumber`, `postedAt`,
`currencyCode`, `direction`, `memo`) — `JSON_FIELD_MAP` is the entire mapping to the canonical names. It uses
`D`/`C` instead of `DEBIT`/`CREDIT`, ISO datetimes instead of dates, and sometimes numeric amounts, sometimes strings.
This is the "consistent schema" work: two different shapes in, one shape out.

**The envelope.** The JSON file is `{"batch_id", "record_count", "control_total", "records": [...]}`. The declared
count and total are saved in a `BatchMeta` object and checked later (Part 6, control totals).

**Row identity.** After reading, every row gets:
- `_source` — which file it came from
- `_row` — its 1-based position in that file (CSV: data rows, header not counted; JSON: position in `records`)
- `_raw` — the original text values, kept for the error report

That is how a reject says "transactions_a.csv, row 46" and how someone can go look at the exact line.

**`read_sources`** reads files sorted by name, so order is deterministic. This matters: "keep the first duplicate"
only makes sense if "first" is well defined. (`transactions_a.csv` sorts before `transactions_b.json`.)

**If asked — "How would you handle a 10 GB file?"** `read_csv` loads everything in memory. For big files, read in
chunks (`chunksize=`) and process each chunk, or push the file into Postgres with `COPY` directly and validate in SQL.
That is a known limit of this design (Part 10).

### 3.2 `transform.py` — making it consistent

**Job:** convert each text field to a proper typed value. **It never rejects anything.** If a value can't be parsed it
becomes `None`, and `validate.py` decides whether that is "missing" or "invalid". Splitting the work this way keeps each
file doing one thing.

| Field | Rule |
|---|---|
| `txn_id` | trim whitespace |
| `account_id` | trim, uppercase (`acc-002` → `ACC-002`) |
| `txn_date` | ISO 8601 only: `YYYY-MM-DD` or a datetime like `2025-02-11T09:30:00Z`. Take the calendar date *as written*. |
| `amount` | exact `Decimal`, at most 2 decimal places, within the range the DB column holds |
| `currency` | trim, uppercase (` inr ` → `INR`) |
| `txn_type` | `D`/`DR`/`DEBIT` → `DEBIT`; `C`/`CR`/`CREDIT` → `CREDIT`; case-insensitive |
| `description` | trim; empty → `None` |

**Decisions worth being able to defend:**

1. **Dates: strict ISO only.** `10/03/2025` is rejected. It's ambiguous (10 March in India/UK, 3 October in the US).
   Guessing wrong silently corrupts data; rejecting is visible and fixable. A regex checks the *shape*, then
   `date.fromisoformat` checks the *calendar* (so `2025-02-30` and `2025-13-45` fail).
2. **Timezones: ignored on purpose.** `2025-02-11T23:30:00-05:00` becomes `2025-02-11`. We store a calendar date,
   not an instant. Converting to UTC could shift the date, and "which day did this post on" is usually the business
   question. Known simplification.
3. **Amounts: never round.** `-10.555` is rejected, not rounded to `-10.56`. Rounding money without being told to
   is how cents go missing. The check uses `d.normalize()` so `100.500` (trailing zero) is fine but `100.505` is not.
4. **Amounts: allowed shapes only.** The regex `^[+-]?\d+(\.\d+)?$` rejects `1,250.50`, `1e3`, `NaN`, `Infinity`.
   Commas are ambiguous between countries (`1.250,50` in Germany) so we don't try.
5. **Range:** `NUMERIC(18,2)` holds up to 16 digits before the point. Anything bigger would make the *database* error
   and abort the load, so it's caught here first and quarantined instead.
6. **Messy-but-valid rows are accepted** (`" inr "`, `"credit"`, `"acc-002"`, `"-75"`). Cleaning is not rejecting.
   Rows `T-1041` and `T-1042` in the sample are deliberately like this, and you can see them cleaned in the DB.

**Pandas detail:** `_apply` builds each column from a list and forces `dtype=object`. Without that, pandas would try
to be clever and convert `None` to `NaN` or dates to timestamps, which would break the `Decimal`/`date` handling.

### 3.3 `validate.py` — the Python half of validation

**Job:** split the rows into `valid` and `rejects`. Three layers.

**Layer 1: row-level** — each row looked at alone. Nested helpers: `_blank` ("was the original text empty?") and
`unparseable` ("there was text, but transform couldn't parse it"). That difference is how we tell:
- `MISSING_FIELD`: nothing was there → `amount is empty`
- `INVALID_*`: something was there but wrong → `amount is not a valid number...`

| Code | Triggers when |
|---|---|
| `MISSING_FIELD` | any required field is blank (one error per missing field) |
| `INVALID_DATE` | not an ISO date, or not a real calendar date |
| `FUTURE_DATE` | date is after today |
| `INVALID_AMOUNT` | not a valid number, more than 2 decimals, out of range, or zero |
| `INVALID_CURRENCY` | not in `ALLOWED_CURRENCIES` (INR, USD, EUR, GBP) |
| `INVALID_TXN_TYPE` | not DEBIT/CREDIT/D/C/DR/CR |
| `SIGN_TYPE_MISMATCH` | CREDIT with negative amount, or DEBIT with positive amount |

A row can fail several rules at once (`T-2012` has a bad date *and* a bad amount) and gets all its error codes.
The `flag(mask, code, detail)` helper takes a True/False mask over all rows and records an error for each True.
That's how pandas is used "properly": one rule applied to the whole column at once instead of a Python loop per row.

**Layer 2: batch-level** — comparing rows with each other. Only rows that passed layer 1 take part (a garbage row
must not make a good row look like a conflict).
```python
for txn_id, group in clean.groupby("txn_id", sort=False):
```
For each `txn_id` appearing more than once, build the "business key" of each copy: `(account_id, txn_date, amount,
currency, txn_type)`.
- All copies identical → `DUPLICATE_IN_BATCH`: **keep the first, reject the rest.** (The same transaction arriving
  twice is normal, e.g. in both feeds; one copy is enough.)
- Copies differ → `CONFLICTING_DUPLICATE`: **reject all of them.** Two sources disagree about the same transaction ID;
  nothing in the data says which is right, so a human has to decide. Loading either one would be a guess.

`description` is deliberately *not* in the business key. Free text differing slightly isn't a financial disagreement.

**Layer 3: control totals** (`check_batches`). Separate function. If a file declared a record count or a total,
recompute both and compare. A mismatch becomes a **warning about the file**, stored in `load_runs.batch_warnings`
and printed, not a rejected record. In the sample, `transactions_b.json` claims a total of 12291.07 but its amounts sum
to 12191.07 (off by exactly 100.00, planted on purpose).

**What comes out:** `(valid_dataframe, list_of_reject_dicts)`. Each reject dict holds the source file, row number,
txn_id, error codes, human-readable details, and the original raw values.

### 3.4 `load.py` — the SQL half, and writing to the database

**Job:** do the database work in one transaction. This is the most important file for interviews. Read it twice.

```python
with conn:                      # psycopg2: commit if the block succeeds, roll back on ANY exception
    with conn.cursor() as cur:
        ...
```

**Step by step:**

1. **Create the run.** `INSERT INTO load_runs ... RETURNING run_id`. Every loaded and rejected row will point back
   to this run.
2. **Make a temporary staging table** `stg` (`ON COMMIT DROP`: vanishes when the transaction ends). It has no
   primary key and no constraints; it's just a holding area.
3. **Bulk copy the valid rows into it** with `COPY ... FROM STDIN` (`cur.copy_expert`). `COPY` is Postgres's fast bulk
   path: one operation for the whole batch instead of one `INSERT` per row. We build a CSV in memory (`io.StringIO`)
   and stream it in.
4. **SQL validation — conflicts with existing data:**
   ```sql
   SELECT ... FROM stg s JOIN transactions t ON t.txn_id = s.txn_id
   WHERE (s.account_id, s.txn_date, s.amount, s.currency, s.txn_type)
         IS DISTINCT FROM (t.account_id, t.txn_date, t.amount, t.currency, t.txn_type)
   ```
   Same `txn_id` already stored, different values → `CONFLICT_WITH_DB`. The detail says exactly what differs
   (`amount: incoming -65.56 vs stored -70.56`). **We never overwrite stored data automatically.**
   `IS DISTINCT FROM` is "not equal" that treats NULL properly (plain `<>` returns NULL if either side is NULL, which
   would silently skip the row). It compares whole rows at once.
5. **SQL — identical rows:** same query with `IS NOT DISTINCT FROM`, counted as `skipped`. This is the idempotency.
6. **Insert only the new rows:**
   ```sql
   INSERT INTO transactions (...) SELECT ... FROM stg s
   WHERE NOT EXISTS (SELECT 1 FROM transactions t WHERE t.txn_id = s.txn_id)
   ON CONFLICT (txn_id) DO NOTHING
   ```
   `NOT EXISTS` already excludes existing IDs. `ON CONFLICT DO NOTHING` is a belt-and-braces safety net: if another run
   inserted the same ID a millisecond ago, the database refuses the duplicate instead of erroring.
7. **Row accounting self-check.** Every valid row must be exactly one of loaded / skipped / conflict, and every row read
   must be either valid or rejected. If the numbers don't add up, it raises an error and the whole transaction rolls
   back. A pipeline that can lose a row without noticing is the worst kind; this makes it noisy instead.
8. **Store rejects** (`execute_values` bulk insert, raw record saved as `JSONB`), and **update the run** with final counts.

**Why one transaction for everything?** Atomicity. If step 8 crashes after step 6 inserted rows, without a
transaction you'd have loaded data with no run record and no rejects: half a run. With one, it's all or nothing. A
test proves it (`test_failure_mid_load_rolls_everything_back`): it crashes the reject insert on purpose and checks
that zero transactions and zero run records survive.

**Why a staging table + SQL, not just Python loops?** Comparing 100,000 incoming rows with the database one query at
a time is slow. One set-based SQL join does it in a single statement. Also, this is the real meaning of "SQL-based
validation": checks that need the database's data are done *by* the database.

**A pandas gotcha in this file:** `itertuples` silently renames columns that start with `_` (like `_source`), so
`load.py` uses `to_dict("records")` instead. A real pitfall like this is a good small detail to mention.

### 3.5 `report.py` — error reports

`write_reject_report` writes `reports/rejects_run_<id>.csv`: one line per failed record with run id, file, row number,
txn_id, error codes (joined with `|`), human-readable details, and the raw record as JSON. Sorted by file then row.
The same information is in the `rejected_records` table (and via the API). The CSV is for people who want to open it in
Excel; the table is for queries.

`reject_counts` counts rejected **rows** per code. A row with two problems counts under both codes, which is why the
code counts in the sample sum to 21 while 20 rows were rejected. Know this; it's a classic "why don't the numbers add
up" question.

### 3.6 `pipeline.py` — the glue

`run()` is five lines of real logic: `read_sources` → `normalize` → `validate` → `check_batches` → `load`, then write
the report. `main()` is the command line (`--init-db`, `--data-dir`). Exit code `1` on file-level failure, `0`
otherwise — **rejects are a normal outcome, not a failure**; a run that quarantines 20 rows still succeeded.

`today` and `report_dir` are parameters so tests can control them. That's the reason a "FUTURE_DATE" test doesn't break
next year.

### 3.7 `api.py` — the REST API

| Method | Path | Does |
|---|---|---|
| GET | `/health` | runs `SELECT 1`; proves the DB connection works |
| GET | `/runs` | run history, newest first |
| GET | `/runs/{run_id}` | counts and batch warnings; 404 if absent |
| GET | `/runs/{run_id}/rejects` | the error report; optional `?error_code=`; paginated |
| GET | `/transactions` | loaded data; optional `?account_id=`; paginated |
| POST | `/runs` | triggers the pipeline on the configured folder; returns 201 and the summary |

Points to know:
- **REST basics:** resources are nouns (`/runs`), HTTP methods are verbs (GET reads, POST creates), status codes mean
  things (200 OK, 201 Created, 404 Not Found, 422 invalid input).
- **Money goes out as strings** (`"-125.50"`) via `amount::text`. JSON numbers get parsed as floats by most clients
  (JavaScript always does). Same reason the DB uses NUMERIC.
- **SQL injection:** every query uses `%s` placeholders with a parameter tuple, never string formatting of user input.
- **Pagination:** `limit` and `offset`, with caps, so one request can't ask for a million rows.
- **FastAPI gives free docs** at `/docs` (interactive). Use them live in an interview.
- `POST /runs` is synchronous: the request waits while the pipeline runs. Fine for small data; a real system would queue
  the job and return immediately.
- It is a *read* API plus a trigger. The pipeline does not call an external API. If asked "what REST APIs did you
  use", the accurate answer is "I built one with FastAPI to expose the results and trigger a run".

---

## Part 4 — The database

### 4.1 `schema.sql` table by table

**`load_runs`** — one row per execution: when it started/finished, which files, counts (read / loaded / skipped /
rejected), and `batch_warnings` as JSON. `BIGSERIAL` = auto-incrementing 64-bit id. `TIMESTAMPTZ` = timestamp with
timezone (always prefer it over plain `TIMESTAMP`). `TEXT[]` = array of text. `JSONB` = binary JSON, queryable.

**`transactions`** — the clean data.
- `txn_id TEXT PRIMARY KEY` — uniqueness enforced by the database itself. This also creates an index.
- `amount NUMERIC(18,2)` — exact decimal: up to 18 digits total, exactly 2 after the point.
- `currency CHAR(3)` — fixed-length 3-letter code.
- `run_id ... REFERENCES load_runs(run_id)` — a **foreign key**: you cannot have a transaction pointing at a run that
  doesn't exist. This is what makes lineage ("which run loaded this?") trustworthy.
- Four `CHECK` constraints: currency is 3 capital letters; type is DEBIT or CREDIT; amount is not zero; sign matches
  type. These duplicate rules already in Python **on purpose** (defence in depth). `test_database_constraints_are_a_real_backstop`
  proves the database rejects a bad row even if Python was skipped.
- `idx_transactions_account_date` on `(account_id, txn_date)` — the index for the most likely query ("this account's
  transactions in a date range"). Only index what you query; every index slows writes slightly.

**`rejected_records`** — the quarantine. `source_row`, `error_codes TEXT[]`, `error_details`, `raw_record JSONB` (the
values exactly as they arrived). `txn_id` is nullable because the id itself may be the missing field. Indexed by `run_id`.

### 4.2 Idempotent schema
Every statement is `CREATE ... IF NOT EXISTS`, so `--init-db` can be run any number of times. (This is not a real
migration system; see limitations.)

### 4.3 Why NUMERIC and never float (they WILL ask)
Computers store floats in binary. Some decimal fractions, like 0.1, have no exact binary form, so
`0.1 + 0.2 = 0.30000000000000004` in most languages. Across millions of transactions, tiny errors accumulate and
reconciliation fails by a few paise. `NUMERIC` (Postgres) and `Decimal` (Python) store decimal digits exactly.
`test_no_float_precision_loss` demonstrates this.

### 4.4 Transactions, in one paragraph you can say out loud
"The load runs inside a single database transaction. psycopg2's `with conn` block commits on success and rolls back
on any exception, so a failure at any point leaves the database exactly as it was. That includes the run record, so a
crashed run leaves no half-loaded data."

### 4.5 Psycopg2 notes
`psycopg2` is the Python driver for Postgres. `cur.execute(sql, params)` with `%s` placeholders sends values separately
from the SQL (prevents injection). `with conn:` manages the transaction but does **not** close the connection; the
pipeline closes it in a `finally`.

---

## Part 5 — Python vs SQL: who validates what, and why

| Check | Where | Why there |
|---|---|---|
| Missing / invalid fields, future dates, sign vs type | Python (`validate.py`) | Needs parsing logic and produces specific, friendly error messages; cheap to test without a database. |
| Duplicates and conflicts inside the batch | Python (pandas `groupby`) | The data is already in memory; the rule is about the incoming batch only. |
| Control totals | Python | Compares file-level claims with recomputed values. |
| **Conflict with already-stored rows** | **SQL** (`load.py`) | Needs the database's data. One set-based join beats fetching rows to Python. |
| Idempotent skip of identical rows | **SQL** | Same join. |
| Final guarantees (not null, check, primary key, foreign key) | **SQL** (`schema.sql`) | Last line of defence; holds even if someone writes to the table from another program. |

**Rule of thumb you can state:** *row-level logic in Python, set-based comparisons against stored data in SQL, hard
invariants as database constraints.*

---

## Part 6 — "Duplicates", "conflicts" and "mismatches": exact definitions

"Data mismatches" is the vaguest phrase on your resume. If you can't say what it means concretely, that's the line
they'll pull. These are the four things in this project that count as mismatches:

| # | Mismatch | Code | Example in the sample data |
|---|---|---|---|
| 1 | A record disagrees with itself: the sign of the amount contradicts the debit/credit type | `SIGN_TYPE_MISMATCH` | `T-2011`: DEBIT but `+250.00` |
| 2 | Two incoming records with the same ID disagree on values (possibly from different files) | `CONFLICTING_DUPLICATE` | `T-1020` is `-1792.06` in the CSV (row 20) and `-1791.06` in the JSON (row 22) → both rejected |
| 3 | An incoming record disagrees with what is already stored | `CONFLICT_WITH_DB` | Run 3 on `data_extra`: `T-1001` arrives with a different amount |
| 4 | A file disagrees with its own declared totals | warning `CONTROL_TOTAL_MISMATCH` / `RECORD_COUNT_MISMATCH` | JSON declares 12291.07, actual 12191.07 |

And **duplicates**, strictly:
- *Exact duplicate*: same `txn_id`, same business values → keep first, reject the rest (`DUPLICATE_IN_BATCH`),
  e.g. `T-1005` repeated at the end of the CSV, and `T-1010` appearing in both files.
- *Against the database*: same `txn_id`, same values as stored → **skipped**, not an error (that's idempotency).
- Same `txn_id`, different values → not a duplicate, a *conflict* (rows 2 and 3 above).

**Why conflicts aren't resolved automatically** ("latest wins" / "database wins"): in finance, silently picking a
winner is how errors go unnoticed. Quarantining forces a person to decide. Alternative designs exist (versioned
records, "last writer wins" with an audit log) and are fine if you can explain the tradeoff.

---

## Part 7 — A real run, line by line

The sample data (`generate_sample_data.py`, seeded so it never changes):

**`transactions_a.csv` (56 rows)**
| Rows | Content | Result |
|---|---|---|
| 1–40 | `T-1001`…`T-1040`, clean | loaded (except row 20, see below) |
| 41–42 | `T-1041`, `T-1042`: messy but valid (` inr `, `credit`, `acc-002`, `-75`) | cleaned and loaded |
| 43 | `T-2001` amount empty | `MISSING_FIELD` |
| 44 | `T-2002` account empty | `MISSING_FIELD` |
| 45 | txn_id empty | `MISSING_FIELD` |
| 46 | `T-2003` date `2025-13-45` | `INVALID_DATE` |
| 47 | `T-2004` date `10/03/2025` | `INVALID_DATE` |
| 48 | `T-2005` date `2099-01-01` | `FUTURE_DATE` |
| 49 | `T-2006` amount `12abc` | `INVALID_AMOUNT` |
| 50 | `T-2007` amount `0.00` | `INVALID_AMOUNT` |
| 51 | `T-2008` amount `-10.555` | `INVALID_AMOUNT` |
| 52 | `T-2009` currency `XXX` | `INVALID_CURRENCY` |
| 53 | `T-2010` type `TRANSFER` | `INVALID_TXN_TYPE` |
| 54 | `T-2011` DEBIT with `+250.00` | `SIGN_TYPE_MISMATCH` |
| 55 | `T-2012` date `2025-02-30` and amount `abc` | `INVALID_DATE` + `INVALID_AMOUNT` |
| 56 | exact copy of `T-1005` | `DUPLICATE_IN_BATCH` |
| 20 | `T-1020` (conflicts with the JSON copy) | `CONFLICTING_DUPLICATE` |

**`transactions_b.json` (25 records)**
| Rows | Content | Result |
|---|---|---|
| 1–20 | `T-3001`…`T-3020`, different field names, D/C, ISO datetimes, mixed number/string amounts | loaded |
| 21 | copy of `T-1010` from the CSV | `DUPLICATE_IN_BATCH` |
| 22 | `T-1020` with amount +1.00 | `CONFLICTING_DUPLICATE` |
| 23 | `T-3901` amount `null` | `MISSING_FIELD` |
| 24 | `T-3902` direction `X` | `INVALID_TXN_TYPE` |
| 25 | `T-3903` date `yesterday` | `INVALID_DATE` |

**Totals (first run, verified by running it):**
```
rows read 81 = rows loaded 61 + rows skipped 0 + rows rejected 20
```
- CSV: 15 rejected (row 20 plus rows 43–56). JSON: 5 rejected. 15 + 5 = 20.
- Loaded: 41 from the CSV (42 valid IDs minus `T-1020`) + 20 from the JSON = 61.
- Error-code counts: MISSING_FIELD 4, INVALID_DATE 4, INVALID_AMOUNT 4, INVALID_TXN_TYPE 2, DUPLICATE_IN_BATCH 2,
  CONFLICTING_DUPLICATE 2, FUTURE_DATE 1, INVALID_CURRENCY 1, SIGN_TYPE_MISMATCH 1 → 21 code-instances across 20 rows
  because `T-2012` has two.
- Batch warning: `CONTROL_TOTAL_MISMATCH`, off by 100.00, with a note that 1 row (`T-3901`) had no readable amount.

**Run 2 (same files again):** read 81, loaded **0**, skipped **61**, rejected 20. No duplicates created. Note: the 20
rejects are written *again* under run 2. Rejects belong to the run that found them, so each run has its own audit trail.

**Run 3 (`data_extra/late_arrivals.csv`):** 3 rows. `T-1002` identical → skipped. `T-1001` changed amount →
`CONFLICT_WITH_DB`, and the stored value is **not** overwritten (a test asserts it stays `-70.56`). `T-4001` new → loaded.

---

## Part 8 — The tests (and what each proves)

Run `pytest`. 56 tests, about a second.

| File | Covers |
|---|---|
| `test_transform.py` | every parse rule with good and bad inputs; Decimal exactness; type mapping |
| `test_validate.py` | each row-level rule alone; multi-error rows; duplicate vs conflict; description is not part of identity; invalid row doesn't poison a valid one with the same id; blank ids aren't grouped; control totals |
| `test_pipeline.py` | **needs Postgres.** Sample counts match the planted defects exactly; re-run is idempotent; DB conflicts don't overwrite; a crash rolls everything back; DB constraints reject bad rows directly; a broken file fails loudly with no run record; every API endpoint |

Notes:
- The integration tests use **`fin_etl_test`**, a separate database wiped before each test. They never touch your real data.
- Unit tests need no database at all because validation is pure (a DataFrame in, results out). That's a design benefit.
- I flipped the SQL conflict comparison once to check the tests notice; they failed. A test suite that can't fail is decoration.
- Honest limits: tests are tiny-data. Nothing tests performance or concurrent runs.

---

## Part 9 — Run it yourself, then break it (do this part)

### 9.1 Setup
You need Python 3.10+ and PostgreSQL 14+. Either install Postgres natively, or use Docker:
```bash
docker run -d --name finpg -e POSTGRES_USER=fin_etl -e POSTGRES_PASSWORD=fin_etl \
  -e POSTGRES_DB=fin_etl -p 5432:5432 postgres:16
docker exec finpg createdb -U fin_etl fin_etl_test
```
(I ran everything against a native Postgres 16 here; the Docker commands are standard but I did not run them.)

Native install: create the role and databases with the three `psql` lines in `README.md`.
Then:
```bash
pip install -r requirements.txt
python -m fin_etl.pipeline --init-db
pytest
```
Look at the data like a data engineer would:
```bash
psql postgresql://fin_etl:fin_etl@localhost:5432/fin_etl
```
```sql
SELECT count(*) FROM transactions;
SELECT * FROM load_runs ORDER BY run_id;
SELECT error_codes, count(*) FROM rejected_records WHERE run_id = 1 GROUP BY 1 ORDER BY 2 DESC;
SELECT txn_id, error_details FROM rejected_records WHERE run_id = 1 AND 'INVALID_AMOUNT' = ANY(error_codes);
SELECT account_id, currency, sum(amount) FROM transactions GROUP BY 1, 2 ORDER BY 1, 2;
```

### 9.2 Experiments (each takes a minute; predict the result *before* running)

1. **Run it twice.** Expect loaded 0, skipped 61. Why? (Idempotency: Part 3.4 step 5.)
2. **Edit the CSV:** change the amount of `T-1003` in `data/transactions_a.csv` (for example `2749.51` → `2749.52`) and
   re-run. Expect loaded 0, skipped 60, rejected 21, with one `CONFLICT_WITH_DB`. The stored value stays unchanged.
   (Put the file back afterwards, or run `python generate_sample_data.py` to regenerate it.)
3. **Add a currency:** append `T-4100,ACC-001,2025-03-21,500.00,JPY,CREDIT,Yen` to `data_extra/late_arrivals.csv` and run
   `python -m fin_etl.pipeline --data-dir data_extra`. Expect an `INVALID_CURRENCY` reject for `T-4100`. Now add `"JPY"` to
   `ALLOWED_CURRENCIES` in `config.py` and run again: `T-4100` loads. (Allowlist as configuration.)
4. **Break a file:** rename the `amount` header to `amt` in the CSV. Expect "missing required columns ['amount']", exit
   code 1, and **no new run** in `load_runs`. (File-level failure vs record-level.)
5. **Make the database say no:** in `psql`, try
   `INSERT INTO transactions (txn_id,account_id,txn_date,amount,currency,txn_type,source_file,run_id) VALUES ('X','A','2025-01-01',10,'INR','DEBIT','f',1);`
   It fails the `chk_sign_matches_type` constraint. That is the backstop working.
6. **Crash it:** in `load.py`, add `raise RuntimeError("boom")` just before the `UPDATE load_runs` statement. Run it. Check
   `SELECT count(*) FROM transactions` did not change. (Atomicity.) Remove the line afterwards.
7. **Use the API:** `uvicorn fin_etl.api:app --reload`, open `http://127.0.0.1:8000/docs`, call `POST /runs`, then
   `GET /runs/{id}/rejects?error_code=MISSING_FIELD`.
8. **Float vs Decimal:** `python -c "print(0.1+0.2); from decimal import Decimal as D; print(D('0.1')+D('0.2'))"`.

If you can predict all eight outcomes before running them, you understand the project.

---

## Part 10 — Design decisions, tradeoffs, and honest limitations

### 10.1 Decisions

| Decision | Why | Alternative | When the alternative wins |
|---|---|---|---|
| Plain Python script, no orchestrator | Small scale; fewer moving parts to explain and run | Airflow / Dagster / cron + monitoring | Many pipelines, schedules, dependencies, retries, alerting |
| pandas for in-memory processing | Vectorised rules, groupby for duplicates, readable | Plain Python / csv module; Spark | Plain: tiny data. Spark: data too big for one machine |
| Read everything as text | No silent type guessing | Let pandas infer | Never, for money |
| Decimal / NUMERIC | Exact money | float | Never, for money |
| Hand-written validators | 7 rules; full control of messages | pydantic / pandera / Great Expectations | More rules, shared schemas, declarative rule files, data docs |
| Staging table + `COPY` + SQL join | Fast, set-based, one round-trip | `INSERT` per row from Python | Very small loads where simplicity beats speed |
| Quarantine rejects, never drop | Auditability | Drop or fail the whole batch | Fail-whole-batch when partial loads are unacceptable (e.g. a ledger that must balance) |
| Conflicts rejected, not overwritten | No silent data change | Upsert (`ON CONFLICT DO UPDATE`) | Source is authoritative and corrections are expected; keep history in an audit table |
| One DB transaction per run | All-or-nothing | Commit per chunk | Huge loads where one giant transaction is too heavy |
| `NOT EXISTS` + `ON CONFLICT DO NOTHING` | Explicit check plus race safety | Only one of them | — |
| Control totals warn, don't block | Reconciliation findings are surfaced without halting everything | Quarantine the whole file | When a mismatch means the file is untrustworthy (often true in real finance) |

### 10.2 Known limitations (say these first; it reads as maturity)
- **Scale:** whole files load into memory. Fine for megabytes, not tens of gigabytes. Fix: chunked reads or load raw
  into Postgres and validate in SQL.
- **Re-reads every file every run.** Idempotency makes that safe but wasteful. Real systems track processed files
  (name + checksum), or archive them after loading.
- **Not tested under concurrent runs.** The primary key prevents duplicate rows, and I believe the row-accounting check
  would fail loudly if two runs raced, but I have not tested that.
- **Failed runs leave no trace in the database** (the run record rolls back with everything else). Only the exception /
  exit code tells you. A real system logs failures somewhere durable.
- **Rejects are re-recorded on every re-run** of the same files (by design, per-run audit trail), so the table grows.
- **No account validation:** `account_id` isn't checked against an accounts table. "Invalid values" means malformed
  values, not unknown customers.
- **Control totals sum across currencies** in the sample, which is financially meaningless. A real control total is per
  currency. It demonstrates the mechanism only.
- **No currency conversion**, no per-currency reporting.
- **Dates:** ISO only, timezone ignored.
- **Hard-coded allowlist** of four currencies in `config.py`.
- **Default DB credentials** in `config.py` are for local development only.
- **No structured logging, metrics, alerting or scheduler.**
- **No real migrations:** `schema.sql` is create-if-missing, so changing a column later needs a migration tool
  (Alembic, Flyway).
- **Synthetic data:** real data is messier in ways nobody plants on purpose.

### 10.3 What you would do next (only say what you could actually explain)
Per-currency control totals; file tracking by checksum; chunked processing; structured logging with run ids; a migration
tool; an `accounts` table with a foreign key; schema validation with pydantic or pandera; retries and a scheduler.

---

## Part 11 — Things you have NOT done (say so plainly if asked)
Airflow, dbt, Spark, Kafka/streaming, cloud warehouses (BigQuery, Snowflake, Redshift), CDC, Docker/Kubernetes
deployment of this project, CI, performance tuning at scale, production monitoring. If an interviewer asks about one,
"I haven't used that in this project, here's how I understand it fits, and here's what I'd compare it to" is a strong,
honest answer. "Yes" followed by silence is not.

---

## Part 12 — Interview Q&A bank

Answers are skeletons. Say them in your own words.

### The opener
**"Walk me through this project."** (≈60 seconds)
> It's a small ETL pipeline I built to practise data validation. It reads transactions from two feeds, a CSV and a JSON
> file with different field names and formats, normalises them to one schema, and validates them. Bad records are
> quarantined with the reason and exported as an error report; good ones go into PostgreSQL in a single transaction.
> I used synthetic data with defects I planted deliberately, so I can check the exact counts: 81 rows in, 61 loaded, 20
> rejected. Re-running it is idempotent, and there's a small FastAPI layer to query results. The parts I care most about
> are not using floats for money, never dropping a record silently, and making the load atomic.

**30-second version:** "A batch ETL pipeline in Python and Postgres. It normalises CSV and JSON transaction feeds,
validates them in Python and SQL, quarantines failures with reasons, and loads the rest atomically and idempotently.
It's a personal project on synthetic data."

**"Was this at a company / on real data?"** "No, it's a personal project on synthetic data I generated, with known
defects planted so I could test the validation exactly."

### ETL and design
**Why not Airflow / dbt / Spark?** The scale (tens of rows to maybe thousands) doesn't justify them. A script is easier
to reason about. They'd win with many pipelines, scheduling, retries, or data too big for one machine. I haven't used
them on this project.

**Batch or streaming? Incremental or full?** Batch. Each run reads every file in the folder, but idempotency makes
re-reading safe. Real incremental loading would track processed files by checksum.

**How is it triggered?** From the command line or `POST /runs`. No scheduler; cron would be the simplest addition.

**What happens if it crashes halfway?** One database transaction wraps the load, so the database is left unchanged. A
test simulates the crash.

**How do you make it idempotent?** Primary key on `txn_id`, a SQL check that skips identical rows and rejects
conflicting ones, and `ON CONFLICT DO NOTHING` as a safety net. Re-running loads 0 and skips 61.

### Ingestion and transformation
**How do you read the files?** `pd.read_csv` with `dtype=str` and `keep_default_na=False`; `json.load` with
`parse_float=Decimal`. Everything stays text until I convert it deliberately.

**What about a bad file?** Structural problems (missing columns, invalid JSON, wrong field counts) fail the run loudly.
Bad individual records are quarantined.

**What inconsistencies did the sources have?** Field names, debit/credit encoding (`DEBIT` vs `D`), dates vs ISO
datetimes, numeric vs string amounts, casing and whitespace in currency and account.

**Why is `10/03/2025` rejected?** It's ambiguous between DD/MM and MM/DD. A wrong guess silently corrupts data; a
rejection is visible.

**Why Decimal and NUMERIC, not float?** Binary floats can't represent most decimal fractions exactly
(`0.1 + 0.2 = 0.30000000000000004`). Money needs exact arithmetic.

**Why not round `-10.555`?** Rounding money without a rule is silently changing data. Rejecting makes someone decide.

**Where does the schema mapping live?** `JSON_FIELD_MAP` in `extract.py` (names) and `transform.py` (values).

### Loading and Postgres
**Table design?** `transactions` with `txn_id` as primary key, `NUMERIC(18,2)` amount, four CHECK constraints, a foreign
key to `load_runs`, and an index on `(account_id, txn_date)`. Plus `rejected_records` and `load_runs`.

**Why `COPY` and a staging table?** `COPY` bulk-loads far faster than row-by-row inserts, and a staging table lets one
set-based SQL query compare the whole batch with stored data.

**Why `IS DISTINCT FROM`?** It's a NULL-safe not-equal that works on whole rows. Plain `<>` returns NULL when a value is
NULL and the row would be silently skipped.

**What does `ON CONFLICT DO NOTHING` do?** If an insert would violate the primary key, skip that row instead of erroring.
Here it's a safety net for a concurrent run.

**Is a load atomic?** Yes, a single transaction: run record, inserts, rejects, summary all commit or all roll back.

**Why an index on `(account_id, txn_date)`?** The obvious query is "an account's transactions in a date range". I only
indexed what I'd query.

**What's a foreign key doing there?** `transactions.run_id` must exist in `load_runs`: every row is traceable to the
run that loaded it.

### Validation
**What's a duplicate?** Same `txn_id` with identical values. First kept, rest rejected as `DUPLICATE_IN_BATCH`; if it's
already in the DB, it's skipped.

**Duplicate vs conflict?** Conflict = same id, different values. Both copies rejected, because nothing says which is
right. Against the DB, the stored row is kept and the incoming one is rejected.

**What counts as "missing" vs "invalid"?** Missing: original text was empty. Invalid: text present but unparseable or
illegal. Different error codes, different fix.

**Give a concrete "data mismatch".** (Pick one.) "A DEBIT with a positive amount", "the same transaction ID with
different amounts in the CSV and the JSON", or "a JSON file declaring a control total that doesn't match its records".

**What's a control total?** A total the sender declares so the receiver can check completeness. I recompute it and warn
on a mismatch.

**Why warn instead of block on control-total mismatch?** It's a finding about the whole file, not about any single row,
so I surface it as a stored warning rather than a rejected record. It's a judgement call: in real finance I'd likely
quarantine the whole file until someone explains the difference.

**Why validate in both Python and SQL?** Python for row logic and friendly messages; SQL for comparing against stored
data efficiently; constraints as a last guard even if the application has a bug.

**Pandera / pydantic / Great Expectations?** I wrote seven rules by hand to keep it small and understandable. With
many more rules I'd move to a declarative library.

**What do you do with rejected records?** They go to `rejected_records` (queryable) and to a CSV with the reason and the
original values. Nothing is dropped.

### Error reporting and API
**What's in an error report row?** Run id, file, row number, txn_id, error codes, details, raw record as JSON.

**Why do reject counts add to more than the rows rejected?** A row can fail several rules and counts under each code.
`T-2012` has a bad date and a bad amount.

**What does your API do?** It exposes runs, rejects and transactions, and `POST /runs` triggers the pipeline. Amounts are
strings to avoid float parsing in clients.

**Is it really REST?** It follows the basics: resource URLs, HTTP verbs, status codes, pagination. It's a small API;
I wouldn't claim more.

### Hard questions
**How would you scale it?** Chunked reads; load raw rows with `COPY` and validate in SQL; partition the table by date;
move orchestration to Airflow/Dagster; add metrics and alerts.

**What would you change if this were a ledger that must balance?** Fail the whole batch on any mismatch, rather than a
partial load; per-currency control totals; immutable records with corrections as new entries.

**What are its weaknesses?** (Say 3 from Part 10.2, unprompted.)

**What did you learn / hardest part?** Choose honestly. Candidates: realising that "validation" needs definitions
(duplicate vs conflict), making the load idempotent and atomic, and avoiding floats end to end including JSON parsing.

**How do you know it works?** 56 tests, including a Postgres integration suite with exact expected counts, a crash test
and a direct constraint test. I also broke the SQL check on purpose to confirm the tests notice.

### Questions where "I don't know / didn't do that" is the right answer
- "How many records a day does it process?" → It's a personal project on a few dozen rows.
- "What broke in production?" → It hasn't been in production.
- "Did you use Airflow / dbt / Spark?" → No, not in this project.

---

## Part 13 — One-page cheat sheet (re-read before an interview)

**Pitch:** batch ETL, Python + Postgres; CSV + JSON → one schema → validate (Python + SQL) → quarantine bad rows with
reasons → atomic, idempotent load; personal project, synthetic data.

**Numbers:** 56 tests · 81 read = 61 loaded + 20 rejected · re-run: 0 loaded, 61 skipped · 9 reject codes in the first
run (+ `CONFLICT_WITH_DB` in a later one) · 1 control-total warning (off by 100.00).

**Pipeline order:** `read_sources → normalize → validate → check_batches → load → write_reject_report`.

**Error codes:** MISSING_FIELD · INVALID_DATE · FUTURE_DATE · INVALID_AMOUNT · INVALID_CURRENCY · INVALID_TXN_TYPE ·
SIGN_TYPE_MISMATCH · DUPLICATE_IN_BATCH · CONFLICTING_DUPLICATE · CONFLICT_WITH_DB.

**Rules of the house:** never float for money · never round money · never guess ambiguous dates · never drop a record
silently · never overwrite stored data automatically · one transaction per run · row counts must add up.

**Say first when asked for weaknesses:** whole-file memory, no scheduler, no concurrency testing, failed runs not
recorded, no account validation, control totals not per currency, synthetic data.

**Self-test (answer out loud, no notes):**
1. What does each letter of ETL do here, and which file does it?
2. Why is everything read as text?
3. What's the difference between a duplicate and a conflict, and what happens to each?
4. Why NUMERIC? Why Decimal? Where could a float sneak in, and how is it blocked?
5. What does `IS DISTINCT FROM` do and why use it?
6. What happens if the process dies after the INSERT but before the UPDATE of `load_runs`?
7. Why does re-running load 0 rows?
8. Name four things "mismatch" means in this project.
9. Why do error-code counts sum to 21 when 20 rows were rejected?
10. Name three limitations without looking.
