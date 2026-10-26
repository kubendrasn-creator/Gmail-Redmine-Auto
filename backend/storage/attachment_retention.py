import time
from pathlib import Path

from backend.config.settings import ATTACHMENTS_DIR, PROCESSED_MESSAGE_RETENTION_DAYS
from backend.utils.logger import get_logger

logger = get_logger("attachment_retention")


def prune_expired_attachments(
    directory: Path = ATTACHMENTS_DIR,
    retention_days: int = PROCESSED_MESSAGE_RETENTION_DAYS,
) -> int:
    """Remove attachment files older than the processed-message retention window."""
    directory = Path(directory)
    if not directory.exists():
        return 0

    cutoff = time.time() - retention_days * 24 * 60 * 60
    removed = 0
    for path in directory.rglob("*"):
        try:
            if (
                path.is_file()
                and path.name != ".gitkeep"
                and path.stat().st_mtime < cutoff
            ):
                path.unlink()
                removed += 1
        except OSError as exc:
            logger.warning("Could not prune expired attachment %s: %s", path, exc)

    for path in sorted(directory.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir():
            try:
                path.rmdir()
            except OSError:
                pass
    return removed
