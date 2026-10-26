from pathlib import Path

import requests
import pytest

from backend.confirm import ConfirmAction, ConfirmResult
from backend.processor import ticket_processor as processor_module
from backend.processor.attachments import AttachmentPipeline
from backend.processor.ticket_processor import TicketProcessor
from backend.redmine.redmine_client import RedmineClient
from backend.utils.links import LinkDetector
from backend.utils.retry import RedmineUploadError


class FakeStore:
    def __init__(self):
        self.records = []
        self.updates = []

    def get(self, message_id):
        return next(
            (
                record for record in reversed(self.records)
                if record.get("message_id") == message_id
            ),
            None,
        )

    def get_by_fingerprint(self, fingerprint):
        return None

    def get_by_in_reply_to(self, value):
        return None

    def get_by_rfc_message_id(self, value):
        return None

    def get_by_thread_id(self, value):
        return None

    def add(self, **record):
        current = self.get(record["message_id"])
        if current is not None:
            current.update(record)
            return current
        self.records.append(record)
        return record

    def update(self, message_id, **fields):
        self.updates.append((message_id, fields))
        record = self.get(message_id)
        if record is not None:
            record.update(fields)
        return record


class FakeClassifier:
    def classify(self, email):
        return {
            "is_ticket": True,
            "ticket_type": "issue",
            "source": "ai",
            "category": "issue",
            "suggested_subject": "AI subject",
            "suggested_description": "AI description",
            "issue_evidence": "It fails",
            "missing_fields": [],
            "reasoning": "A reported failure",
            "confidence": 0.95,
        }


class FakeConfirmer:
    review_all = True
    preview_attachments = False
    persist_skips = False
    use_suggested_draft = True

    def __init__(self, result):
        self.result = result

    def confirm_new_ticket(self, draft):
        return self.result

    def confirm_reply_attach(self, draft):
        raise AssertionError("Not a reply")


class FakeRedmine:
    def __init__(self):
        self.created = []

    def create_issue(self, **payload):
        self.created.append(payload)
        return {"issue": {"id": 456, "subject": payload["subject"]}}

    build_issue_payload = staticmethod(RedmineClient.build_issue_payload)


def make_processor(confirmer, dry_run=False):
    processor = TicketProcessor.__new__(TicketProcessor)
    processor.gmail = object()
    processor.parser = object()
    processor.classifier = FakeClassifier()
    processor.pdf_generator = object()
    processor.redmine = FakeRedmine()
    processor.processed_store = FakeStore()
    processor.dry_run = dry_run
    processor.confirmer = confirmer
    processor.attachment_pipeline = AttachmentPipeline(max_size_mb=1)
    processor.link_detector = LinkDetector()
    processor._complete_new_ticket_processing = lambda **kwargs: {
        "action": "ticket_created",
        "status": "completed",
        "message_id": kwargs["message_id"],
        "subject": kwargs["email"]["subject"],
        "redmine_issue_id": kwargs["issue_id"],
    }
    return processor


def sample_email():
    return {
        "message_id": "gmail123",
        "thread_id": "thread123",
        "rfc_message_id": "<gmail123@example.test>",
        "in_reply_to": "",
        "references": "",
        "sender_email": "reporter@example.test",
        "sender_name": "Reporter",
        "subject": "Original subject",
        "received_at": "Mon, 26 Oct 2026 10:00:00 +0000",
        "body": "It fails",
        "has_fresh_structured_fields": False,
    }


def test_edited_approval_passes_fields_to_redmine_and_records_message():
    processor = make_processor(
        FakeConfirmer(ConfirmResult(
            ConfirmAction.EDIT,
            edited_subject="Edited subject",
            edited_description="Edited description",
        ))
    )

    result = processor.process_message("gmail123", email=sample_email())

    assert result["action"] == "ticket_created"
    assert processor.redmine.created == [{
        "project_id": processor_module.REDMINE_PROJECT_ID,
        "subject": "Edited subject",
        "description": "Edited description",
        "message_id": "gmail123",
    }]
    record = processor.processed_store.records[0]
    assert record["status"] == "ticket_created"
    assert record["message_id"] == "gmail123"


def test_reject_is_persisted_but_skip_is_not():
    rejected = make_processor(FakeConfirmer(ConfirmResult(ConfirmAction.REJECT)))
    rejected.process_message("gmail123", email=sample_email())
    assert rejected.processed_store.records[0]["status"] == "rejected"

    skipped = make_processor(FakeConfirmer(ConfirmResult(ConfirmAction.SKIP)))
    skipped.process_message("gmail123", email=sample_email())
    assert skipped.processed_store.records == []


def test_dry_run_creates_preview_without_redmine_or_processed_store_writes():
    processor = make_processor(
        FakeConfirmer(ConfirmResult(ConfirmAction.APPROVE)),
        dry_run=True,
    )
    result = processor.process_message("gmail123", email=sample_email())

    assert result["action"] == "ticket_preview"
    assert result["preview"]["issue"]["subject"] == "AI subject"
    assert "gmail-message-id: gmail123" in result["preview"]["issue"]["description"]
    assert processor.redmine.created == []
    assert processor.processed_store.records == []


def test_attachment_upload_413_is_reported_and_local_file_preserved(tmp_path, monkeypatch):
    attachment_root = tmp_path / "attachment-root"
    pdf_root = tmp_path / "generated"
    monkeypatch.setattr(processor_module, "EMAIL_ATTACHMENTS_DIR", attachment_root)
    monkeypatch.setattr(processor_module, "GENERATED_EMAILS_DIR", pdf_root)

    source_dir = attachment_root / "pending" / "gmail123"
    source_dir.mkdir(parents=True)
    source_file = source_dir / "large.bin"
    source_file.write_bytes(b"attachment")

    class FakeGmail:
        def download_attachments(self, message_id, output_dir):
            return [{
                "path": str(source_file),
                "filename": source_file.name,
                "content_type": "application/octet-stream",
            }]

    class FakePdf:
        def generate(self, email, output_path):
            Path(output_path).write_bytes(b"PDF")

    class UploadRedmine:
        def __init__(self):
            self.notes = []
            self.descriptions = []

        @staticmethod
        def _message_marker(message_id):
            return f"<!-- gmail-message-id: {message_id} -->"

        def upload_attachment(self, path):
            if str(path).endswith(".pdf"):
                return "pdf-token"
            raise RedmineUploadError(413, "<html>payload too large</html>")

        def attach_file_to_issue(self, issue_id, attachment):
            return None

        def add_issue_note(self, issue_id, note):
            self.notes.append(note)

        def update_issue_description(self, issue_id, description):
            self.descriptions.append(description)

    processor = TicketProcessor.__new__(TicketProcessor)
    processor.gmail = FakeGmail()
    processor.pdf_generator = FakePdf()
    processor.redmine = UploadRedmine()
    processor.processed_store = FakeStore()
    processor.attachment_pipeline = AttachmentPipeline(max_size_mb=1)

    result = processor._complete_new_ticket_processing(
        message_id="gmail123",
        email=sample_email(),
        issue_id=456,
        description="Issue description",
    )

    assert result["action"] == "ticket_created"
    assert len(processor.redmine.notes) == 1
    assert processor.redmine.notes[0].startswith("Attachment handling:")
    assert str(source_file) in processor.redmine.notes[0]
    assert processor.redmine.descriptions[0].startswith(
        "[Attachment missing - see note]"
    )
    assert source_file.exists()


def test_postprocessing_retry_does_not_duplicate_uploads_after_note_failure(
    tmp_path,
    monkeypatch,
):
    attachment_root = tmp_path / "attachment-root"
    pdf_root = tmp_path / "generated"
    monkeypatch.setattr(processor_module, "EMAIL_ATTACHMENTS_DIR", attachment_root)
    monkeypatch.setattr(processor_module, "GENERATED_EMAILS_DIR", pdf_root)
    source_dir = attachment_root / "pending" / "gmail123"
    source_dir.mkdir(parents=True)
    source_file = source_dir / "original.txt"
    source_file.write_bytes(b"email attachment")

    class FakePdf:
        def generate(self, email, output_path):
            Path(output_path).write_bytes(b"PDF")

    class RetryRedmine:
        def __init__(self):
            self.attachments = []
            self.notes = []
            self.note_attempts = 0

        @staticmethod
        def _message_marker(message_id):
            return f"<!-- gmail-message-id: {message_id} -->"

        def upload_attachment(self, path):
            return f"token-{Path(path).name}"

        def attach_file_to_issue(self, issue_id, attachment):
            self.attachments.append(attachment["filename"])

        def add_issue_note(self, issue_id, note):
            self.note_attempts += 1
            if self.note_attempts == 1:
                raise requests.Timeout("response timed out before the note was saved")
            self.notes.append(note)

        def get_issue(self, issue_id, include=None):
            return {"journals": [{"notes": note} for note in self.notes]}

        def update_issue_description(self, issue_id, description):
            raise AssertionError("No attachment is expected to be skipped")

    class NoDownloadGmail:
        def download_attachments(self, message_id, output_dir):
            raise AssertionError("Completed attachment work should not be repeated")

    processor = TicketProcessor.__new__(TicketProcessor)
    processor.gmail = NoDownloadGmail()
    processor.pdf_generator = FakePdf()
    processor.redmine = RetryRedmine()
    processor.processed_store = FakeStore()
    processor.attachment_pipeline = AttachmentPipeline(max_size_mb=1)
    processor.processed_store.add(
        message_id="gmail123",
        subject="Issue",
        ticket_type="Support",
        status="ticket_created",
        redmine_issue_id=456,
    )
    attachment = {
        "path": str(source_file),
        "filename": "original.txt",
        "content_type": "text/plain",
    }

    with pytest.raises(requests.Timeout):
        processor._complete_new_ticket_processing(
            message_id="gmail123",
            email=sample_email(),
            issue_id=456,
            downloaded_attachments=[attachment],
            description="Issue description",
        )

    processor._complete_new_ticket_processing(
        message_id="gmail123",
        email=sample_email(),
        issue_id=456,
        description="Issue description",
    )

    assert processor.redmine.attachments == ["456.pdf", "original.txt"]
    assert processor.redmine.note_attempts == 2
    assert len(processor.redmine.notes) == 1
