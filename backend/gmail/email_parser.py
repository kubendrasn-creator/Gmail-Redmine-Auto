import base64
from dataclasses import asdict
import html
import re
from email.utils import parseaddr

from backend.gmail.email_cleaner import build_ai_input, parse_thread


class EmailParser:
    """
    Converts a raw Gmail API message into application-friendly email data.
    Extracts RFC headers, plain-text / HTML fallback body, forwarded content,
    and structured ticket fields.
    """

    STRUCTURED_FIELDS = {
        "center": "Center",
        "enterprise": "Enterprise",
        "module": "Module",
        "description": "Description",
        "issue": "Issue",
        "steps": "Steps to Reproduce",
        "expected_result": "Expected Result",
        "actual_result": "Actual Result",
    }

    FORWARD_MARKERS = (
        "---------- forwarded message ---------",
        "---------- forwarded message ----------",
        "----- forwarded message -----",
        "--- original message ---",
        "----- original message -----",
        "begin forwarded message",
        "________________________________",
    )

    FORWARD_HEADER_PATTERNS = {
        "from": re.compile(
            r"^\s*from\s*:\s*(.+)$",
            re.IGNORECASE,
        ),
        "date": re.compile(
            r"^\s*(?:date|sent)\s*:\s*(.+)$",
            re.IGNORECASE,
        ),
        "subject": re.compile(
            r"^\s*subject\s*:\s*(.+)$",
            re.IGNORECASE,
        ),
    }

    REPLY_QUOTE_PATTERNS = [
        re.compile(r"^On\s+.+,\s+.+\s+wrote:\s*$", re.IGNORECASE),
        re.compile(r"^On\s+.+wrote:\s*$", re.IGNORECASE),
        re.compile(r"^-{3,}\s*original message\s*-{3,}$", re.IGNORECASE),
        re.compile(r"^_{10,}$"),
    ]

    def parse(self, message):
        headers = message.get("payload", {}).get("headers", [])

        header_map = {
            header["name"].lower(): header["value"]
            for header in headers
        }

        sender_name, sender_email = parseaddr(
            header_map.get("from", "")
        )

        rfc_message_id = header_map.get("message-id", "").strip()
        in_reply_to = header_map.get("in-reply-to", "").strip()
        references = header_map.get("references", "").strip()

        body = self._extract_body(
            message.get("payload", {})
        )
        body_html = self._find_body_by_mimetype(
            message.get("payload", {}),
            "text/html",
        ) or ""
        thread_messages = parse_thread(body)

        # Extract unquoted / new body (content written before any reply quotation markers)
        new_body, quoted_body = self._split_quoted_content(body)

        # Extract forwarded content
        forwarded = self._extract_forwarded_content(body)

        classification_body = build_ai_input(thread_messages)

        structured_fields = self._extract_structured_fields(
            classification_body
        )

        # Extract structured fields found strictly in the unquoted / fresh portion
        unquoted_structured_fields = self._extract_structured_fields(
            new_body if new_body else body
        )

        has_fresh_structured_fields = bool(
            (
                unquoted_structured_fields.get("center")
                or unquoted_structured_fields.get("enterprise")
            )
            and unquoted_structured_fields.get("module")
            or unquoted_structured_fields.get("description")
            or unquoted_structured_fields.get("issue")
        )

        return {
            "message_id": message.get("id"),
            "thread_id": message.get("threadId"),
            "rfc_message_id": rfc_message_id,
            "in_reply_to": in_reply_to,
            "references": references,

            # Direct Gmail sender
            "sender_name": sender_name,
            "sender_email": sender_email,

            "subject": header_map.get("subject", ""),
            "to": header_map.get("to", ""),
            "cc": header_map.get("cc", ""),
            "received_at": header_map.get("date", ""),
            "body": body,
            "body_html": body_html,
            "thread_messages": [asdict(item) for item in thread_messages],
            "boilerplate_removed": any(
                item.boilerplate_removed for item in thread_messages
            ),
            "new_body": new_body,
            "quoted_body": quoted_body,

            # Content used for ticket detection/extraction
            "classification_body": classification_body,

            # Forwarded email information
            "is_forwarded": forwarded["is_forwarded"],
            "original_sender": forwarded["original_sender"],
            "original_subject": forwarded["original_subject"],
            "original_date": forwarded["original_date"],
            "original_body": forwarded["original_body"],

            # Structured support fields (global & unquoted)
            "center": structured_fields["center"],
            "enterprise": structured_fields["enterprise"],
            "module": structured_fields["module"],
            "description": structured_fields["description"],
            "issue": structured_fields["issue"],
            "steps": structured_fields["steps"],
            "expected_result": structured_fields["expected_result"],
            "actual_result": structured_fields["actual_result"],

            "unquoted_structured_fields": unquoted_structured_fields,
            "has_fresh_structured_fields": has_fresh_structured_fields,
        }

    def _extract_body(self, payload):
        """
        Extract plain-text body from payload. If no plain-text is found,
        fallback to converting HTML body to readable text.
        """
        plain_text = self._find_body_by_mimetype(payload, "text/plain")
        if plain_text and plain_text.strip():
            return plain_text

        html_text = self._find_body_by_mimetype(payload, "text/html")
        if html_text and html_text.strip():
            return self._html_to_plain_text(html_text)

        # Direct payload body fallback
        body_data = payload.get("body", {}).get("data")
        if body_data:
            decoded = self._decode_body(body_data)
            if payload.get("mimeType") == "text/html":
                return self._html_to_plain_text(decoded)
            return decoded

        return ""

    def _find_body_by_mimetype(self, payload, mime_type):
        """
        Recursively look for parts matching a specific MIME type.
        """
        if payload.get("mimeType") == mime_type:
            body_data = payload.get("body", {}).get("data")
            if body_data:
                return self._decode_body(body_data)

        for part in payload.get("parts", []):
            result = self._find_body_by_mimetype(part, mime_type)
            if result:
                return result

        return None

    def _html_to_plain_text(self, html_content):
        """
        Convert HTML content to readable plain text with line breaks preserved.
        """
        if not html_content:
            return ""

        text = html_content
        # Remove script and style blocks
        text = re.sub(r"(?is)<script.*?</script>", "", text)
        text = re.sub(r"(?is)<style.*?</style>", "", text)

        # Replace common block elements / line breaks with newlines
        text = re.sub(r"(?i)<br\s*/?>", "\n", text)
        text = re.sub(r"(?i)</(?:p|div|tr|h[1-6]|li)>", "\n", text)
        text = re.sub(r"(?i)<li[^>]*>", "• ", text)

        # Strip remaining HTML tags
        text = re.sub(r"<[^>]+>", "", text)

        # Unescape HTML entities (e.g. &nbsp;, &amp;, &lt;)
        text = html.unescape(text)

        # Clean up whitespace
        lines = [line.strip() for line in text.splitlines()]
        cleaned = "\n".join(lines)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    def _split_quoted_content(self, body):
        """
        Split an email body into the newly written content (unquoted) and quoted reply content.
        """
        if not body:
            return "", ""

        lines = body.splitlines()
        split_index = None

        for idx, line in enumerate(lines):
            stripped = line.strip()

            # Check quote header patterns
            for pattern in self.REPLY_QUOTE_PATTERNS:
                if pattern.match(stripped):
                    split_index = idx
                    break
            if split_index is not None:
                break

            # If line begins with quote character '>'
            if stripped.startswith(">"):
                split_index = idx
                break

        if split_index is not None:
            new_content = "\n".join(lines[:split_index]).strip()
            quoted_content = "\n".join(lines[split_index:]).strip()
            return new_content, quoted_content

        return body.strip(), ""

    def _decode_body(self, data):
        """
        Decode Gmail's Base64 URL-safe encoded body.
        """
        decoded = base64.urlsafe_b64decode(
            data + "=" * (-len(data) % 4)
        )
        return decoded.decode("utf-8", errors="replace")

    def _extract_forwarded_content(self, body):
        """
        Detect and extract information from a forwarded email.
        """
        result = {
            "is_forwarded": False,
            "original_sender": None,
            "original_subject": None,
            "original_date": None,
            "original_body": None,
        }

        if not body:
            return result

        lines = body.splitlines()
        forward_start = None

        for index, line in enumerate(lines):
            normalized_line = line.strip().lower()
            if normalized_line in self.FORWARD_MARKERS:
                forward_start = index + 1
                break

        if forward_start is None:
            return result

        result["is_forwarded"] = True
        original_lines = lines[forward_start:]
        original_body_lines = []
        header_section = True

        for line in original_lines:
            stripped_line = line.strip()

            if header_section:
                matched_from = self.FORWARD_HEADER_PATTERNS["from"].match(stripped_line)
                if matched_from:
                    _, sender_email = parseaddr(matched_from.group(1).strip())
                    result["original_sender"] = sender_email or matched_from.group(1).strip()
                    continue

                matched_date = self.FORWARD_HEADER_PATTERNS["date"].match(stripped_line)
                if matched_date:
                    result["original_date"] = matched_date.group(1).strip()
                    continue

                matched_subject = self.FORWARD_HEADER_PATTERNS["subject"].match(stripped_line)
                if matched_subject:
                    result["original_subject"] = matched_subject.group(1).strip()
                    continue

                if stripped_line:
                    header_section = False

            if not header_section:
                original_body_lines.append(line)

        original_body = "\n".join(original_body_lines).strip()
        if original_body:
            result["original_body"] = original_body

        return result

    def _extract_structured_fields(self, body):
        """
        Extract structured support-request fields.
        """
        fields = {
            key: None
            for key in self.STRUCTURED_FIELDS
        }

        if not body:
            return fields

        lines = [line.strip() for line in body.splitlines()]
        current_field = None
        current_value = []

        def save_current_field():
            nonlocal current_field, current_value
            if current_field is None:
                return
            value = "\n".join(current_value).strip()
            if value:
                fields[current_field] = value
            current_field = None
            current_value = []

        for line in lines:
            if not line:
                if current_field and current_value:
                    current_value.append("")
                continue

            matched_field = self._match_field(line)
            if matched_field:
                save_current_field()
                field_name, inline_value = matched_field
                current_field = field_name
                if inline_value:
                    current_value = [inline_value]
                else:
                    current_value = []
                continue

            if current_field:
                current_value.append(line)

        save_current_field()

        for field_name, value in fields.items():
            if value:
                value = re.sub(r"\n{3,}", "\n\n", value)
                fields[field_name] = value.strip()

        return fields

    def _match_field(self, line):
        """
        Determine whether a line starts a known structured field.
        """
        for field_name, display_name in self.STRUCTURED_FIELDS.items():
            pattern = rf"^\s*{re.escape(display_name)}\s*:\s*(.*)$"
            match = re.match(pattern, line, flags=re.IGNORECASE)
            if match:
                return field_name, match.group(1).strip()
        return None