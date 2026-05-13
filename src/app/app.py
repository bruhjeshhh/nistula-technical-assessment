"""
Application factory.

Keeps the FastAPI app creation separate from main.py so the app object
can be imported cleanly in tests without starting a server process.
"""

import logging
import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes.webhook import router as webhook_router

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Nistula Guest Message Handler",
        description=(
            "Receives guest messages from multiple channels, normalises them "
            "into a unified schema, and uses Claude to draft replies with a "
            "confidence score."
        ),
        version="1.0.0",
    )

    # Permissive CORS for assessment purposes
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(webhook_router, tags=["Webhook"])

    @app.get("/health", tags=["Health"])
    async def health_check():
        return {"status": "ok", "service": "nistula-guest-handler"}

    return app
