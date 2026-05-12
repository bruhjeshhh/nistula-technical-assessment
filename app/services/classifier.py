"""
Rule-based query classifier.

Classifies a guest message into one of six QueryType values using keyword
matching. A lightweight rule-based approach was chosen deliberately:

  • Zero latency — no extra API call needed.
  • Fully deterministic — easy to unit-test and audit.
  • Easy to extend — add keywords to the lists below; no retraining.

The classifier runs before the Claude API call so the query type can be
included in the prompt, which helps Claude tailor its tone and content.

Trade-off: for highly ambiguous messages (e.g. "I need help") a rule-based
classifier may default to general_enquiry rather than the ideal type.
In production this could be replaced or augmented with a lightweight
intent-detection call. For now, general_enquiry is a safe fallback because
Claude will still produce a helpful reply.
"""

import re
from app.models.schemas import QueryType

# ── Keyword rule table ────────────────────────────────────────────────────────
# Rules are evaluated in ORDER; the first match wins.
# Each entry: (QueryType, list-of-keyword-patterns)

_RULES: list[tuple[QueryType, list[str]]] = [
    (
        QueryType.complaint,
        [
            r"\bnot (working|clean|happy|okay|ok|functional)\b",
            r"\bbroken\b", r"\bbroken down\b",
            r"\bissue\b", r"\bproblem\b", r"\bcomplaint\b",
            r"\bdisappointed\b", r"\bunhappy\b", r"\bupset\b",
            r"\brefund\b", r"\bunacceptable\b", r"\bterrible\b",
            r"\bawful\b", r"\bhorrible\b", r"\bdirty\b",
            r"\bno (hot )?water\b", r"\bno (a/?c|air con)\b",
            r"\bac not\b", r"\bwifi not\b",
        ],
    ),
    (
        QueryType.post_sales_checkin,
        [
            r"\bcheck[- ]?in\b", r"\bcheck[- ]?out\b",
            r"\bwifi\b", r"\bwi-fi\b", r"\bpassword\b",
            r"\bcaretaker\b", r"\bhousekeeper\b",
            r"\bearly check[- ]?in\b",   # also matches special_request but complaint wins first
            r"\blate check[- ]?out\b",
            r"\bkey(s)?\b", r"\baccess\b", r"\bhow do (i|we) get in\b",
            r"\bdirection(s)?\b", r"\baddress\b",
            r"\bpool (heat|towel|hour)\b",
        ],
    ),
    (
        QueryType.special_request,
        [
            r"\bearly check[- ]?in\b",
            r"\blate check[- ]?out\b",
            r"\bairport (transfer|pickup|drop)\b",
            r"\btransfer\b",
            r"\bchef\b", r"\bcook\b", r"\bmeal\b",
            r"\bbouquet\b", r"\bdecoration\b",
            r"\bsurprise\b", r"\banniversary\b", r"\bhoneymoon\b",
            r"\bbaby (cot|crib)\b", r"\bhigh chair\b",
            r"\bwheelchair\b", r"\baccessib\b",
        ],
    ),
    (
        QueryType.pre_sales_availability,
        [
            r"\bavailab\b",
            r"\bfree (on|from|between|for)\b",
            r"\bopen (on|from|for)\b",
            r"\bbook(ing|ed)?\b",
            r"\breserv\b",
            r"\bdate(s)?\b",
            r"\b(april|may|june|july|august|september|october|november|december|january|february|march)\b",
            r"\b\d{1,2}[\/\-]\d{1,2}\b",   # date patterns like 20/04 or 20-04
        ],
    ),
    (
        QueryType.pre_sales_pricing,
        [
            r"\bpric(e|ing)\b", r"\brate(s)?\b", r"\bcost(s)?\b",
            r"\bhow much\b", r"\bcharge\b",
            r"\binr\b", r"\brupee\b", r"\bper night\b",
            r"\bdiscount\b", r"\boffer\b", r"\bdeal\b",
            r"\bpackage\b",
        ],
    ),
    (
        QueryType.general_enquiry,
        [r".*"],   # catch-all — always matches
    ),
]


def classify_query(message_text: str) -> QueryType:
    """
    Return the QueryType that best describes the message.

    Matching is case-insensitive. The first rule whose ANY keyword matches
    the message wins. The final catch-all rule ensures we always return
    a valid QueryType.
    """
    text = message_text.lower()

    for query_type, patterns in _RULES:
        for pattern in patterns:
            if re.search(pattern, text):
                return query_type

    # Defensive fallback (catch-all rule above should prevent reaching here)
    return QueryType.general_enquiry
