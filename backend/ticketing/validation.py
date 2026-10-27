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
    return [
        field
        for field in REQUIRED_FIELDS
        if not values.get(field)
    ]
