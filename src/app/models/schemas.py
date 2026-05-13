"""
Pydantic models for inbound webhook payloads, unified schema, and API responses.
"""

from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field
import uuid


# ── Enumerations ─────────────────────────────────────────────────────────────

class MessageSource(str, Enum):
    whatsapp   = "whatsapp"
    booking_com = "booking_com"
    airbnb     = "airbnb"
    instagram  = "instagram"
    direct     = "direct"


class QueryType(str, Enum):
    pre_sales_availability = "pre_sales_availability"
    pre_sales_pricing      = "pre_sales_pricing"
    post_sales_checkin     = "post_sales_checkin"
    special_request        = "special_request"
    complaint              = "complaint"
    general_enquiry        = "general_enquiry"


class Action(str, Enum):
    auto_send     = "auto_send"      # confidence >= 0.85
    agent_review  = "agent_review"   # 0.60 <= confidence < 0.85
    escalate      = "escalate"       # confidence < 0.60 OR complaint


# ── Inbound Webhook Payload ───────────────────────────────────────────────────

class WebhookPayload(BaseModel):
    """Raw payload received from any channel."""
    source:      MessageSource
    guest_name:  str            = Field(..., min_length=1)
    message:     str            = Field(..., min_length=1)
    timestamp:   datetime
    booking_ref: Optional[str]  = None
    property_id: Optional[str]  = None


# ── Unified / Normalised Schema ───────────────────────────────────────────────

class UnifiedMessage(BaseModel):
    """Canonical message format used internally and passed to AI."""
    message_id:   str        = Field(default_factory=lambda: str(uuid.uuid4()))
    source:       MessageSource
    guest_name:   str
    message_text: str
    timestamp:    datetime
    booking_ref:  Optional[str] = None
    property_id:  Optional[str] = None
    query_type:   QueryType


# ── API Response ──────────────────────────────────────────────────────────────

class MessageResponse(BaseModel):
    """Response returned by POST /webhook/message."""
    message_id:       str
    query_type:       QueryType
    drafted_reply:    str
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    action:           Action
