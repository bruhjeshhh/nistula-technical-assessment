## Question A — The immediate response

> "I'm really sorry — no hot water with breakfast guests arriving is genuinely stressful, and I want to make sure this gets fixed tonight. I've woken up our on-call maintenance team right now and they're being dispatched to Villa B1. A member of our team will message you within the next 15 minutes with an update. Your refund request has been noted and will be reviewed first thing in the morning."

I chose this wording because the guest is angry and stressed, so the response should first acknowledge the inconvenience and urgency without sounding robotic. Committing to a 15-minute human follow-up sets a concrete expectation rather than a vague promise, which reduces the chance of a second angry message. It also avoids promising a refund instantly, which an AI should not autonomously approve.

---

## Question B — The system response

The moment the message is classified as an urgent maintenance complaint with high confidence, the platform triggers in parallel: a maintenance alert is pushed to the on-call engineer with the villa number, complaint text, and a 15-minute acknowledgment deadline; the duty manager receives a separate notification flagging that a refund request is attached; and the conversation is escalated in the agent dashboard with a countdown timer visible to whoever is on night cover.

The conversation, message, AI confidence score, and query type (`urgent_maintenance` + `refund_request`) are all logged immediately. The refund request is tagged on the reservation record so it cannot be missed during the morning handover.

If no human acknowledges within 30 minutes, the system sends a second automated message to the guest — "Our team is still working on reaching maintenance, we haven't forgotten you" — and escalates the notification to the duty manager's phone via a louder channel (call, not just push). The unresolved conversation is flagged red on the dashboard for the morning shift.

---

## Question C — The learning

Three hot water complaints at the same villa within two months is a signal, not a coincidence. The system should automatically surface this pattern by tracking complaint frequency per property and triggering a flag when the same issue category hits a threshold — two occurrences within 60 days is a reasonable starting point.

Once flagged, the platform generates a maintenance report linking all three conversations and sends it to the property manager with a prompt to schedule a full inspection of the boiler at Villa B1. This is not a manual process — it fires automatically the moment the third complaint is logged.

To prevent a fourth complaint, you would build a lightweight preventive maintenance schedule into the platform: after an infrastructure complaint is resolved, the system creates a follow-up task 30 days out to verify the fix held. If the task is marked done, the clock resets. If it is skipped or the complaint recurs, the escalation threshold drops — the system becomes more sensitive to that villa specifically. Over time this creates a per-property risk profile that gets smarter as more data comes in.