from pathlib import Path
import base64
from datetime import datetime, timedelta, timezone
import re
from email.utils import parseaddr

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from backend.config.settings import (
    GMAIL_LOOKBACK_HOURS,
    GMAIL_MAX_RESULTS,
)
from backend.gmail.attachment_filter import AttachmentFilterStore
from backend.utils.logger import get_logger

logger = get_logger("gmail")

BASE_DIR = Path(__file__).resolve().parents[2]

CREDENTIALS_FILE = BASE_DIR / "env" / "credentials.json"
TOKEN_FILE = BASE_DIR / "env" / "token.json"


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly"
]


class GmailClient:
    """
    Handles communication with the Gmail API.
    """

    def __init__(self):
        self.last_filtered_attachments = []
        self.attachment_filter = AttachmentFilterStore()
        self.service = self._authenticate()

    def _authenticate(self):
        """
        Authenticate the application with Gmail.
        """
        credentials = None

        if TOKEN_FILE.exists():
            try:
                credentials = Credentials.from_authorized_user_file(
                    TOKEN_FILE,
                    SCOPES,
                )
            except Exception as exc:
                logger.warning(f"Failed to load token file: {exc}")

        if not credentials or not credentials.valid:
            refreshed = False
            if credentials and credentials.expired and credentials.refresh_token:
                try:
                    logger.info("Refreshing expired Gmail OAuth token...")
                    credentials.refresh(Request())
                    refreshed = True
                except Exception as exc:
                    logger.warning(f"Could not refresh token ({exc}). Starting fresh login flow...")
                    credentials = None

            if not refreshed:
                logger.info("Authenticating with Gmail OAuth flow...")
                flow = InstalledAppFlow.from_client_secrets_file(
                    CREDENTIALS_FILE,
                    SCOPES,
                )
                credentials = flow.run_local_server(port=0)

            TOKEN_FILE.write_text(
                credentials.to_json(),
                encoding="utf-8",
            )
            logger.info("Saved updated Gmail OAuth token.")

        return build(
            "gmail",
            "v1",
            credentials=credentials,
        )

    def get_messages(self, lookback_hours=None, max_results=None):
        """
        Retrieve Gmail messages received within the configured lookback period.
        """
        lookback_hours = (
            GMAIL_LOOKBACK_HOURS if lookback_hours is None else lookback_hours
        )
        max_results = GMAIL_MAX_RESULTS if max_results is None else max_results
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
        cutoff_timestamp = int(cutoff_time.timestamp())

        logger.info(f"Querying Gmail messages with lookback of {lookback_hours}h (after {cutoff_timestamp})...")

        response = (
            self.service.users()
            .messages()
            .list(
                userId="me",
                q=f"after:{cutoff_timestamp}",
                maxResults=max_results,
            )
            .execute()
        )

        messages = response.get("messages", [])
        recent_messages = []

        for message in messages:
            full_message = (
                self.service.users()
                .messages()
                .get(
                    userId="me",
                    id=message["id"],
                    format="minimal",
                )
                .execute()
            )

            internal_date = full_message.get("internalDate")
            if not internal_date:
                continue

            message_time = datetime.fromtimestamp(
                int(internal_date) / 1000,
                tz=timezone.utc,
            )

            if message_time >= cutoff_time:
                recent_messages.append(message)

        return recent_messages

    def get_message(self, message_id):
        """
        Retrieve a complete Gmail message.
        """
        return (
            self.service.users()
            .messages()
            .get(
                userId="me",
                id=message_id,
                format="full",
            )
            .execute()
        )

    def download_attachments(self, message_id, output_dir):
        """
        Download all actual file attachments from a Gmail message.

        Returns a list containing:
            {
                "path": "...",
                "filename": "...",
                "content_type": "..."
            }
        """
        raw_email = self.get_message(message_id)

        output_dir = Path(output_dir)
        output_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        attachments = []
        self.last_filtered_attachments = []
        payload = raw_email.get("payload", {})
        body_html = self._find_html_body(payload)
        headers = {
            entry.get("name", "").lower(): entry.get("value", "")
            for entry in payload.get("headers", [])
        }
        sender = parseaddr(headers.get("from", ""))[1]
        sender_domain = sender.rsplit("@", 1)[-1].lower() if "@" in sender else ""

        def process_part(part):
            filename = part.get("filename")
            body = part.get("body", {})
            attachment_id = body.get("attachmentId")

            # Only process actual named attachments.
            if filename and attachment_id:
                attachment_response = (
                    self.service.users()
                    .messages()
                    .attachments()
                    .get(
                        userId="me",
                        messageId=message_id,
                        id=attachment_id,
                    )
                    .execute()
                )

                data = attachment_response.get("data")
                if not data:
                    return

                file_data = base64.urlsafe_b64decode(
                    data + "=" * (-len(data) % 4)
                )
                content_type = part.get("mimeType", "application/octet-stream")
                part_headers = {
                    entry.get("name", "").lower(): entry.get("value", "")
                    for entry in part.get("headers", [])
                }
                content_id = part_headers.get("content-id", "").strip("<> ")
                digest = self.attachment_filter.sha256(file_data)
                metadata = {
                    "filename": str(filename),
                    "content_type": content_type,
                    "content_id": content_id,
                    "size": len(file_data),
                    "sha256": digest,
                }
                if content_type.lower().startswith("image/"):
                    if self.attachment_filter.ignored(digest):
                        self.attachment_filter.record_hit(digest)
                        self.last_filtered_attachments.append({
                            **metadata,
                            "reason": "hash-denylist",
                        })
                        return
                    if self.attachment_filter.matches_logo(file_data):
                        logger.info(
                            "Image matching configured logo filtered (filename=%s)",
                            filename,
                        )
                        self.last_filtered_attachments.append({
                            **metadata,
                            "reason": "matches-configured-logo",
                        })
                        return

                safe_name = Path(str(filename).replace("\\", "/")).name
                safe_name = re.sub(r'[<>:"/\\|?*]', "_", safe_name).rstrip(" .")
                if safe_name in ("", ".", ".."):
                    safe_name = "attachment"
                file_path = output_dir / safe_name

                # Avoid accidentally overwriting another attachment with the same filename.
                if file_path.exists():
                    stem = file_path.stem
                    suffix = file_path.suffix
                    counter = 1
                    while file_path.exists():
                        file_path = output_dir / f"{stem}_{counter}{suffix}"
                        counter += 1

                file_path.write_bytes(file_data)

                attachments.append(
                    {
                        "path": str(file_path),
                        "filename": file_path.name,
                        "content_type": content_type,
                        "sha256": digest,
                    }
                )

            for child_part in part.get("parts", []):
                process_part(child_part)

        process_part(payload)

        return attachments

    def _find_html_body(self, part):
        if part.get("mimeType") == "text/html":
            data = part.get("body", {}).get("data")
            if data:
                decoded = base64.urlsafe_b64decode(
                    data + "=" * (-len(data) % 4)
                )
                return decoded.decode("utf-8", errors="replace")
        for child in part.get("parts", []):
            found = self._find_html_body(child)
            if found:
                return found
        return ""