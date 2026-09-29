import re


class TicketExtractor:
    """
    Extracts structured ticket information from an email.
    """

    FIELDS = [
        "Center",
        "Module",
        "Description",
        "Expected Result",
        "Actual Result",
    ]

    def extract(self, email):
        body = email.get("body", "").strip()

        return {
            "subject": email.get("subject", "").strip(),
            "center": self._extract_field(body, "Center"),
            "module": self._extract_field(body, "Module"),
            "description": self._extract_field(
                body,
                "Description",
            ),
            "expected_result": self._extract_field(
                body,
                "Expected Result",
            ),
            "actual_result": self._extract_field(
                body,
                "Actual Result",
            ),
        }

    def _extract_field(self, body, field_name):
        """
        Extract the value belonging to a known field.
        """

        other_fields = [
            field
            for field in self.FIELDS
            if field != field_name
        ]

        next_field_pattern = "|".join(
            re.escape(field)
            for field in other_fields
        )

        pattern = (
            rf"(?ims)^"
            rf"{re.escape(field_name)}"
            rf"\s*:\s*"
            rf"(.*?)"
            rf"(?=^"
            rf"(?:{next_field_pattern})"
            rf"\s*:|\Z)"
        )

        match = re.search(pattern, body)

        if not match:
            return ""

        return match.group(1).strip()