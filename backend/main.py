from backend.gmail.gmail_client import GmailClient
from backend.processor.ticket_processor import TicketProcessor
from backend.storage.processed_messages import ProcessedMessageStore


def main():
    gmail = GmailClient()
    processor = TicketProcessor()
    store = ProcessedMessageStore()

    messages = gmail.get_messages()

    if not messages:
        print("No emails found.")
        return

    for message in messages:
        message_id = message["id"]

        existing_record = store.get(message_id)

        if existing_record:
            print("Already processed:")
            print(f"  Message ID: {existing_record['message_id']}")
            print(f"  Subject: {existing_record['subject']}")
            print(f"  Status: {existing_record['status']}")

            if existing_record["redmine_issue_id"]:
                print(
                    f"  Redmine Issue: "
                    f"#{existing_record['redmine_issue_id']}"
                )

            continue

        print(f"Processing message: {message_id}")

        result = processor.process_message(message_id)

        if result is None:
            # Non-ticket emails are also recorded so we don't
            # repeatedly classify the same email every morning.
            raw_email = gmail.get_message(message_id)
            email = processor.parser.parse(raw_email)

            store.add(
                message_id=message_id,
                subject=email["subject"],
                ticket_type=None,
                status="not_a_ticket",
            )

            print("Marked as processed: not a ticket")
            continue

        # Ticket was successfully created.
        store.add(
            message_id=message_id,
            subject=result["subject"],
            ticket_type="Support",
            status="ticket_created",
            redmine_issue_id=result["id"],
        )

        print(
            f"Marked as processed: "
            f"Redmine issue #{result['id']}"
        )


if __name__ == "__main__":
    main()