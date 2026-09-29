from backend.gmail.gmail_client import GmailClient
from backend.gmail.email_parser import EmailParser
from backend.ticketing.ticket_extractor import TicketExtractor


def main():
    gmail = GmailClient()
    parser = EmailParser()
    extractor = TicketExtractor()

    messages = gmail.get_messages(max_results=5)

    for message in messages:
        raw_email = gmail.get_message(message["id"])

        email = parser.parse(raw_email)

        extracted = extractor.extract(email)

        print("\n" + "=" * 60)

        print(f"Subject         : {extracted['subject']}")
        print(f"Center          : {extracted['center']}")
        print(f"Module          : {extracted['module']}")
        print(f"Description     : {extracted['description']}")
        print(f"Expected Result : {extracted['expected_result']}")
        print(f"Actual Result   : {extracted['actual_result']}")


if __name__ == "__main__":
    main()