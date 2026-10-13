import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
LOG_DIR = BASE_DIR / "data" / "logs"
LOG_FILE = LOG_DIR / "gmail_redmine.log"

_configured = False


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
    app_logger.setLevel(logging.INFO)

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    app_logger.addHandler(console_handler)

    # Rotating file handler (10MB max, 5 backups)
    file_handler = RotatingFileHandler(
        filename=str(LOG_FILE),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
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
