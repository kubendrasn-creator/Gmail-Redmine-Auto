import hashlib
import re


STRUCTURED_FINGERPRINT_FIELDS = (
    "center",
    "module",
    "description",
    "issue",
    "steps",
    "expected_result",
    "actual_result",
)


def _normalize_text(value):
    """
    Normalize text so formatting differences do not affect
    duplicate detection.
    """

    if not value:
        return ""

    value = value.strip().lower()

    value = value.replace("\r\n", "\n")
    value = value.replace("\r", "\n")

    # Normalize whitespace.
    value = re.sub(r"\s+", " ", value)

    return value.strip()


def create_content_fingerprint(email):
    """
    Create a deterministic SHA-256 fingerprint for an email.

    Structured support fields are preferred because they represent
    the actual issue and remain stable when Gmail forwarding adds
    additional sender/date/subject headers.

    If structured fields are not available, fall back to the
    normalized email content.
    """

    structured_values = []

    for field_name in STRUCTURED_FINGERPRINT_FIELDS:

        value = _normalize_text(
            email.get(field_name)
        )

        structured_values.append(value)

    # Use structured issue information when meaningful
    # structured data exists.
    meaningful_structured_values = [
        value
        for value in structured_values
        if value
    ]

    if meaningful_structured_values:

        fingerprint_source = "|".join(
            structured_values
        )

    else:

        fingerprint_source = "|".join(
            [
                _normalize_text(
                    email.get("sender_email")
                ),
                _normalize_text(
                    email.get("subject")
                ),
                _normalize_text(
                    email.get("body")
                ),
            ]
        )

    return hashlib.sha256(
        fingerprint_source.encode("utf-8")
    ).hexdigest()