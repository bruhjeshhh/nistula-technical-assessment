"""
Entry point — run with:  python main.py  OR  uvicorn main:app --reload
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Repo-root `.env` (next to `.env.example`) or `src/.env` when running from `src/`
_root = Path(__file__).resolve().parent.parent
load_dotenv(_root / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env")

from app.app import create_app  # noqa: E402  (import after load_dotenv)

app = create_app()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=True,
    )
