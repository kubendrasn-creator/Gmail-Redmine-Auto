from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from backend.utils.prompt import ask_yes_no


class ConfirmAction(str, Enum):
    APPROVE = "APPROVE"
    EDIT = "EDIT"
    REJECT = "REJECT"
    SKIP = "SKIP"


@dataclass
class ConfirmResult:
    action: ConfirmAction
    edited_subject: str | None = None
    edited_description: str | None = None
    attach_to_issue_id: int | None = None


class ReplyAction(str, Enum):
    ATTACH = "ATTACH"
    CREATE_NEW = "CREATE_NEW"
    REJECT = "REJECT"
    SKIP = "SKIP"


@dataclass
class ReplyDecision:
    action: ReplyAction
    issue_id: int | None = None
    edited_subject: str | None = None
    edited_description: str | None = None


class Confirmer(Protocol):
    review_all: bool
    preview_attachments: bool
    persist_skips: bool
    use_suggested_draft: bool

    def confirm_new_ticket(self, draft: dict) -> ConfirmResult:
        ...

    def confirm_reply_attach(self, draft: dict) -> ReplyDecision:
        ...


class CliConfirmer:
    """Terminal implementation of the review decisions used by the CLI."""

    review_all = False
    preview_attachments = False
    persist_skips = True
    use_suggested_draft = False

    def confirm_new_ticket(self, draft: dict) -> ConfirmResult:
        message_id = draft.get("message_id", "")
        subject = draft.get("subject", "No Subject")
        issue_id = draft.get("existing_issue_id")

        if draft.get("is_duplicate") and issue_id:
            print()
            print("Possible duplicate detected.")
            print(f"Message ID: {message_id}")
            print(f"Subject: {subject}")
            print(f"Existing Redmine Ticket: #{issue_id}")
            print("This email appears to be a duplicate.")
            print()
            if not ask_yes_no("Create another ticket anyway?"):
                return ConfirmResult(ConfirmAction.REJECT)
            return ConfirmResult(ConfirmAction.APPROVE)

        print()
        print("New ticket detected.")
        print(f"Subject: {subject}")
        print(f"Sender: {draft.get('sender_email', '')}")
        print()
        if not ask_yes_no("Create Redmine ticket for this email?"):
            return ConfirmResult(ConfirmAction.SKIP)
        print("Creating Redmine ticket...")
        return ConfirmResult(ConfirmAction.APPROVE)

    def confirm_reply_attach(self, draft: dict) -> ReplyDecision:
        message_id = draft.get("message_id", "")
        subject = draft.get("subject", "No Subject")
        thread_id = draft.get("thread_id")
        reply_id = draft.get("existing_issue_id")

        print()
        if draft.get("has_fresh_structured_fields"):
            print("This email is in an existing Gmail conversation, but it appears to contain new ticket information.")
            print(f"Message ID: {message_id}")
            print(f"Subject: {subject}")
            print(f"Existing Redmine Ticket: #{reply_id}")
            print()
            if ask_yes_no("Create a NEW Redmine ticket instead?"):
                return ReplyDecision(ReplyAction.CREATE_NEW)
            if ask_yes_no(f"Do you want to attach this email to Redmine ticket #{reply_id}?"):
                return ReplyDecision(ReplyAction.ATTACH, issue_id=int(reply_id))
            return ReplyDecision(ReplyAction.SKIP)

        print("Possible reply detected.")
        print(f"Message ID: {message_id}")
        print(f"Subject: {subject}")
        print(f"Gmail Thread: {thread_id}")
        print(f"Existing Redmine Ticket: #{reply_id}")
        print()
        if ask_yes_no(f"Do you want to attach this email to Redmine ticket #{reply_id}?"):
            return ReplyDecision(ReplyAction.ATTACH, issue_id=int(reply_id))
        if ask_yes_no("Do you want to create this as a NEW Redmine ticket instead?"):
            return ReplyDecision(ReplyAction.CREATE_NEW)
        return ReplyDecision(ReplyAction.SKIP)
