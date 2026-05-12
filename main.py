"""
Entry point — run with:  python main.py  OR  uvicorn main:app --reload
"""

import os
from dotenv import load_dotenv

load_dotenv()  # load .env before anything else imports os.getenv

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
