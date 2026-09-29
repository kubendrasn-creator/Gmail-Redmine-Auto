import base64
from email.utils import parseaddr


class EmailParser:
    """
    Converts a raw Gmail API message into
    application-friendly email data.
    """

    def parse(self, message):
        headers = message.get("payload", {}).get("headers", [])

        header_map = {
            header["name"].lower(): header["value"]
            for header in headers
        }

        sender_name, sender_email = parseaddr(
            header_map.get("from", "")
        )

        body = self._extract_body(
            message.get("payload", {})
        )

        return {
            "message_id": message.get("id"),
            "thread_id": message.get("threadId"),
            "sender_name": sender_name,
            "sender_email": sender_email,
            "subject": header_map.get("subject", ""),
            "received_at": header_map.get("date", ""),
            "body": body,
        }

    def _extract_body(self, payload):
        """
        Extract the plain-text body from a Gmail message.
        """

        body_data = payload.get("body", {}).get("data")

        if body_data:
            return self._decode_body(body_data)

        for part in payload.get("parts", []):
            if part.get("mimeType") == "text/plain":
                part_data = part.get("body", {}).get("data")

                if part_data:
                    return self._decode_body(part_data)

            if part.get("parts"):
                body = self._extract_body(part)

                if body:
                    return body

        return ""

    def _decode_body(self, data):
        """
        Decode Gmail's Base64 URL-safe encoded body.
        """

        decoded = base64.urlsafe_b64decode(
            data + "=" * (-len(data) % 4)
        )

        return decoded.decode(
            "utf-8",
            errors="replace",
        )