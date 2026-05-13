"""
Tests for the classifier, normaliser, action logic, and the webhook endpoint.

Webhook tests in `TestWebhookEndpointLive` call the real Anthropic API. A full `pytest`
run expects `ANTHROPIC_API_KEY` in the environment or in `.env` at the **repository root**
(loaded below). Use `pytest -m "not integration"` to skip those tests when working offline.
"""

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

# test_handler.py lives at repo/src/tests/ — repo root is three levels up from this file
_repo_root = Path(__file__).resolve().parents[2]
_src_root = Path(__file__).resolve().parents[1]
load_dotenv(_repo_root / ".env")
load_dotenv(_src_root / ".env", override=True)

from app.app import create_app
from app.models.schemas import (
    Action,
    MessageSource,
    QueryType,
    WebhookPayload,
)
from app.services.classifier import classify_query
from app.services.normaliser import normalise
from app.utils.action import determine_action

# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def client():
    return TestClient(create_app())


@pytest.fixture
def require_live_api():
    key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    assert key, (
        "ANTHROPIC_API_KEY is required for live webhook tests (real Anthropic API). "
        "Copy `.env.example` to `.env` at the repository root and set your key, "
        "or run only offline tests: pytest -m 'not integration'"
    )


def make_payload(**overrides) -> dict:
    base = {
        "source": "whatsapp",
        "guest_name": "Rahul Sharma",
        "message": "Is the villa available from April 20 to 24?",
        "timestamp": "2026-05-05T10:30:00Z",
        "booking_ref": "NIS-2024-0891",
        "property_id": "villa-b1",
    }
    base.update(overrides)
    return base


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)


# ── Classifier Tests ──────────────────────────────────────────────────────────


class TestClassifier:
    def test_availability(self):
        assert classify_query("Is the villa available from April 20 to 24?") == QueryType.pre_sales_availability

    def test_pricing(self):
        assert classify_query("What is the rate for 2 adults 3 nights?") == QueryType.pre_sales_pricing

    def test_checkin_wifi(self):
        assert classify_query("What is the WiFi password?") == QueryType.post_sales_checkin

    def test_checkin_time(self):
        assert classify_query("What time is check-in?") == QueryType.post_sales_checkin

    def test_complaint(self):
        assert classify_query("The AC is not working. I am not happy.") == QueryType.complaint

    def test_special_request_transfer(self):
        assert classify_query("Can you arrange an airport transfer for us?") == QueryType.special_request

    def test_special_request_chef(self):
        assert classify_query("We would love a chef for dinner on our first night.") == QueryType.special_request

    def test_general_enquiry(self):
        assert classify_query("Do you allow pets?") == QueryType.general_enquiry

    def test_complaint_wins_over_availability(self):
        assert classify_query("The pool is broken and we need a refund for April 20.") == QueryType.complaint

    def test_booking_availability(self):
        msg = "We want to book the villa for 5 nights from the 20th."
        assert classify_query(msg) == QueryType.pre_sales_availability


# ── Normaliser Tests ──────────────────────────────────────────────────────────


class TestNormaliser:
    def _make_payload(self, **kwargs) -> WebhookPayload:
        defaults = {
            "source": MessageSource.whatsapp,
            "guest_name": "Priya Menon",
            "message": "What time is check-in?",
            "timestamp": datetime(2026, 5, 5, 10, 30, tzinfo=timezone.utc),
            "booking_ref": "NIS-2024-0001",
            "property_id": "villa-b1",
        }
        defaults.update(kwargs)
        return WebhookPayload(**defaults)

    def test_message_id_generated(self):
        result = normalise(self._make_payload())
        assert result.message_id is not None
        assert len(result.message_id) == 36

    def test_source_preserved(self):
        result = normalise(self._make_payload(source=MessageSource.airbnb))
        assert result.source == MessageSource.airbnb

    def test_query_type_classified(self):
        result = normalise(self._make_payload(message="What time is check-in?"))
        assert result.query_type == QueryType.post_sales_checkin

    def test_booking_com_strips_footer(self):
        payload = self._make_payload(
            source=MessageSource.booking_com,
            message="Is the pool available? ---\nAutomated Booking.com footer text",
        )
        result = normalise(payload)
        assert "---" not in result.message_text
        assert "footer" not in result.message_text

    def test_instagram_clears_booking_ref(self):
        payload = self._make_payload(
            source=MessageSource.instagram,
            booking_ref="NIS-2024-9999",
        )
        result = normalise(payload)
        assert result.booking_ref is None

    def test_whitespace_stripped(self):
        payload = self._make_payload(message="   Hello, any availability?   ")
        result = normalise(payload)
        assert result.message_text == "Hello, any availability?"


# ── Action Logic Tests ────────────────────────────────────────────────────────


class TestActionLogic:
    def test_high_confidence_auto_send(self):
        assert determine_action(0.90, QueryType.pre_sales_availability) == Action.auto_send

    def test_mid_confidence_agent_review(self):
        assert determine_action(0.75, QueryType.pre_sales_pricing) == Action.agent_review

    def test_low_confidence_escalate(self):
        assert determine_action(0.50, QueryType.general_enquiry) == Action.escalate

    def test_complaint_always_escalates(self):
        assert determine_action(0.95, QueryType.complaint) == Action.escalate

    def test_boundary_085_auto_send(self):
        assert determine_action(0.85, QueryType.general_enquiry) == Action.auto_send

    def test_boundary_060_agent_review(self):
        assert determine_action(0.60, QueryType.general_enquiry) == Action.agent_review

    def test_boundary_059_escalate(self):
        assert determine_action(0.59, QueryType.general_enquiry) == Action.escalate


# ── Webhook: validation & health (no external API) ───────────────────────────


class TestWebhookEndpointValidation:
    def test_invalid_source_rejected(self, client):
        payload = make_payload(source="telegram")
        resp = client.post("/webhook/message", json=payload)
        assert resp.status_code == 422

    def test_missing_message_rejected(self, client):
        payload = make_payload()
        del payload["message"]
        resp = client.post("/webhook/message", json=payload)
        assert resp.status_code == 422

    def test_health_check(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


# ── Webhook: live Claude API ──────────────────────────────────────────────────


@pytest.mark.integration
class TestWebhookEndpointLive:
    """Full stack through `generate_reply` — requires ANTHROPIC_API_KEY and network."""

    def test_availability_enquiry(self, client, require_live_api):
        payload = make_payload(
            message="Is the villa available from April 20 to 24? What is the rate for 2 adults?"
        )
        resp = client.post("/webhook/message", json=payload)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["query_type"] == "pre_sales_availability"
        assert _UUID_RE.match(data["message_id"])
        assert len(data["drafted_reply"]) > 20
        assert "```json" not in data["drafted_reply"], "reply should be guest prose, not a raw JSON fence"
        assert 0.0 <= data["confidence_score"] <= 1.0
        assert data["action"] in ("auto_send", "agent_review", "escalate")

    def test_complaint_escalates(self, client, require_live_api):
        payload = make_payload(
            source="airbnb",
            message="The AC is not working and we are very uncomfortable. This is unacceptable.",
        )
        resp = client.post("/webhook/message", json=payload)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["query_type"] == "complaint"
        assert data["action"] == "escalate"

    def test_post_sales_checkin(self, client, require_live_api):
        payload = make_payload(
            source="direct",
            message="Hi, what is the WiFi password and what time can we check in?",
        )
        resp = client.post("/webhook/message", json=payload)
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["query_type"] == "post_sales_checkin"
        assert len(data["drafted_reply"]) > 10
        assert 0.0 <= data["confidence_score"] <= 1.0
        assert data["action"] in ("auto_send", "agent_review", "escalate")


# ── Webhook: AI failure path (mocked, no key required) ───────────────────────


class TestWebhookEndpointAiErrors:
    @patch(
        "app.routes.webhook.generate_reply",
        side_effect=RuntimeError("simulated AI outage"),
    )
    def test_ai_error_returns_502(self, _mock, client):
        payload = make_payload(message="Hello?")
        resp = client.post("/webhook/message", json=payload)
        assert resp.status_code == 502
        assert "AI service error" in resp.json()["detail"]
