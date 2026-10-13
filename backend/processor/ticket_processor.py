from pathlib import Path

from backend.gmail.gmail_client import GmailClient
from backend.gmail.email_parser import EmailParser
from backend.gmail.email_pdf_generator import EmailPdfGenerator
from backend.redmine.redmine_client import RedmineClient
from backend.ticketing.ticket_classifier import TicketClassifier
from backend.storage.processed_messages import ProcessedMessageStore
from backend.storage.message_fingerprint import create_content_fingerprint
from backend.config.settings import (
    REDMINE_PROJECT_ID,
    GENERATED_EMAILS_DIR,
    EMAIL_ATTACHMENTS_DIR,
)
from backend.utils.logger import get_logger
from backend.utils.prompt import ask_yes_no

logger = get_logger("processor")


class TicketProcessor:
    """
    Orchestrates the complete Gmail -> Redmine ticket workflow.

    Implements intelligent decision logic:
        - Asks for user confirmation on all new ticket creations for safety.
        - Asks for user confirmation on replies, duplicate fingerprints, and same-thread new issues.
        - Two-phase persistence ensures Redmine ticket IDs are never lost even if PDF/attachments fail.
    """

    def __init__(self):
        self.gmail = GmailClient()
        self.parser = EmailParser()
        self.classifier = TicketClassifier()
        self.pdf_generator = EmailPdfGenerator()
        self.redmine = RedmineClient()
        self.processed_store = ProcessedMessageStore()

    def process_message(self, message_id, email=None, interactive=True):
        """
        Process one Gmail message following the intelligent confirmation flow.
        """
        # 1. Retrieve and parse email if not provided
        if email is None:
            raw_email = self.gmail.get_message(message_id)
            email = self.parser.parse(raw_email)

        subject = email.get("subject", "No Subject")
        thread_id = email.get("thread_id")
        rfc_message_id = email.get("rfc_message_id")
        in_reply_to = email.get("in_reply_to")
        references = email.get("references")

        logger.info(f"Processing message {message_id} | Subject: '{subject}'")

        # 2. Check whether this message was already processed or partially processed
        existing_record = self.processed_store.get(message_id)
        if existing_record:
            status = existing_record.get("status")
            issue_id = existing_record.get("redmine_issue_id")

            if status in ("completed", "reply_attached", "ignored", "sender_not_allowed", "duplicate", "skipped_by_user", "not_a_ticket"):
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

        # 3. Compute content fingerprint & check for duplicate content
        fingerprint = create_content_fingerprint(email)
        duplicate_record = self.processed_store.get_by_fingerprint(fingerprint)

        if duplicate_record and duplicate_record.get("redmine_issue_id"):
            dup_issue_id = duplicate_record.get("redmine_issue_id")
            logger.info(f"Duplicate content detected -> matches Redmine #{dup_issue_id}")

            print()
            print("Possible duplicate detected.")
            print(f"Message ID: {message_id}")
            print(f"Subject: {subject}")
            print(f"Existing Redmine Ticket: #{dup_issue_id}")
            print("This email appears to be a duplicate.")
            print()

            if interactive:
                create_anyway = ask_yes_no("Create another ticket anyway?")
                if not create_anyway:
                    self.processed_store.add(
                        message_id=message_id,
                        subject=subject,
                        ticket_type=None,
                        status="duplicate",
                        redmine_issue_id=dup_issue_id,
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
                    return {
                        "action": "duplicate",
                        "status": "duplicate",
                        "message_id": message_id,
                        "thread_id": thread_id,
                        "subject": subject,
                        "redmine_issue_id": dup_issue_id,
                    }
                # If user said Yes, proceed to create new ticket!
                logger.info(f"User confirmed creating duplicate as new ticket for message {message_id}")

        # 4. Check for Reply / Thread Relationship
        # Lookup In-Reply-To / References first (strongest evidence of a reply)
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

        # 4A. Strong In-Reply-To match
        if reply_match and target_issue_id:
            if has_fresh_structured_fields:
                print()
                print("This email is in an existing Gmail conversation, but it appears to contain new ticket information.")
                print(f"Message ID: {message_id}")
                print(f"Subject: {subject}")
                print(f"In-Reply-To: {in_reply_to}")
                print(f"Existing Redmine Ticket: #{target_issue_id}")
                print()

                if interactive:
                    create_new = ask_yes_no("Create a NEW Redmine ticket instead?")
                    if create_new:
                        return self._process_new_ticket(message_id, email, fingerprint)

                    attach_old = ask_yes_no(f"Do you want to attach this email to Redmine ticket #{target_issue_id}?")
                    if attach_old:
                        return self._process_existing_thread(email, target_issue_id, fingerprint)

                    self._record_skipped(message_id, email, fingerprint)
                    return {
                        "action": "skipped_by_user",
                        "status": "skipped_by_user",
                        "message_id": message_id,
                        "thread_id": thread_id,
                        "subject": subject,
                        "redmine_issue_id": None,
                    }
            else:
                print()
                print("Possible reply detected.")
                print(f"Message ID: {message_id}")
                print(f"Subject: {subject}")
                print(f"Gmail Thread: {thread_id}")
                print(f"In-Reply-To: {in_reply_to}")
                print(f"Existing Redmine Ticket: #{target_issue_id}")
                print()

                if interactive:
                    attach = ask_yes_no(f"Do you want to attach this email to Redmine ticket #{target_issue_id}?")
                    if attach:
                        return self._process_existing_thread(email, target_issue_id, fingerprint)

                    create_new = ask_yes_no("Do you want to create this as a NEW Redmine ticket instead?")
                    if create_new:
                        return self._process_new_ticket(message_id, email, fingerprint)

                    self._record_skipped(message_id, email, fingerprint)
                    return {
                        "action": "skipped_by_user",
                        "status": "skipped_by_user",
                        "message_id": message_id,
                        "thread_id": thread_id,
                        "subject": subject,
                        "redmine_issue_id": None,
                    }

        # 4B. Thread match only (no In-Reply-To header match)
        elif thread_match and target_issue_id:
            if has_fresh_structured_fields:
                print()
                print("This email is in an existing Gmail thread, but it appears to contain new ticket information.")
                print(f"Message ID: {message_id}")
                print(f"Subject: {subject}")
                print(f"Existing Redmine Ticket: #{target_issue_id}")
                print()

                if interactive:
                    create_new = ask_yes_no("Create a NEW Redmine ticket instead?")
                    if create_new:
                        return self._process_new_ticket(message_id, email, fingerprint)

                    attach_old = ask_yes_no(f"Do you want to attach this email to Redmine ticket #{target_issue_id}?")
                    if attach_old:
                        return self._process_existing_thread(email, target_issue_id, fingerprint)

                    self._record_skipped(message_id, email, fingerprint)
                    return {
                        "action": "skipped_by_user",
                        "status": "skipped_by_user",
                        "message_id": message_id,
                        "thread_id": thread_id,
                        "subject": subject,
                        "redmine_issue_id": None,
                    }
            else:
                print()
                print(f"This email shares a Gmail thread with existing Redmine ticket #{target_issue_id}, but has no direct reply header.")
                print(f"Message ID: {message_id}")
                print(f"Subject: {subject}")
                print(f"Gmail Thread: {thread_id}")
                print(f"Existing Redmine Ticket: #{target_issue_id}")
                print()

                if interactive:
                    attach = ask_yes_no(f"Do you want to attach this email to Redmine ticket #{target_issue_id}?")
                    if attach:
                        return self._process_existing_thread(email, target_issue_id, fingerprint)

                    create_new = ask_yes_no("Do you want to create this as a NEW Redmine ticket instead?")
                    if create_new:
                        return self._process_new_ticket(message_id, email, fingerprint)

                    self._record_skipped(message_id, email, fingerprint)
                    return {
                        "action": "skipped_by_user",
                        "status": "skipped_by_user",
                        "message_id": message_id,
                        "thread_id": thread_id,
                        "subject": subject,
                        "redmine_issue_id": None,
                    }

        # 5. Non-reply / Non-ambiguous email: Classify email
        classification = self.classifier.classify(email)
        logger.info(
            f"Classification: is_ticket={classification['is_ticket']}, "
            f"type={classification['ticket_type']}"
        )

        # Log AI reasoning if available
        if classification.get("ai_classification") and classification.get("reasoning"):
            logger.info(f"AI Reasoning: {classification['reasoning']}")

        if not classification["is_ticket"]:
            logger.info(f"Message {message_id} is not a support ticket. Recording and skipping.")
            self.processed_store.add(
                message_id=message_id,
                subject=subject,
                ticket_type=None,
                status="not_a_ticket",
                redmine_issue_id=None,
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
            return {
                "action": "not_a_ticket",
                "status": "not_a_ticket",
                "message_id": message_id,
                "thread_id": thread_id,
                "subject": subject,
                "redmine_issue_id": None,
            }

        # 6. CLEAR NEW TICKET -> ASK USER CONFIRMATION
        print()
        print("New ticket detected.")
        print(f"Subject: {subject}")
        print(f"Sender: {email.get('sender_email')}")
        print()

        if interactive:
            create_ticket = ask_yes_no("Create Redmine ticket for this email?")
            if not create_ticket:
                self._record_skipped(message_id, email, fingerprint)
                return {
                    "action": "skipped_by_user",
                    "status": "skipped_by_user",
                    "message_id": message_id,
                    "thread_id": thread_id,
                    "subject": subject,
                    "redmine_issue_id": None,
                }

        print("Creating Redmine ticket...")
        logger.info(f"User confirmed creating ticket for message {message_id}")

        return self._process_new_ticket(
            message_id=message_id,
            email=email,
            fingerprint=fingerprint,
            classification=classification,
        )

    def _process_new_ticket(self, message_id, email, fingerprint, classification=None):
        """
        Create a new Redmine ticket with two-phase state persistence.
        """
        if classification is None:
            classification = self.classifier.classify(email)

        subject = email.get("subject", "No Subject")
        thread_id = email.get("thread_id")
        rfc_message_id = email.get("rfc_message_id")
        in_reply_to = email.get("in_reply_to")
        references = email.get("references")

        # Step A: Create new Redmine ticket
        result = self.redmine.create_issue(
            project_id=REDMINE_PROJECT_ID,
            subject=subject,
            description=(
                "This support ticket was automatically created from an incoming email.\n\n"
                "Please review the attached original email and validate the issue details before proceeding."
            ),
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
        )

        # Step C: Complete post-creation tasks (PDF, attachments, final status)
        return self._complete_new_ticket_processing(
            message_id=message_id,
            email=email,
            issue_id=issue_id,
            issue_data=issue,
        )

    def _complete_new_ticket_processing(self, message_id, email, issue_id, issue_data=None):
        """
        Complete post-ticket-creation steps: attachment download, PDF generation, upload, and final status update.
        Safely records intermediate failure statuses if PDF or attachment upload fails.
        """
        subject = email.get("subject", "No Subject")
        thread_id = email.get("thread_id")
        attachment_dir = EMAIL_ATTACHMENTS_DIR / str(issue_id) / str(message_id)

        try:
            downloaded_attachments = self.gmail.download_attachments(
                message_id,
                attachment_dir,
            )
            attachment_names = [att["filename"] for att in downloaded_attachments]
            email["attachment_names"] = attachment_names

            # 1. Generate and upload PDF
            GENERATED_EMAILS_DIR.mkdir(parents=True, exist_ok=True)
            output_path = GENERATED_EMAILS_DIR / f"{issue_id}.pdf"

            try:
                self.pdf_generator.generate(email, str(output_path))
                logger.info(f"Generated email PDF: {output_path}")

                token = self.redmine.upload_attachment(str(output_path))
                pdf_attachment = {
                    "token": token,
                    "filename": output_path.name,
                    "content_type": "application/pdf",
                }
                self.redmine.attach_file_to_issue(issue_id, pdf_attachment)
                logger.info(f"Attached PDF to Redmine issue #{issue_id}")
            except Exception as pdf_exc:
                logger.error(f"PDF generation/upload failed for issue #{issue_id}: {pdf_exc}", exc_info=True)
                self.processed_store.update(message_id, status="ticket_created_pdf_failed")
                raise
            finally:
                self._delete_file(output_path)

            # 2. Upload downloaded email attachments to Redmine
            if downloaded_attachments:
                try:
                    logger.info(f"Uploading {len(downloaded_attachments)} original attachment(s) to Redmine #{issue_id}")
                    for attachment in downloaded_attachments:
                        att_token = self.redmine.upload_attachment(attachment["path"])
                        redmine_attachment = {
                            "token": att_token,
                            "filename": attachment["filename"],
                            "content_type": attachment["content_type"],
                        }
                        self.redmine.attach_file_to_issue(issue_id, redmine_attachment)
                        logger.info(f"Attached '{attachment['filename']}' to Redmine issue #{issue_id}")
                except Exception as att_exc:
                    logger.error(f"Email attachment upload failed for issue #{issue_id}: {att_exc}", exc_info=True)
                    self.processed_store.update(message_id, status="ticket_created_attachment_failed")
                    raise

        finally:
            self._cleanup_directory(attachment_dir)

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
        }

    def _process_existing_thread(self, email, issue_id, fingerprint):
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

    def _record_skipped(self, message_id, email, fingerprint):
        """
        Record that an email was skipped by user choice.
        """
        self.processed_store.add(
            message_id=message_id,
            subject=email.get("subject", "No Subject"),
            ticket_type=None,
            status="skipped_by_user",
            redmine_issue_id=None,
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
        logger.info(f"Recorded message {message_id} as skipped_by_user in storage.")

    def _attach_email_files(self, message_id, issue_id):
        """
        Download Gmail attachments, upload to Redmine, attach to issue, and cleanup.
        """
        attachment_dir = EMAIL_ATTACHMENTS_DIR / str(issue_id) / str(message_id)
        try:
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
    def _cleanup_directory(directory):
        """
        Remove a temporary attachment directory cleanly.
        """
        directory = Path(directory)
        if not directory.exists():
            return

        try:
            for path in directory.rglob("*"):
                if path.is_file():
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

            try:
                directory.rmdir()
            except OSError:
                pass
        except OSError as exc:
            logger.warning(f"Could not completely clean temporary directory {directory}: {exc}")