import json
import os
from pathlib import Path

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

def _configured_path(value, default):
    path = Path(value) if value else default
    return path if path.is_absolute() else BASE_DIR / path


TICKET_FIELDS_FILE = _configured_path(
    os.getenv("TICKET_FIELDS_FILE"),
    ENV_DIR / "config" / "ticket_fields.json",
)
_ticket_fields = {}
if TICKET_FIELDS_FILE.exists():
    _ticket_fields = json.loads(TICKET_FIELDS_FILE.read_text(encoding="utf-8"))
    if not isinstance(_ticket_fields, dict):
        raise ValueError(f"{TICKET_FIELDS_FILE} must contain a JSON object")

_center_creation_fields = _ticket_fields.get("center_creation", {})
if not isinstance(_center_creation_fields, dict):
    raise ValueError("center_creation in ticket_fields.json must be a JSON object")

_category_names = _ticket_fields.get("categories", {})
if not isinstance(_category_names, dict):
    raise ValueError("categories in ticket_fields.json must map names to Redmine IDs")
for category_name, category_id in _category_names.items():
    if not isinstance(category_name, str) or not category_name.strip():
        raise ValueError("category names in ticket_fields.json must not be empty")
    if not str(category_id).isdigit() or int(category_id) < 1:
        raise ValueError(f"Invalid Redmine category ID for {category_name!r}")

_default_category = _center_creation_fields.get("category")
if _default_category is not None and not isinstance(_default_category, str):
    raise ValueError("center_creation.category must be a category name")
if _default_category and _category_names and _default_category not in _category_names:
    raise ValueError(
        "center_creation.category must match a name in ticket_fields.json categories"
    )
_default_module = _center_creation_fields.get("module")
if _default_module is not None and (
    not isinstance(_default_module, str) or not _default_module.strip()
):
    raise ValueError("center_creation.module must be a non-empty text value")

REDMINE_MODULE_CUSTOM_FIELD_ID = int(
    _ticket_fields.get("module_custom_field_id") or 0
)
REDMINE_MODULE_VALUE = _center_creation_fields.get("module")
REDMINE_ISSUES_CATEGORY = int(
    _category_names.get(_center_creation_fields.get("category"))
    or 0
)
REDMINE_TICKET_FIELD_DEFAULTS = {
    "center_creation": {
        "module": REDMINE_MODULE_VALUE or "",
        "category_id": REDMINE_ISSUES_CATEGORY,
        "module_value": REDMINE_MODULE_VALUE or "",
    }
}

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
AI_MAX_INPUT_CHARS = int(os.getenv("AI_MAX_INPUT_CHARS", "6000"))
AI_THINKING_BUDGET = int(os.getenv("AI_THINKING_BUDGET", "1024"))
_configured_modules = _ticket_fields.get("modules")
if _configured_modules is not None and (
    not isinstance(_configured_modules, list)
    or not all(
        isinstance(module, str) and module.strip()
        for module in _configured_modules
    )
):
    raise ValueError("modules in ticket_fields.json must be a list of non-empty names")
if _default_module and _configured_modules and _default_module not in _configured_modules:
    raise ValueError(
        "center_creation.module must be included in the ticket_fields.json modules list"
    )
ALLOWED_MODULES = (
    [module.strip() for module in _configured_modules]
    if _configured_modules is not None
    else []
)
LOGO_IMAGE_PATH = ENV_DIR / "logo.png"
LOGO_IMAGE_MAX_PIXEL_DIFFERENCE = float(
    os.getenv("LOGO_IMAGE_MAX_PIXEL_DIFFERENCE", "8")
)
if not 0 <= LOGO_IMAGE_MAX_PIXEL_DIFFERENCE <= 255:
    raise ValueError("LOGO_IMAGE_MAX_PIXEL_DIFFERENCE must be between 0 and 255")
IGNORED_ATTACHMENT_SHA256 = os.getenv("IGNORED_ATTACHMENT_SHA256", "")
STRIP_BOILERPLATE = os.getenv("STRIP_BOILERPLATE", "true").lower() == "true"
REDMINE_CENTER_CUSTOM_FIELD_ID = int(os.getenv("REDMINE_CENTER_CUSTOM_FIELD_ID") or 0)
REDMINE_FEEDBACK_TRACKER_ID = int(os.getenv("REDMINE_FEEDBACK_TRACKER_ID") or 0)
REDMINE_METADATA_TTL_MIN = int(os.getenv("REDMINE_METADATA_TTL_MIN", "60"))
PEOPLE_FILE = _configured_path(
    os.getenv("PEOPLE_FILE"),
    ENV_DIR / "config" / "people.json",
)
PEOPLE = os.getenv("PEOPLE", "")
ASSIGNEE_ACTION_WORDS = [
    value.strip().lower()
    for value in os.getenv(
        "ASSIGNEE_ACTION_WORDS",
        "pls,please,FYA,take up,follow,confirm,check,look into,kindly",
    ).split(",")
    if value.strip()
]
REQUIRED_FIELDS = [
    value.strip()
    for value in os.getenv("REQUIRED_FIELDS", "ticket_type").split(",")
    if value.strip()
]
PDF_DATE_SOURCE = os.getenv("PDF_DATE_SOURCE", "system").lower()
PDF_MAX_EMBED_MB = int(os.getenv("PDF_MAX_EMBED_MB", "5"))
EDIT_LOG_STORE_TEXT = os.getenv("EDIT_LOG_STORE_TEXT", "false").lower() == "true"
REDMINE_MESSAGEID_CUSTOM_FIELD_ID = int(os.getenv("REDMINE_MESSAGEID_CUSTOM_FIELD_ID") or 0)
WEB_PORT = int(os.getenv("WEB_PORT", "8000"))
WEB_AUTO_OPEN_BROWSER = os.getenv("WEB_AUTO_OPEN_BROWSER", "true").lower() == "true"
APP_MODE = os.getenv("APP_MODE", "ui").strip().lower()

# Retry configuration
RETRY_MAX_ATTEMPTS = int(os.getenv("RETRY_MAX_ATTEMPTS", "4"))

# Paths and directories
LOG_FILE_PATH = BASE_DIR / "data" / "logs" / "gmail_redmine.log"
EMAIL_DATA_DIR = BASE_DIR / "email_data"
GENERATED_EMAILS_DIR = EMAIL_DATA_DIR / "generated_emails"
EMAIL_ATTACHMENTS_DIR = EMAIL_DATA_DIR / "email_attachments"
ATTACHMENTS_DIR = EMAIL_ATTACHMENTS_DIR
DATABASE_DIR = BASE_DIR / "data" / "database"


def validate_settings():
    """
    Validate required application configuration.
    """

    missing = []

    if APP_MODE not in {"ui", "terminal"}:
        raise RuntimeError("APP_MODE must be either 'ui' or 'terminal'")

    supported_required_fields = {
        "subject",
        "description",
        "ticket_type",
        "center",
        "module",
        "assignee",
    }
    unsupported_required_fields = set(REQUIRED_FIELDS) - supported_required_fields
    if unsupported_required_fields:
        raise RuntimeError(
            "Unsupported REQUIRED_FIELDS value(s): "
            + ", ".join(sorted(unsupported_required_fields))
        )

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
    
    if not GMAIL_ALLOWED_SENDERS:
        missing.append("GMAIL_ALLOWED_SENDERS")

    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}"
        )