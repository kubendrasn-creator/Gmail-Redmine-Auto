from pathlib import Path
import base64
from datetime import datetime, timedelta, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from backend.config.settings import (
    GMAIL_LOOKBACK_HOURS,
    GMAIL_MAX_RESULTS,
)
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

    def get_messages(self):
        """
        Retrieve Gmail messages received within the configured lookback period.
        """
        cutoff_time = datetime.now(timezone.utc) - timedelta(
            hours=GMAIL_LOOKBACK_HOURS
        )
        cutoff_timestamp = int(cutoff_time.timestamp())

        logger.info(f"Querying Gmail messages with lookback of {GMAIL_LOOKBACK_HOURS}h (after {cutoff_timestamp})...")

        response = (
            self.service.users()
            .messages()
            .list(
                userId="me",
                q=f"after:{cutoff_timestamp}",
                maxResults=GMAIL_MAX_RESULTS,
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

                file_path = output_dir / filename

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
                        "content_type": part.get(
                            "mimeType",
                            "application/octet-stream",
                        ),
                    }
                )

            for child_part in part.get("parts", []):
                process_part(child_part)

        payload = raw_email.get("payload", {})
        process_part(payload)

        return attachments