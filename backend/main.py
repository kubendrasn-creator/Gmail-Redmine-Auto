import argparse
import sys

from backend.config.settings import (
    GMAIL_ALLOWED_SENDERS,
    GMAIL_LOOKBACK_HOURS,
    GMAIL_MAX_RESULTS,
    WEB_PORT,
    validate_settings,
)
from backend.gmail.gmail_client import GmailClient
from backend.utils.preflight import PreflightValidator
from backend.processor.ticket_processor import TicketProcessor
from backend.storage.processed_messages import ProcessedMessageStore
from backend.utils.logger import get_logger

logger = get_logger("main")


def main():
    parser = argparse.ArgumentParser(
        description="Gmail -> Redmine Support Automation"
    )
    parser.add_argument(
        "--message-id",
        type=str,
        default=None,
        help="Process a specific Gmail message ID directly",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run in dry-run mode (no writes to Redmine or database)",
    )
    parser.add_argument(
        "--skip-preflight",
        action="store_true",
        help="Skip preflight validation checks",
    )
    parser.add_argument(
        "--lookback-hours",
        type=int,
        default=None,
        help="Override Gmail lookback hours from config",
    )
    parser.add_argument(
        "--max-results",
        type=int,
        default=None,
        help="Override Gmail max results from config",
    )
    parser.add_argument(
        "--no-ui",
        action="store_true",
        help="Use the existing terminal workflow instead of the local review UI",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=WEB_PORT,
        help="Local review UI port (default: WEB_PORT or 8000)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open a browser automatically",
    )
    parser.add_argument(
        "--auto-run",
        action="store_true",
        help="Fetch emails immediately after the local UI starts",
    )
    args = parser.parse_args()

    # Override config if flags provided
    lookback_hours = args.lookback_hours if args.lookback_hours is not None else GMAIL_LOOKBACK_HOURS
    max_results = args.max_results if args.max_results is not None else GMAIL_MAX_RESULTS

    # Run preflight unless skipped
    if not args.skip_preflight:
        validator = PreflightValidator()
        checks = validator.run_all()
        validator.print_report()
        has_failures = any(c.status.name == "FAIL" for c in checks)
        if has_failures:
            print("Preflight validation failed. Aborting.")
            sys.exit(1)

    try:
        validate_settings()
    except Exception as exc:
        logger.error(f"Configuration validation failed: {exc}")
        print(f"Configuration Error: {exc}")
        sys.exit(1)

    if not args.no_ui:
        from backend.web.server import run_server

        run_server(
            port=args.port,
            no_browser=args.no_browser,
            auto_run=args.auto_run,
            default_dry_run=args.dry_run,
            preflight=checks if not args.skip_preflight else [],
            message_id=args.message_id,
            lookback_hours=lookback_hours,
            max_results=max_results,
        )
        return

    gmail = GmailClient()
    processor = TicketProcessor(dry_run=args.dry_run)
    store = ProcessedMessageStore()

    if args.message_id:
        logger.info(f"Running single-message mode for Gmail message ID: {args.message_id}")
        messages = [{"id": args.message_id.strip()}]
    else:
        logger.info("Starting Gmail -> Redmine automation run...")
        messages = gmail.get_messages(
            lookback_hours=lookback_hours,
            max_results=max_results,
        )

    if not messages:
        logger.info("No emails found in the configured lookback window.")
        print("No emails found.")
        return

    logger.info(f"Retrieved {len(messages)} message(s) to inspect.")

    stats = {
        "total": len(messages),
        "tickets_created": 0,
        "replies_attached": 0,
        "sender_not_allowed": 0,
        "duplicates": 0,
        "not_a_ticket": 0,
        "skipped_by_user": 0,
        "already_processed": 0,
        "failed": 0,
        "ai_fallback": 0,
        "attachments_compressed": 0,
        "attachments_skipped": 0,
    }

    for message in messages:
        message_id = message["id"]
        logger.info("-" * 50)
        logger.info(f"Inspecting message ID: {message_id}")

        # --------------------------------------------------
        # 1. Exact Gmail message duplicate check
        # --------------------------------------------------
        existing_record = store.get(message_id)
        if existing_record:
            status = existing_record.get("status")
            issue_id = existing_record.get("redmine_issue_id")

            if status in ("completed", "reply_attached", "ignored", "sender_not_allowed", "duplicate", "skipped_by_user", "not_a_ticket"):
                logger.info(
                    f"Message {message_id} already processed with status '{status}' "
                    f"(Redmine Issue: #{issue_id if issue_id else 'N/A'}). Skipping."
                )
                stats["already_processed"] += 1
                continue
            elif status in ("ticket_created", "ticket_created_pdf_failed", "ticket_created_attachment_failed"):
                logger.info(
                    f"Message {message_id} has intermediate status '{status}'. Handing off to processor to resume."
                )

        # --------------------------------------------------
        # 2. Retrieve and parse email
        # --------------------------------------------------
        try:
            raw_email = gmail.get_message(message_id)
            email = processor.parser.parse(raw_email)
        except Exception as exc:
            logger.error(f"Failed to fetch/parse message {message_id}: {exc}", exc_info=True)
            stats["failed"] += 1
            continue

        sender_email = (email.get("sender_email") or "").strip().lower()
        subject = email.get("subject", "No Subject")
        thread_id = email.get("thread_id")
        rfc_message_id = email.get("rfc_message_id")
        in_reply_to = email.get("in_reply_to")
        references = email.get("references")

        logger.info(f"Sender: {sender_email} | Subject: '{subject}'")

        # --------------------------------------------------
        # 3. Allowed sender check
        # --------------------------------------------------
        if sender_email not in GMAIL_ALLOWED_SENDERS:
            logger.warning(
                f"Sender '{sender_email}' is not in approved senders list. Recording and skipping."
            )
            if not args.dry_run:
                store.add(
                    message_id=message_id,
                    thread_id=thread_id,
                    rfc_message_id=rfc_message_id,
                    in_reply_to=in_reply_to,
                    references=references,
                    direct_sender=sender_email,
                    original_sender=email.get("original_sender"),
                    original_subject=email.get("original_subject"),
                    original_date=email.get("original_date"),
                    subject=subject,
                    ticket_type=None,
                    status="sender_not_allowed",
                )
            stats["sender_not_allowed"] += 1
            continue

        # --------------------------------------------------
        # 4. Process ticket workflow (with intelligent confirmation)
        # --------------------------------------------------
        try:
            result = processor.process_message(
                message_id,
                email=email,
                interactive=not args.dry_run,
            )
        except Exception as exc:
            logger.error(f"Error processing message {message_id}: {exc}", exc_info=True)
            stats["failed"] += 1
            continue

        if not result:
            logger.warning(f"No result returned for message {message_id}.")
            stats["failed"] += 1
            continue

        action = result.get("action")
        if action == "ticket_created":
            logger.info(f"Successfully created Redmine ticket #{result.get('redmine_issue_id')}")
            stats["tickets_created"] += 1
        elif action == "reply_attached":
            logger.info(f"Successfully attached reply to Redmine ticket #{result.get('redmine_issue_id')}")
            stats["replies_attached"] += 1
        elif action == "not_a_ticket":
            logger.info(f"Message {message_id} was classified as non-ticket.")
            stats["not_a_ticket"] += 1
        elif action == "duplicate":
            logger.info(f"Message {message_id} was identified as duplicate.")
            stats["duplicates"] += 1
        elif action == "skipped_by_user":
            logger.info(f"Message {message_id} was skipped by user confirmation.")
            stats["skipped_by_user"] += 1
        elif action == "already_processed":
            stats["already_processed"] += 1
        else:
            logger.info(f"Message {message_id} processed with action: {action}")

    # --------------------------------------------------
    # Summary
    # --------------------------------------------------
    logger.info("=" * 50)
    logger.info("RUN SUMMARY:")
    logger.info(f"  Total messages:         {stats['total']}")
    logger.info(f"  Tickets created:        {stats['tickets_created']}")
    logger.info(f"  Replies attached:       {stats['replies_attached']}")
    logger.info(f"  Sender not allowed:     {stats['sender_not_allowed']}")
    logger.info(f"  Duplicates recorded:    {stats['duplicates']}")
    logger.info(f"  Non-tickets skipped:    {stats['not_a_ticket']}")
    logger.info(f"  Skipped by user:        {stats['skipped_by_user']}")
    logger.info(f"  Already processed:      {stats['already_processed']}")
    logger.info(f"  Failed:                 {stats['failed']}")
    logger.info(f"  AI fallback:            {stats['ai_fallback']}")
    logger.info(f"  Attachments compressed: {stats['attachments_compressed']}")
    logger.info(f"  Attachments skipped:    {stats['attachments_skipped']}")
    logger.info("=" * 50)


if __name__ == "__main__":
    main()