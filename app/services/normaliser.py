"""
Normalisation service: converts a raw WebhookPayload into a UnifiedMessage.

Each source channel can carry the same information in different field names
or formats. This layer translates everything into our canonical schema before
any business logic runs.

For this assessment all sources share the same payload shape, but the
per-source handlers below make it easy to add channel-specific quirks later
(e.g. Booking.com uses 'traveller_name' instead of 'guest_name').
"""

import logging
from app.models.schemas import MessageSource, UnifiedMessage, WebhookPayload
from app.services.classifier import classify_query

logger = logging.getLogger(__name__)


# ── Per-source field adapters ─────────────────────────────────────────────────
# Each function receives the raw payload dict and returns a dict whose keys
# match UnifiedMessage fields. Add source-specific transformations here.

def _adapt_whatsapp(payload: WebhookPayload) -> dict:
    return {
        "guest_name":   payload.guest_name,
        "message_text": payload.message.strip(),
        "booking_ref":  payload.booking_ref,
        "property_id":  payload.property_id,
    }


def _adapt_booking_com(payload: WebhookPayload) -> dict:
    # Booking.com messages often arrive with trailing automated footers;
    # strip anything after a common separator if present.
    message = payload.message.split("---")[0].strip()
    return {
        "guest_name":   payload.guest_name,
        "message_text": message,
        "booking_ref":  payload.booking_ref,
        "property_id":  payload.property_id,
    }


def _adapt_airbnb(payload: WebhookPayload) -> dict:
    return {
        "guest_name":   payload.guest_name,
        "message_text": payload.message.strip(),
        "booking_ref":  payload.booking_ref,
        "property_id":  payload.property_id,
    }


def _adapt_instagram(payload: WebhookPayload) -> dict:
    # Instagram DMs may lack a booking reference.
    return {
        "guest_name":   payload.guest_name,
        "message_text": payload.message.strip(),
        "booking_ref":  None,  # Instagram DMs rarely carry a ref
        "property_id":  payload.property_id,
    }


def _adapt_direct(payload: WebhookPayload) -> dict:
    return {
        "guest_name":   payload.guest_name,
        "message_text": payload.message.strip(),
        "booking_ref":  payload.booking_ref,
        "property_id":  payload.property_id,
    }


_ADAPTERS = {
    MessageSource.whatsapp:    _adapt_whatsapp,
    MessageSource.booking_com: _adapt_booking_com,
    MessageSource.airbnb:      _adapt_airbnb,
    MessageSource.instagram:   _adapt_instagram,
    MessageSource.direct:      _adapt_direct,
}


# ── Public API ────────────────────────────────────────────────────────────────

def normalise(payload: WebhookPayload) -> UnifiedMessage:
    """
    Convert a raw webhook payload into a UnifiedMessage.

    Steps:
    1. Run the source-specific adapter to extract canonical fields.
    2. Classify the message text into a QueryType.
    3. Assemble and return the UnifiedMessage (message_id auto-generated).
    """
    adapter = _ADAPTERS.get(payload.source, _adapt_direct)
    adapted = adapter(payload)

    query_type = classify_query(adapted["message_text"])

    unified = UnifiedMessage(
        source=payload.source,
        timestamp=payload.timestamp,
        query_type=query_type,
        **adapted,
    )

    logger.info(
        "Normalised message",
        extra={
            "message_id": unified.message_id,
            "source": unified.source,
            "query_type": unified.query_type.value,
        },
    )
    return unified
