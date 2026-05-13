"""Settings. Everything can be overridden with environment variables."""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://fin_etl:fin_etl@localhost:5432/fin_etl"
)
DATA_DIR = Path(os.environ.get("DATA_DIR", PROJECT_ROOT / "data"))
REPORT_DIR = Path(os.environ.get("REPORT_DIR", PROJECT_ROOT / "reports"))
SCHEMA_FILE = PROJECT_ROOT / "schema.sql"

# Currencies we accept. A code outside this set is rejected rather than guessed at.
ALLOWED_CURRENCIES = frozenset({"INR", "USD", "EUR", "GBP"})
