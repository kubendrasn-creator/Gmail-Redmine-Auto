from backend.gmail.gmail_client import GmailClient
from backend.gmail.email_parser import EmailParser
from backend.processor.ticket_processor import TicketProcessor


def main():
    gmail = GmailClient()
    parser = EmailParser()
    processor = TicketProcessor()

    messages = gmail.get_messages(max_results=10)

    if not messages:
        print("No emails found.")
        return

    for message in messages:
        raw_email = gmail.get_message(message["id"])

        # Parse the email so we can check its subject.
        email = parser.parse(raw_email)

        subject = email["subject"]

        if subject == "Issue - Incident Reporting - Incorrect Pathology Event Type":
            print("Found test email.")
            processor.process_message(message["id"])
            return

    print("Test email not found.")


if __name__ == "__main__":
    main()