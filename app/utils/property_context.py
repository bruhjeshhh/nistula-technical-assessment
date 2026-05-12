"""
Static property context injected into every Claude prompt.

In a production system this would be fetched from a database or CMS
keyed by property_id. For this assessment the single villa is hardcoded.
"""

PROPERTY_CONTEXT: dict[str, dict] = {
    "villa-b1": {
        "name":          "Villa B1",
        "location":      "Assagao, North Goa",
        "bedrooms":      3,
        "max_guests":    6,
        "private_pool":  True,
        "check_in":      "2:00 PM",
        "check_out":     "11:00 AM",
        "base_rate":     "INR 18,000 per night (up to 4 guests)",
        "extra_guest":   "INR 2,000 per night per person",
        "wifi_password": "Nistula@2024",
        "caretaker":     "Available 8 AM to 10 PM",
        "chef_on_call":  "Yes, pre-booking required",
        "availability":  {
            "April 20–24 2026": "Available",
        },
        "cancellation":  "Free cancellation up to 7 days before check-in",
        "pets":          "Not allowed",
        "parking":       "Private parking available on premises",
        "airport_transfer": "Can be arranged on request (additional charge)",
    }
}

DEFAULT_PROPERTY_ID = "villa-b1"


def get_property_context(property_id: str | None) -> dict:
    """Return property dict for the given id, falling back to the default."""
    pid = (property_id or DEFAULT_PROPERTY_ID).lower()
    return PROPERTY_CONTEXT.get(pid, PROPERTY_CONTEXT[DEFAULT_PROPERTY_ID])


def format_property_context(property_id: str | None) -> str:
    """Render property context as a readable block for prompt injection."""
    ctx = get_property_context(property_id)
    availability_lines = "\n  ".join(
        f"{k}: {v}" for k, v in ctx.get("availability", {}).items()
    )
    return f"""
PROPERTY INFORMATION
────────────────────
Name            : {ctx['name']}
Location        : {ctx['location']}
Bedrooms        : {ctx['bedrooms']} | Max guests: {ctx['max_guests']}
Private pool    : {'Yes' if ctx['private_pool'] else 'No'}
Check-in        : {ctx['check_in']}
Check-out       : {ctx['check_out']}
Base rate       : {ctx['base_rate']}
Extra guest fee : {ctx['extra_guest']}
WiFi password   : {ctx['wifi_password']}
Caretaker       : {ctx['caretaker']}
Chef on call    : {ctx['chef_on_call']}
Cancellation    : {ctx['cancellation']}
Pets            : {ctx['pets']}
Parking         : {ctx['parking']}
Airport transfer: {ctx['airport_transfer']}

Availability
  {availability_lines}
""".strip()
