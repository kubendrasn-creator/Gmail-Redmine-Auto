from backend.config.settings import REQUIRED_FIELDS


SUPPORTED_REQUIRED_FIELDS = {
    "subject",
    "description",
    "ticket_type",
    "center",
    "module",
    "assignee",
}


def missing_required_fields(values):
    required_fields = REQUIRED_FIELDS
    if str(values.get("ticket_type") or "").strip().lower() == "feedback":
        required_fields = [
            field for field in REQUIRED_FIELDS
            if field != "module"
        ]
    return [
        field
        for field in required_fields
        if not values.get(field)
    ]
