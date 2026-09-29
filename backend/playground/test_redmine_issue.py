from pathlib import Path

from backend.redmine.redmine_client import RedmineClient


def main():
    redmine = RedmineClient()

    pdf_path = Path(
        "generated_emails/"
        "Issue - Incident Reporting - "
        "Incorrect Pathology Event Type.pdf"
    )

    # 1. Upload PDF
    token = redmine.upload_attachment(
        str(pdf_path)
    )

    print("PDF uploaded.")
    print(f"Token: {token}")

    # 2. Create Support ticket
    attachment = {
        "token": token,
        "filename": pdf_path.name,
        "content_type": "application/pdf",
    }

    result = redmine.create_issue(
        project_id=1,
        tracker_id=3,
        subject="Issue - Incident Reporting - Incorrect Pathology Event Type",
        description=(
            "This support ticket was automatically created from an incoming email.\n\n"
            "Please review the attached original email and validate the issue "
            "details before proceeding."
        ),
        attachment=attachment,
    )

    issue = result["issue"]

    print("\n" + "=" * 60)
    print("Redmine issue created successfully.")
    print(f"Issue ID: {issue['id']}")
    print(f"Subject: {issue['subject']}")
    print(f"Tracker: {issue['tracker']['name']}")
    print(f"Project: {issue['project']['name']}")
    print("=" * 60)


if __name__ == "__main__":
    main()