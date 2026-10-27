import hashlib
from io import BytesIO
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from backend.config.settings import DATABASE_DIR


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
                CREATE TABLE IF NOT EXISTS visual_image_observations (
                    sha256 TEXT NOT NULL,
                    sender_domain TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    filename TEXT NOT NULL DEFAULT '',
                    size INTEGER NOT NULL DEFAULT 0,
                    perceptual_hash TEXT NOT NULL,
                    PRIMARY KEY (sha256, sender_domain, message_id)
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_visual_image_sender
                ON visual_image_observations (sender_domain)
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
                """
                SELECT 1 FROM ignored_attachments
                WHERE sha256 = ? AND reason != 'auto-repeat'
                """,
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

    @staticmethod
    def perceptual_hash(image_data):
        try:
            with Image.open(BytesIO(image_data)) as image:
                if image.width * image.height > 40_000_000:
                    return None
                pixels = list(
                    image.convert("RGB")
                    .convert("L")
                    .resize((9, 8), Image.Resampling.LANCZOS)
                    .getdata()
                )
        except (Image.DecompressionBombError, OSError, ValueError):
            return None

        value = 0
        for row in range(8):
            offset = row * 9
            for column in range(8):
                value = (value << 1) | (
                    pixels[offset + column] > pixels[offset + column + 1]
                )
        return f"{value:016x}"

    def record_image(
        self,
        digest,
        image_data,
        sender_domain,
        message_id,
        filename,
        size,
        threshold,
        max_hash_distance,
    ):
        if not sender_domain or not message_id or threshold < 2:
            return False
        image_hash = self.perceptual_hash(image_data)
        if image_hash is None:
            return False

        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            previous_images = connection.execute(
                """
                SELECT message_id, perceptual_hash
                FROM visual_image_observations
                WHERE sender_domain = ? AND message_id != ?
                """,
                (sender_domain.lower(), message_id),
            ).fetchall()
            matching_messages = {
                previous_message_id
                for previous_message_id, previous_hash in previous_images
                if (
                    int(image_hash, 16) ^ int(previous_hash, 16)
                ).bit_count() <= max_hash_distance
            }
            connection.execute(
                """
                INSERT OR IGNORE INTO visual_image_observations
                    (sha256, sender_domain, message_id, filename, size, perceptual_hash)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    digest,
                    sender_domain.lower(),
                    message_id,
                    filename,
                    size,
                    image_hash,
                ),
            )
        return len(matching_messages) + 1 >= threshold

    def record_hit(self, digest):
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "UPDATE ignored_attachments SET hit_count = hit_count + 1 WHERE sha256 = ?",
                (digest.lower(),),
            )
