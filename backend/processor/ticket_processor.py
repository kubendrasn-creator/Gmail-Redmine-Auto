from pathlib import Path

from backend.gmail.gmail_client import GmailClient
from backend.gmail.email_parser import EmailParser
from backend.gmail.email_pdf_generator import EmailPdfGenerator
from backend.redmine.redmine_client import RedmineClient
from backend.ticketing.ticket_classifier import TicketClassifier

from backend.config.settings import (
    REDMINE_PROJECT_ID,
)


class TicketProcessor:
    """
    Orchestrates the complete Gmail -> Redmine ticket workflow.
    """

    def __init__(self):
        self.gmail = GmailClient()
        self.parser = EmailParser()
        self.classifier = TicketClassifier()
        self.pdf_generator = EmailPdfGenerator()
        self.redmine = RedmineClient()

    def process_message(self, message_id):
        """
        Process one Gmail message.
        """

        # 1. Retrieve email
        raw_email = self.gmail.get_message(message_id)

        # 2. Parse email
        email = self.parser.parse(raw_email)

        print("=" * 60)
        print(f"Subject: {email['subject']}")

        # 3. Classify email
        classification = self.classifier.classify(email)

        print(f"Is Ticket: {classification['is_ticket']}")
        print(f"Ticket Type: {classification['ticket_type']}")

        # 4. Ignore non-ticket emails
        if not classification["is_ticket"]:
            print("Not a support ticket. Skipping.")
            return None

        print("Support ticket detected.")

        # 5. Generate PDF
        output_dir = Path("generated_emails")
        output_dir.mkdir(exist_ok=True)

        output_path = output_dir / "temporary_support_email.pdf"

        self.pdf_generator.generate(
            email,
            str(output_path),
        )

        print(f"PDF created: {output_path}")

        # 6. Upload PDF to Redmine
        token = self.redmine.upload_attachment(
            str(output_path)
        )

        print("PDF uploaded to Redmine.")

        # 7. Prepare attachment
        attachment = {
            "token": token,
            "filename": output_path.name,
            "content_type": "application/pdf",
        }

        # 8. Create Redmine Support ticket
        result = self.redmine.create_issue(
            project_id=REDMINE_PROJECT_ID,
            subject=email["subject"],
            description=(
                "This support ticket was automatically created "
                "from an incoming email.\n\n"
                "Please review the attached original email and "
                "validate the issue details before proceeding."
            ),
            attachment=attachment,
        )

        issue = result["issue"]

        print("Redmine ticket created.")
        print(f"Issue ID: {issue['id']}")
        print(f"Subject: {issue['subject']}")
        print(f"Tracker: {issue['tracker']['name']}")

        return issue