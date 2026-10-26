import logging
from logging.handlers import RotatingFileHandler
from functools import lru_cache
from pathlib import Path
import json

from backend.config.settings import BASE_DIR, GEMINI_API_KEY, REDMINE_API_KEY

BASE_DIR = Path(__file__).resolve().parents[2]
LOG_DIR = BASE_DIR / "data" / "logs"
LOG_FILE = LOG_DIR / "gmail_redmine.log"

_configured = False


@lru_cache(maxsize=1)
def _known_secrets():
    values = {secret for secret in (GEMINI_API_KEY, REDMINE_API_KEY) if secret}
    for path in (
        BASE_DIR / "env" / "token.json",
        BASE_DIR / "env" / "credentials.json",
    ):
        try:
            contents = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        pending = [contents]
        while pending:
            value = pending.pop()
            if isinstance(value, dict):
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
            elif isinstance(value, str) and len(value) >= 8:
                values.add(value)
    return tuple(values)


class SecretRedactionFilter(logging.Filter):
    def __init__(self):
        super().__init__()
        self.secrets = _known_secrets()

    def filter(self, record):
        message = record.getMessage()
        for secret in self.secrets:
            message = message.replace(secret, "[REDACTED]")
        record.msg = message
        record.args = ()
        return True


def setup_logging():
    global _configured
    if _configured:
        return

    LOG_DIR.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Root application logger
    app_logger = logging.getLogger("gmail_redmine")
    app_logger.setLevel(logging.DEBUG)
    redaction_filter = SecretRedactionFilter()

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(redaction_filter)
    app_logger.addHandler(console_handler)

    # Rotating file handler (10MB max, 5 backups)
    file_handler = RotatingFileHandler(
        filename=str(LOG_FILE),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    file_handler.addFilter(redaction_filter)
    app_logger.addHandler(file_handler)

    _configured = True


def get_logger(name=None):
    """
    Get a logger instance with formatting and file+console outputs configured.
    """
    setup_logging()
    if not name or name == "__main__":
        return logging.getLogger("gmail_redmine")
    return logging.getLogger(f"gmail_redmine.{name}")
