from pathlib import Path

from backend.redmine.redmine_client import RedmineClient


def main():
    redmine = RedmineClient()

    pdf_path = Path(
        "generated_emails/"
        "Issue - Incident Reporting - "
        "Incorrect Pathology Event Type.pdf"
    )

    token = redmine.upload_attachment(
        str(pdf_path)
    )

    print("Upload successful.")
    print(f"Upload token: {token}")


if __name__ == "__main__":
    main()