from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from datetime import datetime, timedelta, timezone

from backend.config.settings import (
    GMAIL_LOOKBACK_HOURS,
    GMAIL_MAX_RESULTS,
)


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
            credentials = Credentials.from_authorized_user_file(
                TOKEN_FILE,
                SCOPES,
            )

        if not credentials or not credentials.valid:

            if credentials and credentials.expired and credentials.refresh_token:
                credentials.refresh(Request())

            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    CREDENTIALS_FILE,
                    SCOPES,
                )

                credentials = flow.run_local_server(
                    port=0
                )

            TOKEN_FILE.write_text(
                credentials.to_json(),
                encoding="utf-8",
            )

        return build(
            "gmail",
            "v1",
            credentials=credentials,
        )

    def get_messages(self):
        """
        Retrieve Gmail messages received within the configured
        lookback period.
        """

        cutoff_time = datetime.now(timezone.utc) - timedelta(
            hours=GMAIL_LOOKBACK_HOURS
        )

        response = (
            self.service.users()
            .messages()
            .list(
                userId="me",
                maxResults=GMAIL_MAX_RESULTS,
            )
            .execute()
        )

        messages = response.get("messages", [])

        recent_messages = []

        for message in messages:
            message_id = message["id"]

            full_message = (
                self.service.users()
                .messages()
                .get(
                    userId="me",
                    id=message_id,
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