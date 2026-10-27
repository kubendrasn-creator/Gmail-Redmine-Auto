import hashlib
import os
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from backend.config.settings import DATABASE_DIR


@dataclass(frozen=True)
class FilterDecision:
    keep: bool
    reason: str


def classify_attachment(
    part_meta,
    body_html,
    *,
    signature_image_max_kb=50,
    signature_name_patterns=None,
):
    content_type = str(part_meta.get("content_type") or "").lower()
    if not content_type.startswith("image/"):
        return FilterDecision(True, "not-image")

    filename = Path(str(part_meta.get("filename") or "")).name
    size = int(part_meta.get("size") or 0)
    content_id = str(part_meta.get("content_id") or "").strip("<> ")
    cid_referenced = bool(
        content_id
        and re.search(
            rf"cid:\s*{re.escape(content_id)}(?:[\"'>\s]|$)",
            body_html or "",
            re.IGNORECASE,
        )
    )
    generic_name = bool(re.fullmatch(r"image(?:[_-]?\d+)?(?:\.[^.]+)?", filename, re.I))
    if cid_referenced and (
        size < signature_image_max_kb * 1024 or generic_name
    ):
        return FilterDecision(False, "inline-signature")

    patterns = signature_name_patterns or (
        r"image[_-]?\d*",
        r"logo",
        r"outlook-",
        r"ascwebqi",
        r"signature",
        r"banner",
    )
    if size < signature_image_max_kb * 1024 and any(
        re.search(pattern, filename, re.IGNORECASE)
        for pattern in patterns
    ):
        return FilterDecision(False, "signature-name")

    return FilterDecision(True, "kept")


class AttachmentFilterStore:
    def __init__(self, database_path=None):
        self.database_path = Path(
            database_path or DATABASE_DIR / "attachment_filter.sqlite3"
        )
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS ignored_attachments (
                    sha256 TEXT PRIMARY KEY,
                    filename TEXT NOT NULL DEFAULT '',
                    size INTEGER NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL,
                    added_at TEXT NOT NULL,
                    hit_count INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS attachment_observations (
                    sha256 TEXT NOT NULL,
                    sender_domain TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    filename TEXT NOT NULL DEFAULT '',
                    size INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (sha256, sender_domain, message_id)
                )
                """
            )
        for digest in os.getenv("IGNORED_ATTACHMENT_SHA256", "").split(","):
            digest = digest.strip().lower()
            if re.fullmatch(r"[a-f0-9]{64}", digest):
                self.ignore(digest, reason="environment")

    @staticmethod
    def sha256(data):
        return hashlib.sha256(data).hexdigest()

    def ignored(self, digest):
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            row = connection.execute(
                "SELECT 1 FROM ignored_attachments WHERE sha256 = ?",
                (digest.lower(),),
            ).fetchone()
        return row is not None

    def ignore(self, digest, filename="", size=0, reason="manual"):
        if not re.fullmatch(r"[a-f0-9]{64}", digest.lower()):
            raise ValueError("Attachment SHA-256 must be 64 hexadecimal characters")
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                """
                INSERT INTO ignored_attachments
                    (sha256, filename, size, reason, added_at, hit_count)
                VALUES (?, ?, ?, ?, ?, 0)
                ON CONFLICT(sha256) DO UPDATE SET
                    filename = CASE WHEN excluded.filename = '' THEN filename ELSE excluded.filename END,
                    size = CASE WHEN excluded.size = 0 THEN size ELSE excluded.size END,
                    reason = CASE WHEN reason = 'environment' THEN reason ELSE excluded.reason END
                """,
                (
                    digest.lower(),
                    filename,
                    size,
                    reason,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def record_image(self, digest, sender_domain, message_id, filename, size, threshold):
        if not sender_domain or not message_id:
            return False
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO attachment_observations
                    (sha256, sender_domain, message_id, filename, size)
                VALUES (?, ?, ?, ?, ?)
                """,
                (digest, sender_domain.lower(), message_id, filename, size),
            )
            count = connection.execute(
                """
                SELECT COUNT(*) FROM attachment_observations
                WHERE sha256 = ? AND sender_domain = ?
                """,
                (digest, sender_domain.lower()),
            ).fetchone()[0]
            if count >= threshold:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO ignored_attachments
                        (sha256, filename, size, reason, added_at, hit_count)
                    VALUES (?, ?, ?, 'auto-repeat', ?, 0)
                    """,
                    (
                        digest,
                        filename,
                        size,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                return connection.execute(
                    "SELECT reason FROM ignored_attachments WHERE sha256 = ?",
                    (digest,),
                ).fetchone()[0] == "auto-repeat"
        return False

    def record_hit(self, digest):
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "UPDATE ignored_attachments SET hit_count = hit_count + 1 WHERE sha256 = ?",
                (digest.lower(),),
            )
