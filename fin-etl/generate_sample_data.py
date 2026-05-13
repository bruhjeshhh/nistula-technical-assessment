"""Regenerate the synthetic sample data in ./data and ./data_extra.

All records are made up. The files contain clean rows plus deliberately seeded
defects with KNOWN expected outcomes, so tests and demos have a ground truth.
Deterministic: running it twice gives identical files.

    python generate_sample_data.py
"""
import csv
import json
import random
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).parent
rng = random.Random(42)
HEADER = ["txn_id", "account_id", "txn_date", "amount", "currency", "txn_type", "description"]
MEMOS = ["Salary", "Rent", "Groceries", "Utility bill", "Refund", "Transfer in",
         "Fuel", "Insurance", "Subscription", "Dining", "Invoice payment", "Cash deposit"]


def make_row(txn_id, accounts, months=(1, 2, 3)):
    credit = rng.random() < 0.4
    cents = rng.randint(10_000, 500_000) if credit else -rng.randint(500, 200_000)
    return {
        "txn_id": txn_id,
        "account_id": rng.choice(accounts),
        "txn_date": f"2025-{rng.choice(months):02d}-{rng.randint(1, 28):02d}",
        "amount": f"{Decimal(cents) / 100:.2f}",
        "currency": "USD" if rng.random() < 0.15 else "INR",
        "txn_type": "CREDIT" if credit else "DEBIT",
        "description": rng.choice(MEMOS),
    }


# ---------------------------------------------------------------- CSV feed
accounts = [f"ACC-{i:03d}" for i in range(1, 6)]
csv_rows = [make_row(f"T-{1000 + i}", accounts) for i in range(1, 41)]  # T-1001..T-1040

# valid but messy: the transform step must clean these, not reject them
csv_rows.append({"txn_id": " T-1041 ", "account_id": "acc-002", "txn_date": "2025-02-14",
                 "amount": "1500.00", "currency": " inr ", "txn_type": "credit",
                 "description": "  Messy but valid  "})
csv_rows.append({"txn_id": "T-1042", "account_id": "ACC-004", "txn_date": "2025-03-02",
                 "amount": "-75", "currency": "INR", "txn_type": "Debit", "description": ""})

# seeded defects (comment = expected error code)
defects = [
    ("T-2001", "ACC-001", "2025-03-10", "", "INR", "DEBIT", "Rent"),                # MISSING_FIELD
    ("T-2002", "", "2025-03-10", "-50.00", "INR", "DEBIT", ""),                      # MISSING_FIELD
    ("", "ACC-002", "2025-03-11", "100.00", "USD", "CREDIT", "Refund"),              # MISSING_FIELD
    ("T-2003", "ACC-001", "2025-13-45", "-10.00", "INR", "DEBIT", "Bad date"),       # INVALID_DATE
    ("T-2004", "ACC-001", "10/03/2025", "-10.00", "INR", "DEBIT", "DD/MM date"),     # INVALID_DATE
    ("T-2005", "ACC-003", "2099-01-01", "-10.00", "INR", "DEBIT", "Future"),         # FUTURE_DATE
    ("T-2006", "ACC-003", "2025-03-12", "12abc", "INR", "DEBIT", "Text amount"),     # INVALID_AMOUNT
    ("T-2007", "ACC-003", "2025-03-12", "0.00", "INR", "CREDIT", "Zero"),            # INVALID_AMOUNT
    ("T-2008", "ACC-003", "2025-03-12", "-10.555", "INR", "DEBIT", "3 decimals"),    # INVALID_AMOUNT
    ("T-2009", "ACC-004", "2025-03-12", "-10.00", "XXX", "DEBIT", "Bad currency"),   # INVALID_CURRENCY
    ("T-2010", "ACC-004", "2025-03-12", "-10.00", "INR", "TRANSFER", "Bad type"),    # INVALID_TXN_TYPE
    ("T-2011", "ACC-004", "2025-03-13", "250.00", "INR", "DEBIT", "Sign mismatch"),  # SIGN_TYPE_MISMATCH
    ("T-2012", "ACC-005", "2025-02-30", "abc", "usd", "DEBIT", "Two problems"),      # INVALID_DATE + INVALID_AMOUNT
]
for d in defects:
    csv_rows.append(dict(zip(HEADER, d)))
csv_rows.append(dict(csv_rows[4]))  # exact copy of T-1005                           # DUPLICATE_IN_BATCH

with open(ROOT / "data" / "transactions_a.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=HEADER)
    w.writeheader()
    w.writerows(csv_rows)

# ---------------------------------------------------------------- JSON feed
# Different field names, D/C codes, ISO datetimes, numeric amounts. Wrapped in an
# envelope that declares its own record count and control total.
def to_json_rec(r, as_string=False):
    amount = r["amount"] if as_string else float(r["amount"])
    return {"transactionId": r["txn_id"], "accountNumber": r["account_id"],
            "postedAt": r["txn_date"] + "T09:30:00Z", "amount": amount,
            "currencyCode": r["currency"], "direction": r["txn_type"][0],
            "memo": r["description"]}

json_rows = []
for i in range(1, 21):  # T-3001..T-3020
    json_rows.append(to_json_rec(make_row(f"T-{3000 + i}", accounts), as_string=(i % 4 == 0)))

t1010 = next(r for r in csv_rows if r["txn_id"] == "T-1010")
t1020 = next(r for r in csv_rows if r["txn_id"] == "T-1020")
json_rows.append(to_json_rec(t1010))                                      # DUPLICATE_IN_BATCH (cross-file)
conflict = dict(t1020, amount=f"{Decimal(t1020['amount']) + Decimal('1.00'):.2f}")
json_rows.append(to_json_rec(conflict))                                   # CONFLICTING_DUPLICATE (x2 with CSV row)
bad = make_row("T-3901", accounts)
json_rows.append({**to_json_rec(bad), "amount": None})                    # MISSING_FIELD
bad = make_row("T-3902", accounts)
json_rows.append({**to_json_rec(bad), "direction": "X"})                  # INVALID_TXN_TYPE
bad = make_row("T-3903", accounts)
json_rows.append({**to_json_rec(bad), "postedAt": "yesterday"})           # INVALID_DATE

true_total = sum((Decimal(str(r["amount"])) for r in json_rows if r["amount"] is not None),
                 Decimal("0"))
envelope = {
    "batch_id": "B-2025-03-A",
    "record_count": len(json_rows),                     # correct
    "control_total": float(true_total + Decimal("100.00")),  # deliberately wrong by 100.00
    "records": json_rows,
}
with open(ROOT / "data" / "transactions_b.json", "w") as fh:
    json.dump(envelope, fh, indent=2)

# ------------------------------------- data_extra: a "late arrivals" file for a 2nd run
t1001 = next(r for r in csv_rows if r["txn_id"] == "T-1001")
t1002 = next(r for r in csv_rows if r["txn_id"] == "T-1002")
extra = [
    dict(t1002),                                                          # identical to stored -> skipped
    dict(t1001, amount=f"{Decimal(t1001['amount']) + Decimal('5.00'):.2f}"),  # CONFLICT_WITH_DB
    {"txn_id": "T-4001", "account_id": "ACC-001", "txn_date": "2025-03-20", "amount": "999.00",
     "currency": "INR", "txn_type": "CREDIT", "description": "Late arrival"},  # new -> loaded
]
with open(ROOT / "data_extra" / "late_arrivals.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=HEADER)
    w.writeheader()
    w.writerows(extra)

print(f"CSV rows: {len(csv_rows)}, JSON rows: {len(json_rows)}, extra rows: {len(extra)}")
