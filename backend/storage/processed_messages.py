from datetime import datetime, timezone
import json
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]
PROCESSED_FILE = (
    BASE_DIR 
    / "data" 
    / "database" 
    / "processed_messages.json"
)


class ProcessedMessageStore:
    """
    Keeps track of Gmail messages that have already been processed.
    """

    def __init__(self):
        self.file_path = PROCESSED_FILE

        self.file_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        if not self.file_path.exists():
            self._save([])

    def _load(self):
        with self.file_path.open(
            "r",
            encoding="utf-8",
        ) as file:
            return json.load(file)

    def _save(self, records):
        with self.file_path.open(
            "w",
            encoding="utf-8",
        ) as file:
            json.dump(
                records,
                file,
                indent=2,
                ensure_ascii=False,
            )

    def get(self, message_id):
        """
        Return the stored record for a Gmail message.

        Supports both the old format:
            "message_id"

        and the new format:
            {
                "message_id": "...",
                "subject": "...",
                ...
            }
        """

        records = self._load()

        for record in records:

            # Old format
            if isinstance(record, str):
                if record == message_id:
                    return {
                        "message_id": record,
                        "subject": "Legacy processed message",
                        "ticket_type": None,
                        "status": "already_processed",
                        "redmine_issue_id": None,
                        "processed_at": None,
                    }

            # New format
            elif isinstance(record, dict):
                if record.get("message_id") == message_id:
                    return record

        return None

    def contains(self, message_id):
        """
        Check whether a Gmail message has already been processed.
        """

        return self.get(message_id) is not None

    def add(
        self,
        message_id,
        subject,
        ticket_type,
        status,
        redmine_issue_id=None,
    ):
        """
        Store processing information for a Gmail message.
        """

        records = self._load()

        if self.contains(message_id):
            return

        record = {
            "message_id": message_id,
            "subject": subject,
            "ticket_type": ticket_type,
            "status": status,
            "redmine_issue_id": redmine_issue_id,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }

        records.append(record)

        self._save(records)