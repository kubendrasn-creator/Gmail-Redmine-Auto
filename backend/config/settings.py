from pathlib import Path
import os

from dotenv import load_dotenv


# Project root
BASE_DIR = Path(__file__).resolve().parents[2]

# Environment configuration
ENV_DIR = BASE_DIR / "env"
ENV_FILE = ENV_DIR / ".env"

load_dotenv(ENV_FILE)


# Redmine configuration
REDMINE_URL = os.getenv("REDMINE_URL")
REDMINE_API_KEY = os.getenv("REDMINE_API_KEY")
REDMINE_PROJECT_ID = int(os.getenv("REDMINE_PROJECT_ID") or 0)
REDMINE_SUPPORT_TRACKER_ID = int(os.getenv("REDMINE_SUPPORT_TRACKER_ID") or 0)
REDMINE_ASSIGNEE_ID = int(os.getenv("REDMINE_ASSIGNEE_ID") or 0)

REDMINE_CATEGORY_CUSTOM_FIELD_ID = int(os.getenv("REDMINE_CATEGORY_CUSTOM_FIELD_ID") or 0)
REDMINE_CATEGORY_VALUE = os.getenv("REDMINE_CATEGORY_VALUE")

REDMINE_ISSUES_CATEGORY = int(os.getenv("REDMINE_ISSUES_CATEGORY") or 0)
REDMINE_ISSUES_CATEGORY_VALUE = os.getenv("REDMINE_ISSUES_CATEGORY_VALUE")

REDMINE_SEVERITY_CUSTOM_FIELD_ID = int(os.getenv("REDMINE_SEVERITY_CUSTOM_FIELD_ID") or 0)
REDMINE_SEVERITY_VALUE = os.getenv("REDMINE_SEVERITY_VALUE")

REDMINE_ESTIMATED_HOURS = int(os.getenv("REDMINE_ESTIMATED_HOURS") or 0)
REDMINE_START_DATE = os.getenv("REDMINE_START_DATE")
REDMINE_END_DATE = os.getenv("REDMINE_END_DATE")

# Gmail configuration
GMAIL_MAX_RESULTS = int(os.getenv("GMAIL_MAX_RESULTS") or 10)
GMAIL_LOOKBACK_HOURS = int(os.getenv("GMAIL_LOOKBACK_HOURS") or 24)

GMAIL_ALLOWED_SENDERS = [
    email.strip().lower()
    for email in os.getenv(
        "GMAIL_ALLOWED_SENDERS",
        ""
    ).split(",")
    if email.strip()
]

# AI Classification configuration
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Retention configuration
PROCESSED_MESSAGE_RETENTION_DAYS = int(os.getenv("PROCESSED_MESSAGE_RETENTION_DAYS", "30"))
THREAD_RETENTION_DAYS = int(os.getenv("THREAD_RETENTION_DAYS", "180"))

# Attachment configuration
REDMINE_MAX_ATTACHMENT_MB = int(os.getenv("REDMINE_MAX_ATTACHMENT_MB", "10"))
VIDEO_COMPRESSION_ENABLED = os.getenv("VIDEO_COMPRESSION_ENABLED", "true").lower() == "true"

# AI configuration
AI_MAX_INPUT_CHARS = int(os.getenv("AI_MAX_INPUT_CHARS", "12000"))
ALLOWED_MODULES = [
    m.strip()
    for m in os.getenv("ALLOWED_MODULES", "").split(",")
    if m.strip()
]
REDMINE_TEXT_FORMAT = os.getenv("REDMINE_TEXT_FORMAT", "textile")
REDMINE_MESSAGEID_CUSTOM_FIELD_ID = int(os.getenv("REDMINE_MESSAGEID_CUSTOM_FIELD_ID") or 0)

# Retry configuration
RETRY_MAX_ATTEMPTS = int(os.getenv("RETRY_MAX_ATTEMPTS", "4"))

# Paths and directories
LOG_FILE_PATH = BASE_DIR / "data" / "logs" / "gmail_redmine.log"
EMAIL_DATA_DIR = BASE_DIR / "email_data"
GENERATED_EMAILS_DIR = EMAIL_DATA_DIR / "generated_emails"
EMAIL_ATTACHMENTS_DIR = EMAIL_DATA_DIR / "email_attachments"
DATABASE_DIR = BASE_DIR / "data" / "database"


def validate_settings():
    """
    Validate required application configuration.
    """

    missing = []

    if not REDMINE_URL:
        missing.append("REDMINE_URL")

    if not REDMINE_API_KEY:
        missing.append("REDMINE_API_KEY")

    if not REDMINE_PROJECT_ID:
        missing.append("REDMINE_PROJECT_ID")

    if not REDMINE_SUPPORT_TRACKER_ID:
        missing.append("REDMINE_SUPPORT_TRACKER_ID")
    
    if not REDMINE_ASSIGNEE_ID:
        missing.append("REDMINE_ASSIGNEE_ID")
    
    # Custom fields are now optional
    # if not REDMINE_CATEGORY_CUSTOM_FIELD_ID:
    #     missing.append("REDMINE_CATEGORY_CUSTOM_FIELD_ID")
    #
    # if not REDMINE_CATEGORY_VALUE:
    #     missing.append("REDMINE_CATEGORY_VALUE")
    #
    # if not REDMINE_SEVERITY_CUSTOM_FIELD_ID:
    #     missing.append("REDMINE_SEVERITY_CUSTOM_FIELD_ID")
    #
    # if not REDMINE_SEVERITY_VALUE:
    #     missing.append("REDMINE_SEVERITY_VALUE")

    if not GMAIL_MAX_RESULTS:
        missing.append("GMAIL_MAX_RESULTS")

    if not GMAIL_LOOKBACK_HOURS:
        missing.append("GMAIL_LOOKBACK_HOURS")
    
    if not GMAIL_ALLOWED_SENDERS:
        missing.append("GMAIL_ALLOWED_SENDERS")

    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}"
        )