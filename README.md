# Nistula Guest Message Handler

A FastAPI backend that receives inbound guest messages from multiple channels, normalises them into a unified schema, drafts a reply using the Claude API, and returns a confidence-scored response with a recommended action.

**What’s in this repo (assessment):** Part 1 lives under `app/` with `main.py` as the entrypoint; Part 2 is `schema.sql`; Part 3 is `thinking.md`; copy `.env.example` to `.env` for local runs (no secrets in git).

---

## Quick Start

> **Python version:** use **Python 3.11 or 3.12**. The pinned `pydantic==2.9.2` ships only as a source wheel against `pyo3` <= 3.13, so `pip install` fails on Python 3.14. Use `python3.12 -m venv …` if your default `python` is 3.14.

```bash
# 1. Clone / unzip the project
cd nistula-technical-assessment

# 2. Create and activate a virtual environment (Python 3.11 / 3.12)
python3.12 -m venv venv
source venv/bin/activate         # Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Configure environment
cp .env.example .env
# Edit .env and set ANTHROPIC_API_KEY=<your key>

# 5. Run the server
python main.py
# Server starts at http://localhost:8000
# Interactive Swagger UI:  http://localhost:8000/docs
# Health check:            http://localhost:8000/health
```

---

## Example Request

```bash
curl -X POST http://localhost:8000/webhook/message \
  -H "Content-Type: application/json" \
  -d '{
    "source": "whatsapp",
    "guest_name": "Rahul Sharma",
    "message": "Is the villa available from April 20 to 24? What is the rate for 2 adults?",
    "timestamp": "2026-05-05T10:30:00Z",
    "booking_ref": "NIS-2024-0891",
    "property_id": "villa-b1"
  }'
```

Example response:

```json
{
  "message_id": "a3f1c2d4-...",
  "query_type": "pre_sales_availability",
  "drafted_reply": "Hi Rahul! Great news — Villa B1 is available from April 20–24. The base rate is INR 18,000 per night for up to 4 guests, so for 2 adults that works out to INR 72,000 for 4 nights. Shall I go ahead and hold these dates for you?",
  "confidence_score": 0.95,
  "action": "auto_send"
}
```

---

## Architecture

```
POST /webhook/message
        │
        ▼
  WebhookPayload          ← Pydantic validates & parses the inbound JSON
        │
        ▼
  normalise()             ← Per-source adapter + UUID generation
        │
        ▼
  classify_query()        ← Rule-based keyword classifier (zero latency)
        │
        ▼
  UnifiedMessage          ← Canonical internal schema
        │
        ▼
  generate_reply()        ← Claude API call with injected property context
        │
        ▼
  determine_action()      ← Threshold logic → auto_send / agent_review / escalate
        │
        ▼
  MessageResponse         ← Returned to caller
```

### File Structure

```
nistula-technical-assessment/
├── main.py                         Entry point (uvicorn)
├── requirements.txt
├── pytest.ini                      Registers the `integration` test marker
├── schema.sql                      Part 2 — PostgreSQL DDL with inline comments
├── thinking.md                     Part 3 — written scenario answers
├── .env.example                    Copy to .env and fill in your key
├── .gitignore                      Keeps .env, venv/, __pycache__ out of git
├── app/
│   ├── app.py                      FastAPI app factory, /health route
│   ├── models/
│   │   └── schemas.py              Pydantic models & enums
│   ├── routes/
│   │   └── webhook.py              POST /webhook/message handler
│   ├── services/
│   │   ├── classifier.py           Rule-based query classifier
│   │   ├── normaliser.py           Per-channel field adapters
│   │   └── ai_service.py           Claude API client
│   └── utils/
│       ├── action.py               Confidence → action mapping
│       └── property_context.py     Property data & prompt formatter
└── tests/
    └── test_handler.py             Unit tests + live API integration tests
```

### Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/webhook/message` | The main pipeline. Validate → normalise → classify → Claude → action. |
| `GET`  | `/health`          | Liveness probe — returns `{"status": "ok", "service": "nistula-guest-handler"}`. |
| `GET`  | `/docs`            | Auto-generated Swagger UI for trying requests interactively. |

---

## Design Decisions

### 1. Rule-Based Classifier (not a second AI call)

The query classifier uses ordered regex rules rather than calling Claude to classify.

**Why:**
- **Zero added latency** — the classification happens in microseconds before the Claude call, not in serial after it.
- **Deterministic** — the same message always gets the same classification. This makes testing and debugging straightforward.
- **Easy to maintain** — a PM or support lead can read the keyword lists and suggest additions without touching ML infrastructure.
- **The classification feeds the prompt anyway** — Claude still sees the query type label, which helps it tailor tone. The label doesn't need to be perfect; it's a hint, not a gate.

**Trade-off:** For genuinely ambiguous messages ("I need help") it will default to `general_enquiry`. In production this could be addressed by routing ambiguous messages to a lightweight intent-detection call or simply letting `agent_review` catch them via confidence scoring.

### 2. Confidence Score Formula

Claude is asked to return two self-reported signals in its JSON response. **These values are produced by Claude itself, scored against the rubric in our system prompt — our code does not compute them**, it just clamps them to `[0, 1]` and blends them:

| Signal | Weight | What it measures |
|--------|--------|-----------------|
| `certainty` | 60% | Is the answer factually correct given the context provided? |
| `completeness` | 40% | Did the context cover every sub-question the guest asked? |

```
confidence_score = certainty × 0.6 + completeness × 0.4
```

**Why two signals instead of one?**  
A reply can be *certain* but *incomplete* — e.g. "Yes the villa is available" correctly answers the availability half of a pricing + availability question, but omits the pricing. A single confidence number from Claude wouldn't distinguish these cases. The blended score penalises partial answers even when Claude is confident about the parts it did answer.

**Why 60/40 weighting?**  
Correctness matters slightly more than completeness. An incorrect answer that seems complete is worse than a correct-but-partial answer that the agent can supplement.

**Caveat with model self-reporting.**  
LLMs can be overconfident about hallucinated facts. The system prompt mitigates this by instructing Claude to answer **only** from the supplied property context, but the assumption is worth flagging: in production you'd want to calibrate these scores against real outcomes (did the agent edit the reply? did the guest reply happy?) and consider adding an LLM-as-judge pass for high-stakes flows.

### 3. Action Thresholds

| Condition | Action |
|-----------|--------|
| `query_type == complaint` | `escalate` (always — human empathy required) |
| `confidence >= 0.85` | `auto_send` |
| `0.60 ≤ confidence < 0.85` | `agent_review` |
| `confidence < 0.60` | `escalate` |

Complaints always escalate regardless of the score. A confident-sounding AI apology is not a substitute for a human resolving an issue.

**Note on the 0.85 boundary.** The brief describes the bands as "above 0.85", "0.60–0.85", "below 0.60", which overlap at exactly 0.85. We deliberately put `0.85` into `auto_send` (using `>=`) so that a clean "1.0 certainty × 1.0 completeness" reply auto-sends without ambiguity. Trivial to flip to `>` if a stricter reading is preferred — the unit tests pin the boundary explicitly.

### 4. Structured JSON from Claude

The system prompt instructs Claude to return a JSON envelope (`reply`, `certainty`, `completeness`, `notes`) rather than free text. This avoids a second parsing/extraction call and gives us machine-readable confidence signals.

A fallback handles the case where Claude returns malformed JSON — it uses the raw text as the reply with a default confidence of 0.5 (which routes to `agent_review`).

### 5. Per-Source Adapters in the Normaliser

Each channel (WhatsApp, Booking.com, Airbnb, etc.) has its own adapter function even though the current test payload is identical across channels. This makes it cheap to add channel-specific quirks later:

- Booking.com messages often include automated footers (already handled — strips after `---`)
- Instagram DMs rarely carry a booking reference (already handled — sets to `None`)
- Airbnb may send HTML-escaped text (easy to add: `html.unescape()` in `_adapt_airbnb`)

### 6. No Database

The assessment scope is a single stateless request–response cycle. State (message history, booking records) would come from a database or CMS in production. The `property_context.py` module is deliberately structured as a dict keyed by `property_id` to make that migration easy.

---

## Running Tests

```bash
pytest tests/ -v
```

Classifier, normaliser, action logic, validation, health, and one AI-error case run **without** calling Anthropic.

**Live Claude tests** (`TestWebhookEndpointLive`, marked `integration`) call the real API. They run when `ANTHROPIC_API_KEY` is set (e.g. in `.env`, loaded automatically by the test module via `python-dotenv`). If the key is missing, those tests are **skipped**.

Run only fast tests (skip live API):

```bash
pytest tests/ -v -m "not integration"
```

Run everything including live API (needs key + network):

```bash
pytest tests/ -v
```

Tests cover:
- **Classifier** — all 6 query types including edge cases (complaint > availability, catch-all)
- **Normaliser** — UUID generation, per-source adapters, whitespace stripping
- **Action logic** — all three action outcomes including boundary values (0.60, 0.85)
- **Webhook** — live availability / complaint / check-in flows, validation failures, health check, and a mocked AI failure (502)

---

## Supported Sources

| Value | Channel |
|-------|---------|
| `whatsapp` | WhatsApp Business |
| `booking_com` | Booking.com |
| `airbnb` | Airbnb |
| `instagram` | Instagram DM |
| `direct` | Direct / email |

---

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | ✅ | — | Claude API key |
| `CLAUDE_MODEL` | ❌ | `claude-sonnet-4-20250514` | Model to use |
| `LOG_LEVEL` | ❌ | `INFO` | Python log level |
| `PORT` | ❌ | `8000` | HTTP port |

---

## What I Would Add With More Time

1. **Database layer** — store all messages and AI replies with their confidence scores. This creates an audit trail and enables fine-tuning the confidence thresholds over time with real data.

2. **Async queue** — move the Claude API call off the request path. Webhook responds immediately with `202 Accepted` + `message_id`; the drafted reply is delivered via a callback or polling endpoint. This is more resilient to Claude API latency spikes.

3. **Multi-turn context** — pass the last N messages for a given `booking_ref` into the Claude prompt so it can handle follow-up questions naturally.

4. **Confidence calibration** — log real outcomes (did the agent edit the reply? was the guest satisfied?) and use them to tune the 0.60/0.85 thresholds and the 60/40 weighting.

5. **Rate limiting & webhook verification** — HMAC signature validation per source (WhatsApp and Airbnb both provide these) to ensure only genuine messages are processed.
