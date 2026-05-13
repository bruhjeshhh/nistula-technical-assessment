"""
POST /webhook/message

Orchestrates the full pipeline:
  1. Parse & validate the inbound payload (Pydantic).
  2. Normalise into UnifiedMessage.
  3. Call Claude to generate a drafted reply + confidence signals.
  4. Determine action based on confidence and query type.
  5. Return structured MessageResponse.
"""

import logging
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from app.models.schemas import MessageResponse, WebhookPayload
from app.services.normaliser import normalise
from app.services.ai_service import generate_reply
from app.utils.action import determine_action

logger   = logging.getLogger(__name__)
router   = APIRouter()


@router.post(
    "/webhook/message",
    response_model=MessageResponse,
    summary="Receive an inbound guest message",
    description=(
        "Accepts a raw guest message from any supported channel, "
        "normalises it, drafts an AI reply via Claude, and returns "
        "the reply with a confidence score and recommended action."
    ),
)
async def handle_message(payload: WebhookPayload) -> MessageResponse:
    """Main entry-point for all inbound guest messages."""

    # ── Step 1: Normalise ─────────────────────────────────────────────────────
    try:
        unified_msg = normalise(payload)
    except Exception as exc:
        logger.exception("Normalisation failed")
        raise HTTPException(status_code=422, detail=f"Message normalisation error: {exc}") from exc

    # ── Step 2: Generate AI reply ─────────────────────────────────────────────
    try:
        drafted_reply, confidence_score, _notes = await generate_reply(unified_msg)
    except Exception as exc:
        logger.exception("AI reply generation failed", extra={"message_id": unified_msg.message_id})
        raise HTTPException(
            status_code=502,
            detail=f"AI service error: {exc}",
        ) from exc

    # ── Step 3: Determine action ──────────────────────────────────────────────
    action = determine_action(confidence_score, unified_msg.query_type)

    response = MessageResponse(
        message_id=unified_msg.message_id,
        query_type=unified_msg.query_type,
        drafted_reply=drafted_reply,
        confidence_score=confidence_score,
        action=action,
    )

    logger.info(
        "Request completed",
        extra={
            "message_id":       response.message_id,
            "query_type":       response.query_type.value,
            "confidence_score": response.confidence_score,
            "action":           response.action.value,
        },
    )

    return response
