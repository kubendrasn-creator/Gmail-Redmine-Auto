from pathlib import Path
import re

import requests

from backend.gmail.gmail_client import GmailClient
from backend.gmail.email_parser import EmailParser
from backend.gmail.email_pdf_generator import EmailPdfGenerator
from backend.redmine.redmine_client import RedmineClient
from backend.ticketing.ticket_classifier import TicketClassifier
from backend.storage.processed_messages import ProcessedMessageStore
from backend.storage.message_fingerprint import create_content_fingerprint
from backend.config.settings import (
    GMAIL_ALLOWED_SENDERS,
    REDMINE_PROJECT_ID,
    REDMINE_URL,
    GENERATED_EMAILS_DIR,
    EMAIL_ATTACHMENTS_DIR,
)
from backend.confirm import (
    CliConfirmer,
    Confirmer,
    ConfirmAction,
    ConfirmResult,
    ReplyAction,
    ReplyDecision,
)
from backend.utils.logger import get_logger
from backend.processor.attachments import AttachmentPipeline
from backend.utils.links import LinkDetector
from backend.ticketing.ticket_classifier import decide
from backend.utils.retry import RedmineUploadError

logger = get_logger("processor")


class TicketProcessor:
    """
    Orchestrates the complete Gmail -> Redmine ticket workflow.

    Implements intelligent decision logic:
        - Asks for user confirmation on all new ticket creations for safety.
        - Asks for user confirmation on replies, duplicate fingerprints, and same-thread new issues.
        - Two-phase persistence ensures Redmine ticket IDs are never lost even if PDF/attachments fail.
    """

    def __init__(self, dry_run: bool = False, confirmer: Confirmer | None = None):
        self.gmail = GmailClient()
        self.parser = EmailParser()
        self.classifier = TicketClassifier()
        self.pdf_generator = EmailPdfGenerator()
        self.redmine = RedmineClient(dry_run=dry_run)
        self.processed_store = ProcessedMessageStore()
        self.dry_run = dry_run
        self.confirmer: Confirmer = confirmer or CliConfirmer()
        self.attachment_pipeline = AttachmentPipeline()
        self.link_detector = LinkDetector()

    def process_message(self, message_id, email=None, interactive=True):
        """
        Process one message, delegating all human decisions to the configured confirmer.
        """
        if email is None:
            raw_email = self.gmail.get_message(message_id)
            email = self.parser.parse(raw_email)

        subject = email.get("subject", "No Subject")
        thread_id = email.get("thread_id")
        in_reply_to = email.get("in_reply_to")
        references = email.get("references")

        logger.info(f"Processing message {message_id} | Subject: '{subject}'")

        existing_record = self.processed_store.get(message_id)
        if existing_record:
            status = existing_record.get("status")
            issue_id = existing_record.get("redmine_issue_id")

            if status in (
                "completed", "reply_attached", "ignored", "sender_not_allowed",
                "duplicate", "skipped_by_user", "not_a_ticket", "rejected",
            ):
                logger.info(
                    f"Message {message_id} already processed with status '{status}' "
                    f"(Redmine Issue: #{issue_id if issue_id else 'N/A'}). Skipping."
                )
                return {
                    "action": "already_processed",
                    "status": status,
                    "message_id": message_id,
                    "thread_id": thread_id,
                    "subject": subject,
                    "redmine_issue_id": issue_id,
                }
            elif status in ("ticket_created", "ticket_created_pdf_failed", "ticket_created_attachment_failed") and issue_id:
                logger.info(
                    f"Resuming incomplete processing for message {message_id} (Redmine #{issue_id}, status={status})."
                )
                return self._complete_new_ticket_processing(
                    message_id=message_id,
                    email=email,
                    issue_id=issue_id,
                )

        fingerprint = create_content_fingerprint(email)
        duplicate_record = self.processed_store.get_by_fingerprint(fingerprint)

        reply_match = self.processed_store.get_by_in_reply_to(in_reply_to)
        if not reply_match and references:
            for ref in references.split():
                ref_match = self.processed_store.get_by_rfc_message_id(ref)
                if ref_match:
                    reply_match = ref_match
                    break

        thread_match = self.processed_store.get_by_thread_id(thread_id) if thread_id else None
        target_issue_id = (reply_match or thread_match or {}).get("redmine_issue_id")
        has_fresh_structured_fields = email.get("has_fresh_structured_fields", False)
        is_duplicate = bool(
            duplicate_record
            and duplicate_record.get("redmine_issue_id")
        )
        if is_duplicate:
            target_issue_id = duplicate_record["redmine_issue_id"]
        is_reply = bool(target_issue_id and (reply_match or thread_match))
        review_all = self.confirmer.review_all
        if (is_reply or is_duplicate) and not review_all:
            classification = {
                "is_ticket": True,
                "ticket_type": "Support",
                "source": "existing-match",
                "category": "followup" if is_reply else "issue",
                "suggested_subject": subject,
                "suggested_description": None,
            }
        else:
            classification = self.classifier.classify(email)
        logger.info(
            f"Classification: is_ticket={classification['is_ticket']}, "
            f"type={classification['ticket_type']}"
        )

        # Log AI reasoning if available
        if classification.get("ai_classification") and classification.get("reasoning"):
            logger.info(f"AI Reasoning: {classification['reasoning']}")

        if not classification["is_ticket"] and not review_all and not is_duplicate and not is_reply:
            logger.info(f"Message {message_id} is not a support ticket. Recording and skipping.")
            self._record_decision(
                message_id,
                email,
                fingerprint,
                "not_a_ticket",
            )
            return {
                "action": "not_a_ticket",
                "status": "not_a_ticket",
                "message_id": message_id,
                "thread_id": thread_id,
                "subject": subject,
                "redmine_issue_id": None,
            }

        draft = self._build_review_draft(
            message_id,
            email,
            classification,
            is_duplicate=is_duplicate,
            is_reply=is_reply,
            existing_issue_id=target_issue_id,
            has_fresh_structured_fields=has_fresh_structured_fields,
        )

        if is_reply and not is_duplicate:
            if interactive:
                decision = self.confirmer.confirm_reply_attach(draft)
            else:
                decision = ReplyDecision(ReplyAction.CREATE_NEW)
            if decision.action == ReplyAction.ATTACH:
                return self._process_existing_thread(
                    email,
                    decision.issue_id or target_issue_id,
                    fingerprint,
                    draft.get("downloaded_attachments"),
                )
            if decision.action == ReplyAction.SKIP:
                self._cleanup_review_attachments(draft)
                return self._handle_skip(message_id, email, fingerprint)
            if decision.action == ReplyAction.REJECT:
                self._cleanup_review_attachments(draft)
                self._record_decision(
                    message_id,
                    email,
                    fingerprint,
                    "rejected",
                    ticket_type=classification.get("ticket_type"),
                )
                return {
                    "action": "rejected",
                    "status": "rejected",
                    "message_id": message_id,
                    "thread_id": thread_id,
                    "subject": subject,
                    "redmine_issue_id": None,
                }
            return self._process_new_ticket(
                message_id,
                email,
                fingerprint,
                classification,
                decision.edited_subject,
                decision.edited_description,
                draft.get("downloaded_attachments"),
            )

        if interactive:
            result = self.confirmer.confirm_new_ticket(draft)
        else:
            result = ConfirmResult(ConfirmAction.APPROVE)

        if result.action in (ConfirmAction.APPROVE, ConfirmAction.EDIT):
            if result.attach_to_issue_id:
                return self._process_existing_thread(
                    email,
                    result.attach_to_issue_id,
                    fingerprint,
                    draft.get("downloaded_attachments"),
                )
            return self._process_new_ticket(
                message_id,
                email,
                fingerprint,
                classification,
                result.edited_subject,
                result.edited_description,
                draft.get("downloaded_attachments"),
            )
        if result.action == ConfirmAction.REJECT:
            status = "rejected" if review_all else (
                "duplicate" if is_duplicate else "skipped_by_user"
            )
            self._record_decision(
                message_id,
                email,
                fingerprint,
                status,
                target_issue_id if is_duplicate else None,
                classification.get("ticket_type"),
            )
            self._cleanup_review_attachments(draft)
            return {
                "action": status,
                "status": status,
                "message_id": message_id,
                "thread_id": thread_id,
                "subject": subject,
                "redmine_issue_id": target_issue_id if is_duplicate else None,
            }
        self._cleanup_review_attachments(draft)
        return self._handle_skip(message_id, email, fingerprint)

    def _build_review_draft(
        self,
        message_id,
        email,
        classification,
        *,
        is_duplicate=False,
        is_reply=False,
        existing_issue_id=None,
        has_fresh_structured_fields=False,
    ):
        draft = {
            "message_id": message_id,
            "subject": email.get("subject", "No Subject"),
            "sender_email": email.get("sender_email", ""),
            "thread_id": email.get("thread_id"),
            "email": email,
            "classification": classification,
            "is_duplicate": is_duplicate,
            "is_reply": is_reply,
            "existing_issue_id": existing_issue_id,
            "has_fresh_structured_fields": has_fresh_structured_fields,
            "detected_links": [
                {"url": link.url, "type": link.link_type}
                for link in self.link_detector.detect_links(email.get("body", ""))
            ],
            "suggested_action": decide(
                classification,
                {
                    "is_duplicate": is_duplicate,
                    "is_reply": is_reply,
                    "sender_allowed": (
                        email.get("sender_email", "").strip().lower()
                        in GMAIL_ALLOWED_SENDERS
                    ),
                },
            ),
        }
        if existing_issue_id and self.confirmer.review_all:
            try:
                issue = self.redmine.get_issue(existing_issue_id)
                draft["existing_issue"] = {
                    "id": existing_issue_id,
                    "subject": issue.get("subject", ""),
                    "url": (
                        f"{REDMINE_URL.rstrip('/')}/issues/{existing_issue_id}"
                        if REDMINE_URL else None
                    ),
                }
            except Exception as exc:
                logger.warning(
                    "Could not fetch existing Redmine issue #%s for review: %s",
                    existing_issue_id,
                    exc,
                )
        if self.confirmer.preview_attachments:
            try:
                draft["downloaded_attachments"] = self._download_for_review(message_id)
                draft["attachment_plans"] = self.attachment_pipeline.plan_attachments(
                    [Path(attachment["path"]) for attachment in draft["downloaded_attachments"]]
                )
            except Exception as exc:
                logger.exception(
                    "Could not prepare attachments for Gmail message %s",
                    message_id,
                )
                draft["downloaded_attachments"] = None
                draft["attachment_plans"] = []
                draft["attachment_error"] = str(exc)
        return draft

    def _download_for_review(self, message_id):
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", message_id)
        directory = EMAIL_ATTACHMENTS_DIR / "pending" / safe_id
        return self.gmail.download_attachments(message_id, directory)

    def _cleanup_review_attachments(self, draft):
        attachments = draft.get("downloaded_attachments", [])
        if attachments:
            self._cleanup_directory(Path(attachments[0]["path"]).parent)

    def _record_decision(
        self,
        message_id,
        email,
        fingerprint,
        status,
        issue_id=None,
        ticket_type=None,
    ):
        if self.dry_run:
            return
        self.processed_store.add(
            message_id=message_id,
            subject=email.get("subject", "No Subject"),
            ticket_type=ticket_type,
            status=status,
            redmine_issue_id=issue_id,
            thread_id=email.get("thread_id"),
            rfc_message_id=email.get("rfc_message_id"),
            in_reply_to=email.get("in_reply_to"),
            references=email.get("references"),
            direct_sender=email.get("sender_email"),
            original_sender=email.get("original_sender"),
            original_subject=email.get("original_subject"),
            original_date=email.get("original_date"),
            content_fingerprint=fingerprint,
        )

    def _handle_skip(self, message_id, email, fingerprint):
        if self.confirmer.persist_skips:
            self._record_decision(
                message_id,
                email,
                fingerprint,
                "skipped_by_user",
            )
        return {
            "action": "skipped_by_user",
            "status": "skipped_by_user",
            "message_id": message_id,
            "thread_id": email.get("thread_id"),
            "subject": email.get("subject", "No Subject"),
            "redmine_issue_id": None,
        }

    def _process_new_ticket(
        self,
        message_id,
        email,
        fingerprint,
        classification=None,
        edited_subject=None,
        edited_description=None,
        downloaded_attachments=None,
    ):
        """
        Create a new Redmine ticket with two-phase state persistence.
        """
        if classification is None:
            classification = self.classifier.classify(email)

        subject = edited_subject
        if subject is None:
            subject = (
                classification.get("suggested_subject")
                if self.confirmer.use_suggested_draft
                else None
            ) or email.get("subject", "No Subject")
        description = edited_description
        if description is None and self.confirmer.use_suggested_draft:
            description = classification.get("suggested_description")
        if not description:
            description = (
                "This support ticket was automatically created from an incoming email.\n\n"
                "Please review the attached original email and validate the issue details before proceeding."
            )
        thread_id = email.get("thread_id")
        rfc_message_id = email.get("rfc_message_id")
        in_reply_to = email.get("in_reply_to")
        references = email.get("references")

        if self.dry_run:
            return {
                "action": "ticket_preview",
                "status": "preview",
                "message_id": message_id,
                "thread_id": thread_id,
                "subject": subject,
                "preview": self.redmine.build_issue_payload(
                    REDMINE_PROJECT_ID,
                    subject,
                    description,
                    message_id,
                ),
                "attachment_plans": [
                    self._attachment_plan_dict(plan)
                    for plan in self.attachment_pipeline.plan_attachments(
                        [
                            Path(attachment["path"])
                            for attachment in (downloaded_attachments or [])
                        ]
                    )
                ],
            }

        result = self.redmine.create_issue(
            project_id=REDMINE_PROJECT_ID,
            subject=subject,
            description=description,
            message_id=message_id,
        )

        issue = result.get("issue", {})
        issue_id = issue.get("id")
        if not issue_id:
            raise RuntimeError(f"Redmine did not return a valid issue ID: {result}")

        logger.info(f"Created Redmine issue #{issue_id} ('{issue.get('subject')}')")

        # Step B: IMMEDIATE write to storage (two-phase commit to prevent duplicate issues on crash)
        self.processed_store.add(
            message_id=message_id,
            subject=subject,
            ticket_type="Support",
            status="ticket_created",
            redmine_issue_id=issue_id,
            thread_id=thread_id,
            rfc_message_id=rfc_message_id,
            in_reply_to=in_reply_to,
            references=references,
            direct_sender=email.get("sender_email"),
            original_sender=email.get("original_sender"),
            original_subject=email.get("original_subject"),
            original_date=email.get("original_date"),
            content_fingerprint=fingerprint,
            issue_description=description,
        )

        # Step C: Complete post-creation tasks (PDF, attachments, final status)
        return self._complete_new_ticket_processing(
            message_id=message_id,
            email=email,
            issue_id=issue_id,
            issue_data=issue,
            downloaded_attachments=downloaded_attachments,
            description=description,
        )

    def _complete_new_ticket_processing(
        self,
        message_id,
        email,
        issue_id,
        issue_data=None,
        downloaded_attachments=None,
        description="",
    ):
        """
        Complete post-ticket-creation steps: attachment download, PDF generation, upload, and final status update.
        Safely records intermediate failure statuses if PDF or attachment upload fails.
        """
        subject = email.get("subject", "No Subject")
        thread_id = email.get("thread_id")
        record = self.processed_store.get(message_id) or {}
        if not description and issue_data:
            description = issue_data.get("description", "")
        if not description:
            description = record.get("issue_description", "")
        safe_id = re.sub(r"[^A-Za-z0-9_-]", "_", message_id)
        attachment_dir = EMAIL_ATTACHMENTS_DIR / "pending" / safe_id
        uploaded_names = set(record.get("uploaded_attachment_names", []))
        attachment_handling = list(record.get("attachment_handling", []))
        preserve_paths = set()

        try:
            if downloaded_attachments is None and not record.get("attachments_processed"):
                downloaded_attachments = self.gmail.download_attachments(
                    message_id,
                    attachment_dir,
                )
            if downloaded_attachments is None:
                downloaded_attachments = []
            attachment_names = [att["filename"] for att in downloaded_attachments]
            email["attachment_names"] = attachment_names

            # 1. Generate and upload PDF
            GENERATED_EMAILS_DIR.mkdir(parents=True, exist_ok=True)
            output_path = GENERATED_EMAILS_DIR / f"{issue_id}.pdf"

            if not record.get("pdf_attached"):
                try:
                    self.pdf_generator.generate(email, str(output_path))
                    logger.info(f"Generated email PDF: {output_path}")

                    token = self.redmine.upload_attachment(str(output_path))
                    pdf_attachment = {
                        "token": token,
                        "filename": output_path.name,
                        "content_type": "application/pdf",
                    }
                    try:
                        self.redmine.attach_file_to_issue(issue_id, pdf_attachment)
                    except (requests.Timeout, requests.ConnectionError) as upload_error:
                        if not self._issue_has_attachment(issue_id, output_path.name):
                            raise upload_error
                    logger.info(f"Attached PDF to Redmine issue #{issue_id}")
                    self.processed_store.update(
                        message_id,
                        pdf_attached=True,
                    )
                except Exception as pdf_exc:
                    logger.error(
                        "PDF generation/upload failed for issue #%s: %s",
                        issue_id,
                        pdf_exc,
                        exc_info=True,
                    )
                    self.processed_store.update(
                        message_id,
                        status="ticket_created_pdf_failed",
                    )
                    raise
                finally:
                    self._delete_file(output_path)

            if record.get("attachments_processed"):
                for line in attachment_handling:
                    match = (
                        re.search(r"local path: (.+)$", line)
                        if line.startswith("Skipped ")
                        else None
                    )
                    if match:
                        preserve_paths.add(Path(match.group(1)).resolve())
            else:
                pending_attachments = [
                    attachment for attachment in downloaded_attachments
                    if attachment["filename"] not in uploaded_names
                ]
                plans = self.attachment_pipeline.plan_attachments(
                    [Path(attachment["path"]) for attachment in pending_attachments]
                )
                results = self.attachment_pipeline.execute_attachments(
                    self.redmine,
                    plans,
                )
                skipped = []
                for attachment, result in zip(pending_attachments, results):
                    filename = attachment["filename"]
                    if result.action.value == "SKIP":
                        skipped.append(result)
                        preserve_paths.add(Path(attachment["path"]).resolve())
                        attachment_handling.append(
                            f"Skipped {result.name}: {result.note or 'Upload was not completed'}; "
                            f"local path: {attachment['path']}"
                        )
                        continue

                    if result.action.value == "COMPRESS":
                        attachment_handling.append(
                            f"Compressed {result.name}: "
                            f"{result.original_size / 1024 / 1024:.1f} MB -> "
                            f"{result.final_size / 1024 / 1024:.1f} MB; "
                            f"local path: {attachment['path']}"
                        )

                    if result.token:
                        try:
                            self.redmine.attach_file_to_issue(
                                issue_id,
                                {
                                    "token": result.token,
                                    "filename": filename,
                                    "content_type": attachment["content_type"],
                                },
                            )
                        except (requests.Timeout, requests.ConnectionError) as upload_error:
                            if not self._issue_has_attachment(issue_id, filename):
                                raise upload_error
                        except RedmineUploadError as exc:
                            skipped.append(result)
                            preserve_paths.add(Path(attachment["path"]).resolve())
                            attachment_handling.append(
                                f"Skipped {result.name}: HTTP {exc.status}; "
                                f"local path: {attachment['path']}"
                            )
                            continue
                        uploaded_names.add(filename)
                        self.processed_store.update(
                            message_id,
                            uploaded_attachment_names=sorted(uploaded_names),
                        )
                        logger.info(
                            "Attached '%s' to Redmine issue #%s",
                            filename,
                            issue_id,
                        )

                if skipped:
                    updated_description = (
                        f"[Attachment missing - see note]\n\n{description}"
                    )
                    marker = self.redmine._message_marker(message_id)
                    if marker not in updated_description:
                        updated_description = f"{updated_description.rstrip()}\n\n{marker}"
                    self.redmine.update_issue_description(
                        issue_id,
                        updated_description,
                    )

                if not attachment_handling:
                    attachment_handling.append("No files were compressed or skipped.")
                self.processed_store.update(
                    message_id,
                    attachments_processed=True,
                    attachment_handling=attachment_handling,
                )

            handling_note = "Attachment handling:\n" + "\n".join(attachment_handling)
            if not record.get("handling_note_written"):
                try:
                    self.redmine.add_issue_note(issue_id, handling_note)
                except Exception as note_error:
                    try:
                        note_was_saved = self._issue_has_note(issue_id, handling_note)
                    except Exception as verify_error:
                        logger.warning(
                            "Could not verify attachment handling note for issue #%s: %s",
                            issue_id,
                            verify_error,
                        )
                        note_was_saved = False
                    if not note_was_saved:
                        raise note_error
                self.processed_store.update(message_id, handling_note_written=True)

        finally:
            self._cleanup_directory(attachment_dir, preserve_paths)

        # Mark as completed in storage
        self.processed_store.update(message_id, status="completed")
        logger.info(f"Successfully completed processing for message {message_id} -> Redmine #{issue_id}")

        return {
            "action": "ticket_created",
            "status": "completed",
            "message_id": message_id,
            "thread_id": thread_id,
            "subject": subject,
            "redmine_issue_id": issue_id,
            "issue": issue_data,
            "attachment_handling": attachment_handling,
        }

    def _issue_has_attachment(self, issue_id, filename):
        issue = self.redmine.get_issue(issue_id, include="attachments")
        return any(
            attachment.get("filename") == filename
            for attachment in issue.get("attachments", [])
        )

    def _issue_has_note(self, issue_id, note):
        issue = self.redmine.get_issue(issue_id, include="journals")
        return any(
            journal.get("notes") == note
            for journal in issue.get("journals", [])
        )

    @staticmethod
    def _attachment_plan_dict(plan):
        return {
            "file": plan.name,
            "original_size": plan.original_size,
            "action": plan.action.value,
            "estimated_final_size": plan.final_size,
            "note": plan.note,
            "local_path": str(plan.local_path) if plan.local_path else None,
        }

    def _process_existing_thread(
        self,
        email,
        issue_id,
        fingerprint,
        downloaded_attachments=None,
    ):
        """
        Process a Gmail message as a reply/note to an existing Redmine ticket.
        """
        message_id = email["message_id"]
        subject = email.get("subject", "No Subject")
        thread_id = email.get("thread_id")
        rfc_message_id = email.get("rfc_message_id")
        in_reply_to = email.get("in_reply_to")
        references = email.get("references")

        logger.info(f"Adding reply to existing Redmine issue #{issue_id}")

        if self.dry_run:
            return {
                "action": "reply_preview",
                "status": "preview",
                "message_id": message_id,
                "thread_id": thread_id,
                "subject": subject,
                "redmine_issue_id": issue_id,
                "preview": {
                    "issue_id": issue_id,
                    "note": f"Reply received through Gmail:\n\n{email.get('body', '').strip()}",
                },
            }

        # Add reply body as note to Redmine issue
        reply_body = email.get("body", "").strip()
        if reply_body:
            note = f"Reply received through Gmail:\n\n{reply_body}"
            self.redmine.add_issue_note(issue_id, note)
            logger.info(f"Added reply note to Redmine issue #{issue_id}")

        # Attach any new email attachments
        self._attach_email_files(
            message_id=message_id,
            issue_id=issue_id,
            downloaded_attachments=downloaded_attachments,
        )

        # Save/update storage
        self.processed_store.add(
            message_id=message_id,
            subject=subject,
            ticket_type="Support Reply",
            status="reply_attached",
            redmine_issue_id=issue_id,
            thread_id=thread_id,
            rfc_message_id=rfc_message_id,
            in_reply_to=in_reply_to,
            references=references,
            direct_sender=email.get("sender_email"),
            original_sender=email.get("original_sender"),
            original_subject=email.get("original_subject"),
            original_date=email.get("original_date"),
            content_fingerprint=fingerprint,
        )
        self.processed_store.update(message_id, status="reply_attached")

        logger.info(f"Marked reply message {message_id} as attached for Redmine issue #{issue_id}")

        return {
            "action": "reply_attached",
            "status": "reply_attached",
            "message_id": message_id,
            "thread_id": thread_id,
            "subject": subject,
            "redmine_issue_id": issue_id,
        }

    def _attach_email_files(
        self,
        message_id,
        issue_id,
        downloaded_attachments=None,
    ):
        """
        Download Gmail attachments, upload to Redmine, attach to issue, and cleanup.
        """
        attachment_dir = EMAIL_ATTACHMENTS_DIR / str(issue_id) / str(message_id)
        try:
            attachments = downloaded_attachments
            if attachments is None:
                attachments = self.gmail.download_attachments(message_id, attachment_dir)
            if not attachments:
                return

            logger.info(f"Found {len(attachments)} attachment(s) for reply message {message_id}")
            for attachment in attachments:
                token = self.redmine.upload_attachment(attachment["path"])
                redmine_attachment = {
                    "token": token,
                    "filename": attachment["filename"],
                    "content_type": attachment["content_type"],
                }
                self.redmine.attach_file_to_issue(issue_id, redmine_attachment)
                logger.info(f"Attached '{attachment['filename']}' to Redmine issue #{issue_id}")
        finally:
            if downloaded_attachments:
                self._cleanup_directory(Path(downloaded_attachments[0]["path"]).parent)
            else:
                self._cleanup_directory(attachment_dir)

    @staticmethod
    def _delete_file(file_path):
        """
        Delete one temporary file safely.
        """
        try:
            path = Path(file_path)
            if path.exists():
                path.unlink()
        except OSError as exc:
            logger.warning(f"Could not delete temporary file {file_path}: {exc}")

    @staticmethod
    def _cleanup_directory(directory, preserve_paths=None):
        """
        Remove a temporary attachment directory cleanly.
        """
        directory = Path(directory)
        if not directory.exists():
            return

        preserved = {
            Path(path).resolve()
            for path in (preserve_paths or set())
        }
        try:
            for path in directory.rglob("*"):
                if path.is_file() and path.resolve() not in preserved:
                    try:
                        path.unlink()
                    except OSError:
                        pass

            for path in sorted(directory.rglob("*"), reverse=True):
                if path.is_dir():
                    try:
                        path.rmdir()
                    except OSError:
                        pass

            if not any(directory.iterdir()):
                directory.rmdir()
        except OSError as exc:
            logger.warning(f"Could not completely clean temporary directory {directory}: {exc}")