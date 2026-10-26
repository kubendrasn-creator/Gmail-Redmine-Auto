from datetime import datetime, timezone
import json
from pathlib import Path

from backend.config.settings import (
    BASE_DIR,
    PROCESSED_MESSAGE_RETENTION_DAYS,
    THREAD_RETENTION_DAYS,
)
from backend.utils.logger import get_logger

logger = get_logger("storage")

PROCESSED_FILE = (
    BASE_DIR
    / "data"
    / "database"
    / "processed_messages.json"
)


class ProcessedMessageStore:
    """
    Keeps track of processed Gmail messages and Redmine mappings.

    Stores:
        - Gmail message ID & RFC Message-ID
        - In-Reply-To & References headers
        - Gmail thread ID
        - Redmine issue ID
        - Sender / forwarding metadata
        - Content fingerprint
        - Status and processing timestamp
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
            try:
                records = json.load(file)
            except (json.JSONDecodeError, ValueError):
                logger.warning(f"Could not parse {self.file_path}, starting with empty records.")
                records = []

        now_ts = datetime.now(timezone.utc).timestamp()
        log_cutoff = now_ts - (PROCESSED_MESSAGE_RETENTION_DAYS * 24 * 60 * 60)
        thread_cutoff = now_ts - (THREAD_RETENTION_DAYS * 24 * 60 * 60)

        cleaned_records = []

        for record in records:
            if not isinstance(record, dict):
                continue

            processed_at = record.get("processed_at")
            if not processed_at:
                continue

            try:
                processed_timestamp = datetime.fromisoformat(
                    processed_at
                ).timestamp()
            except (ValueError, TypeError):
                continue

            # Thread anchor records (associated with a Redmine issue)
            # are retained longer so replies to old tickets still map accurately.
            if record.get("redmine_issue_id"):
                if processed_timestamp >= thread_cutoff:
                    cleaned_records.append(record)
            else:
                if processed_timestamp >= log_cutoff:
                    cleaned_records.append(record)

        if len(cleaned_records) != len(records):
            purged_count = len(records) - len(cleaned_records)
            logger.info(f"Purged {purged_count} expired records from storage based on retention policy.")
            self._save(cleaned_records)

        return cleaned_records

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
        """
        records = self._load()

        for record in records:
            if isinstance(record, str):
                if record == message_id:
                    return {
                        "message_id": record,
                        "rfc_message_id": None,
                        "in_reply_to": None,
                        "references": None,
                        "thread_id": None,
                        "direct_sender": None,
                        "original_sender": None,
                        "original_subject": None,
                        "original_date": None,
                        "subject": "Legacy processed message",
                        "ticket_type": None,
                        "status": "already_processed",
                        "redmine_issue_id": None,
                        "content_fingerprint": None,
                        "processed_at": None,
                    }
            elif isinstance(record, dict):
                if record.get("message_id") == message_id:
                    return record

        return None

    def contains(self, message_id):
        """
        Check whether a Gmail message has already been processed.
        """
        return self.get(message_id) is not None

    def get_by_rfc_message_id(self, rfc_message_id):
        """
        Find an existing record by RFC Message-ID.
        """
        if not rfc_message_id:
            return None

        clean_id = rfc_message_id.strip()
        records = self._load()

        for record in records:
            if not isinstance(record, dict):
                continue
            stored_rfc = (record.get("rfc_message_id") or "").strip()
            if stored_rfc and (stored_rfc == clean_id or stored_rfc in clean_id or clean_id in stored_rfc):
                if record.get("redmine_issue_id"):
                    return record

        return None

    def get_by_in_reply_to(self, in_reply_to):
        """
        Find an existing processed message matching an In-Reply-To or References header.
        """
        if not in_reply_to:
            return None

        clean_in_reply_to = in_reply_to.strip()
        records = self._load()

        for record in records:
            if not isinstance(record, dict):
                continue

            stored_rfc = (record.get("rfc_message_id") or "").strip()
            stored_msg_id = (record.get("message_id") or "").strip()

            if stored_rfc and (stored_rfc == clean_in_reply_to or stored_rfc in clean_in_reply_to):
                if record.get("redmine_issue_id"):
                    return record

            if stored_msg_id and (stored_msg_id == clean_in_reply_to):
                if record.get("redmine_issue_id"):
                    return record

        return None

    def get_by_thread_id(self, thread_id):
        """
        Find an existing processed message belonging to the supplied Gmail thread.
        """
        if not thread_id:
            return None

        records = self._load()

        for record in records:
            if not isinstance(record, dict):
                continue

            if record.get("thread_id") == thread_id:
                if record.get("redmine_issue_id"):
                    return record

        return None

    def get_by_fingerprint(self, content_fingerprint):
        """
        Find a previously processed email using its content fingerprint.
        """
        if not content_fingerprint:
            return None

        records = self._load()

        for record in records:
            if not isinstance(record, dict):
                continue

            if record.get("content_fingerprint") == content_fingerprint:
                return record

        return None

    def add(
        self,
        message_id,
        subject,
        ticket_type,
        status,
        redmine_issue_id=None,
        thread_id=None,
        rfc_message_id=None,
        in_reply_to=None,
        references=None,
        direct_sender=None,
        original_sender=None,
        original_subject=None,
        original_date=None,
        content_fingerprint=None,
        issue_description=None,
    ):
        """
        Store processing information for a Gmail message.
        """
        records = self._load()

        if self.contains(message_id):
            return self.update(
                message_id,
                subject=subject,
                ticket_type=ticket_type,
                status=status,
                redmine_issue_id=redmine_issue_id,
                thread_id=thread_id,
                rfc_message_id=rfc_message_id,
                in_reply_to=in_reply_to,
                references=references,
                direct_sender=direct_sender,
                original_sender=original_sender,
                original_subject=original_subject,
                original_date=original_date,
                content_fingerprint=content_fingerprint,
            )

        record = {
            "message_id": message_id,
            "rfc_message_id": rfc_message_id,
            "in_reply_to": in_reply_to,
            "references": references,
            "thread_id": thread_id,
            "direct_sender": direct_sender,
            "original_sender": original_sender,
            "original_subject": original_subject,
            "original_date": original_date,
            "subject": subject,
            "ticket_type": ticket_type,
            "status": status,
            "redmine_issue_id": redmine_issue_id,
            "content_fingerprint": content_fingerprint,
            "issue_description": issue_description,
            "processed_at": datetime.now(timezone.utc).isoformat(),
        }

        records.append(record)
        self._save(records)
        return record

    def update(self, message_id, **fields):
        """
        Update specific fields of an existing processed record.
        """
        records = self._load()
        updated_record = None

        for record in records:
            if isinstance(record, dict) and record.get("message_id") == message_id:
                for key, val in fields.items():
                    if val is not None or key == "status":
                        record[key] = val
                record["updated_at"] = datetime.now(timezone.utc).isoformat()
                updated_record = record
                break

        if updated_record:
            self._save(records)

        return updated_record