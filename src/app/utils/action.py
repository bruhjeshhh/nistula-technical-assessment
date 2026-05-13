"""
Determines the action to take based on confidence score and query type.

Rules (in priority order):
  1. Any complaint → escalate (regardless of confidence score)
  2. confidence >= 0.85 → auto_send
  3. 0.60 <= confidence < 0.85 → agent_review
  4. confidence < 0.60 → escalate
"""

from app.models.schemas import Action, QueryType


def determine_action(confidence_score: float, query_type: QueryType) -> Action:
    """Return the appropriate action for a given confidence score and query type."""
    if query_type == QueryType.complaint:
        return Action.escalate

    if confidence_score >= 0.85:
        return Action.auto_send
    elif confidence_score >= 0.60:
        return Action.agent_review
    else:
        return Action.escalate
