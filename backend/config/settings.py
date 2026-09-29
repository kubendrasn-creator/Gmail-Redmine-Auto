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
REDMINE_PROJECT_ID = int(os.getenv("REDMINE_PROJECT_ID"))
REDMINE_SUPPORT_TRACKER_ID = int(os.getenv("REDMINE_SUPPORT_TRACKER_ID"))
REDMINE_ASSIGNEE_ID = int(os.getenv("REDMINE_ASSIGNEE_ID"))
GMAIL_MAX_RESULTS = int(os.getenv("GMAIL_MAX_RESULTS"))
GMAIL_LOOKBACK_HOURS = int(os.getenv("GMAIL_LOOKBACK_HOURS"))

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

    if not GMAIL_MAX_RESULTS:
        missing.append("GMAIL_MAX_RESULTS")

    if not GMAIL_LOOKBACK_HOURS:
        missing.append("GMAIL_LOOKBACK_HOURS")

    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}"
        )