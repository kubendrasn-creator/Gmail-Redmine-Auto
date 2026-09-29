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

        if store.contains(message_id):
            print(f"Already processed: {message_id}")
            continue

        print(f"Processing message: {message_id}")

        result = processor.process_message(message_id)

        # Only mark it processed after successful processing.
        if result is not None:
            store.add(message_id)
            print(f"Marked as processed: {message_id}")


if __name__ == "__main__":
    main()