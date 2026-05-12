"""
AI reply service: calls the Claude API to draft a guest reply.

Design decisions
────────────────
• System prompt sets tone, persona, and hard constraints so the model
  never invents information not in the property context.
• The property context and query classification are injected into the
  user turn so the model can tailor its response precisely.
• We ask Claude to respond in a structured JSON envelope so confidence
  signals (certainty, completeness) can be extracted programmatically
  without a second API call.
• httpx is used directly (no SDK dependency) — fewer moving parts,
  easier to mock in tests.
"""

import json
import logging
import os
import re
import httpx
from app.models.schemas import QueryType, UnifiedMessage
from app.utils.property_context import format_property_context

logger = logging.getLogger(__name__)

# Strips an optional ```json ... ``` (or plain ```...```) fence Claude
# sometimes wraps its JSON in, so json.loads can parse the body directly.
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n?(.*?)\n?```\s*$", re.DOTALL | re.IGNORECASE)


def _extract_json_payload(text: str) -> str:
    """Return the JSON substring from a model response.

    Handles three cases:
      1. Plain JSON — returned unchanged.
      2. Fenced JSON (```json ... ``` or ``` ... ```) — fence stripped.
      3. JSON embedded in prose — the first {...} block is extracted.
    """
    stripped = text.strip()
    fence_match = _FENCE_RE.match(stripped)
    if fence_match:
        return fence_match.group(1).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start != -1 and end > start:
        return stripped[start : end + 1]
    return stripped

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL      = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-20250514")
REQUEST_TIMEOUT   = 30  # seconds

# ── Prompt helpers ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a warm, professional guest-relations assistant for Nistula Villas, a luxury villa rental company in Goa, India.

Your job is to draft replies to guest enquiries based ONLY on the property information provided. Do not invent details that are not in the context.

TONE GUIDELINES
• Friendly and personal — use the guest's first name.
• Concise — answer the question directly, then offer to help further.
• Confident when you have the answer; honest when you do not.
• For complaints, always acknowledge the inconvenience first, apologise sincerely, then provide next steps.

RESPONSE FORMAT
You must respond with a valid JSON object and nothing else:

{
  "reply": "<your drafted reply to the guest>",
  "certainty": <0.0–1.0 — how confident you are the reply is correct and complete>,
  "completeness": <0.0–1.0 — how fully the available context answered the guest's question>,
  "notes": "<optional internal note for the agent, or empty string>"
}

certainty   = 1.0 when the answer is unambiguously in the context.
            = 0.7–0.9 when partially answered or making reasonable inferences.
            = below 0.6 when the question cannot be answered from the provided context.

completeness = 1.0 when every part of the question is answered.
             = lower when some sub-questions remain unanswered."""


def _build_user_prompt(msg: UnifiedMessage, property_context: str) -> str:
    """Construct the user-turn prompt with all relevant context."""
    booking_info = (
        f"Booking reference: {msg.booking_ref}" if msg.booking_ref
        else "No booking reference provided (pre-sales enquiry)"
    )

    query_type_hint = {
        QueryType.pre_sales_availability: "The guest is asking about availability.",
        QueryType.pre_sales_pricing:      "The guest is asking about pricing / rates.",
        QueryType.post_sales_checkin:     "The guest has an existing booking and is asking about check-in details, WiFi, or property access.",
        QueryType.special_request:        "The guest is making a special request.",
        QueryType.complaint:              "The guest is raising a complaint. Prioritise empathy and resolution.",
        QueryType.general_enquiry:        "The guest has a general enquiry.",
    }[msg.query_type]

    return f"""GUEST MESSAGE
─────────────
Source      : {msg.source.value}
Guest name  : {msg.guest_name}
{booking_info}
Query type  : {msg.query_type.value}
Hint        : {query_type_hint}

Message:
\"\"\"{msg.message_text}\"\"\"

{property_context}

Draft a reply to the guest's message using ONLY the information above."""


# ── Main service function ─────────────────────────────────────────────────────

async def generate_reply(msg: UnifiedMessage) -> tuple[str, float, str]:
    """
    Call Claude and return (drafted_reply, confidence_score, notes).

    Confidence score combines:
      • certainty   (Claude's self-reported answer correctness) — 60 % weight
      • completeness (how fully the question was answered)       — 40 % weight

    This blended score penalises replies that are "confident" but only
    partially answer a multi-part question.

    Raises:
        httpx.HTTPStatusError  — if the API returns a non-2xx status.
        ValueError             — if the response is not parseable JSON.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable is not set.")

    property_context = format_property_context(msg.property_id)
    user_prompt      = _build_user_prompt(msg, property_context)

    payload = {
        "model":      CLAUDE_MODEL,
        "max_tokens": 1024,
        "system":     SYSTEM_PROMPT,
        "messages":   [{"role": "user", "content": user_prompt}],
    }

    headers = {
        "x-api-key":         api_key,
        "anthropic-version": "2023-06-01",
        "content-type":      "application/json",
    }

    logger.info("Calling Claude API", extra={"message_id": msg.message_id, "model": CLAUDE_MODEL})

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        response = await client.post(ANTHROPIC_API_URL, json=payload, headers=headers)
        response.raise_for_status()

    raw_text = response.json()["content"][0]["text"]

    try:
        data = json.loads(_extract_json_payload(raw_text))
    except json.JSONDecodeError:
        logger.warning("Claude response was not valid JSON; using raw text as reply.")
        return raw_text.strip(), 0.5, ""

    drafted_reply = data.get("reply", "").strip()
    certainty     = float(data.get("certainty",    0.5))
    completeness  = float(data.get("completeness", 0.5))
    notes         = data.get("notes", "")

    # Clamp values to [0, 1]
    certainty    = max(0.0, min(1.0, certainty))
    completeness = max(0.0, min(1.0, completeness))

    # Weighted blend: certainty 60%, completeness 40%
    confidence_score = round(certainty * 0.6 + completeness * 0.4, 4)

    logger.info(
        "Claude reply generated",
        extra={
            "message_id":       msg.message_id,
            "certainty":        certainty,
            "completeness":     completeness,
            "confidence_score": confidence_score,
        },
    )

    return drafted_reply, confidence_score, notes
