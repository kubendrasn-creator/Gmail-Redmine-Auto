from backend.gmail.gmail_client import GmailClient
from backend.gmail.email_parser import EmailParser
from backend.ticketing.ticket_classifier import TicketClassifier


def main():
    gmail = GmailClient()
    parser = EmailParser()
    classifier = TicketClassifier()

    messages = gmail.get_messages(max_results=5)

    for message in messages:
        raw_email = gmail.get_message(message["id"])

        email = parser.parse(raw_email)

        result = classifier.classify(email)

        print("\n" + "=" * 60)

        print(f"Subject: {email['subject']}")

        print(f"Is Ticket: {result['is_ticket']}")

        print(f"Ticket Type: {result['ticket_type']}")

        print(
            f"Matched Keywords: "
            f"{result['matched_keywords']}"
        )


if __name__ == "__main__":
    main()