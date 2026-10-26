import concurrent.futures
import uuid

from backend.config.settings import REDMINE_PROJECT_ID
from backend.processor.confirmation import (
    ConfirmAction,
    ConfirmResult,
    ReplyAction,
    ReplyDecision,
)
from backend.redmine.redmine_client import RedmineClient
from backend.utils.logger import _known_secrets

from backend.web.state import AppState


def redact_secrets(value):
    if isinstance(value, str):
        for secret in _known_secrets():
            value = value.replace(secret, "[REDACTED]")
        return value
    if isinstance(value, dict):
        return {key: redact_secrets(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    return value


class WebConfirmer:
    review_all = True
    preview_attachments = True
    persist_skips = False
    use_suggested_draft = True

    def __init__(self, state: AppState, dry_run=False):
        self.state = state
        self.dry_run = dry_run

    def _wait_for_decision(self, draft, reply=False):
        email = draft.get("email", {})
        classification = draft.get("classification", {})
        item_id = uuid.uuid4().hex
        attachment_plans = draft.get("attachment_plans", [])
        item = {
            "id": item_id,
            "message_id": draft.get("message_id", ""),
            "sender": email.get("sender_email", ""),
            "received_at": email.get("received_at", ""),
            "subject": classification.get("suggested_subject") or email.get("subject", ""),
            "suggested_action": draft.get("suggested_action", "REVIEW"),
            "category": classification.get("category") or classification.get("ticket_type"),
            "is_duplicate": bool(draft.get("is_duplicate")),
            "is_reply": bool(draft.get("is_reply")),
            "has_attachments": bool(attachment_plans),
            "status": "PENDING",
            "email": {
                "body": email.get("body", ""),
                "subject": email.get("subject", ""),
                "sender_email": email.get("sender_email", ""),
                "received_at": email.get("received_at", ""),
            },
            "classification": classification,
            "existing_issue": draft.get("existing_issue"),
            "existing_issue_id": draft.get("existing_issue_id"),
            "attachment_plans": [
                {
                    "file": plan.name,
                    "original_size": plan.original_size,
                    "action": plan.action.value,
                    "estimated_final_size": plan.final_size,
                    "note": plan.note,
                    "local_path": str(plan.local_path) if plan.local_path else None,
                }
                for plan in attachment_plans
            ],
            "detected_links": draft.get("detected_links", []),
            "dry_run": self.dry_run,
            "is_reply_review": reply,
            "error": draft.get("attachment_error"),
        }
        suggested_subject = (
            classification.get("suggested_subject")
            or email.get("subject", "")
        )
        suggested_description = classification.get("suggested_description") or ""
        if not suggested_description:
            suggested_description = (
                "This support ticket was automatically created from an incoming email.\n\n"
                "Please review the attached original email and validate the issue details before proceeding."
            )
        item["suggested_subject"] = suggested_subject
        item["suggested_description"] = suggested_description

        if self.dry_run and not reply:
            item["dry_run_payload"] = redact_secrets(
                RedmineClient.build_issue_payload(
                    REDMINE_PROJECT_ID,
                    suggested_subject,
                    suggested_description,
                    draft.get("message_id"),
                )
            )

        future = concurrent.futures.Future()
        with self.state.lock:
            if self.state.shutdown_requested:
                return (
                    ReplyDecision(ReplyAction.SKIP)
                    if reply else ConfirmResult(ConfirmAction.SKIP)
                )
            self.state.futures[item_id] = (future, reply)
            item["created_at"] = self.state.store._now()
            self.state.save_item(item)
            self.state.run_state = "WAITING_FOR_USER"
        self.state.publish("item_added", item)
        self.state.publish("run_state", {"state": "WAITING_FOR_USER"})
        try:
            return future.result()
        finally:
            with self.state.lock:
                self.state.futures.pop(item_id, None)
                if self.state.run_state == "WAITING_FOR_USER":
                    self.state.run_state = "CLASSIFYING"
            self.state.publish("run_state", {"state": "CLASSIFYING"})

    def confirm_new_ticket(self, draft):
        result = self._wait_for_decision(draft)
        return result if isinstance(result, ConfirmResult) else ConfirmResult(ConfirmAction.SKIP)

    def confirm_reply_attach(self, draft):
        result = self._wait_for_decision(draft, reply=True)
        return result if isinstance(result, ReplyDecision) else ReplyDecision(ReplyAction.SKIP)
