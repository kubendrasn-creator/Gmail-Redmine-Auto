from backend.config.settings import (
    REDMINE_SUPPORT_TRACKER_ID,
    validate_settings,
)
from backend.redmine.redmine_client import RedmineClient


def main():
    validate_settings()

    client = RedmineClient()

    issue = client.create_issue(
        project_id=1,
        tracker_id=int(REDMINE_SUPPORT_TRACKER_ID),
        subject="Test ticket created from Python",
        description="This is a test issue created using the Redmine REST API.",
    )

    print("Issue created successfully.")
    print(f"Issue ID: {issue['issue']['id']}")
    print(f"Subject: {issue['issue']['subject']}")


if __name__ == "__main__":
    main()