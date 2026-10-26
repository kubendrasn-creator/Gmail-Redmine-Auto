from backend.gmail.gmail_client import GmailClient
from backend.gmail.email_parser import EmailParser
from backend.gmail.email_pdf_generator import EmailPdfGenerator
from backend.ticketing.ticket_classifier import TicketClassifier
import re

from backend.config.settings import GENERATED_EMAILS_DIR

def create_safe_filename(subject):
    filename = re.sub(r'[<>:"/\\|?*]', "_", subject)
    filename = filename.strip()

    return f"{filename}.pdf"

def main():
    gmail = GmailClient()
    parser = EmailParser()
    classifier = TicketClassifier()
    pdf_generator = EmailPdfGenerator()

    messages = gmail.get_messages(max_results=10)

    if not messages:
        print("No emails found.")
        return

    for message in messages:
        raw_email = gmail.get_message(message["id"])

        email = parser.parse(raw_email)

        classification = classifier.classify(email)

        print("\n" + "=" * 60)
        print(f"Subject: {email['subject']}")
        print(f"Is Ticket: {classification['is_ticket']}")
        print(f"Ticket Type: {classification['ticket_type']}")

        # Ignore non-ticket emails
        if not classification["is_ticket"]:
            print("Skipping email - not a support ticket.")
            continue

        print("Support ticket detected.")

        GENERATED_EMAILS_DIR.mkdir(parents=True, exist_ok=True)

        filename = create_safe_filename(
            email["subject"]
        )

        output_path = str(GENERATED_EMAILS_DIR / filename)

        pdf_generator.generate(
            email,
            output_path,
        )

        print(f"PDF created: {output_path}")


if __name__ == "__main__":
    main()