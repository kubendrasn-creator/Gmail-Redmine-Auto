import hashlib
from io import BytesIO
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from backend.config.settings import (
    DATABASE_DIR,
    LOGO_IMAGE_MAX_PIXEL_DIFFERENCE,
    LOGO_IMAGE_PATH,
)

_COMPARISON_SIZE = (48, 48)


class AttachmentFilterStore:
    def __init__(self, database_path=None):
        self.database_path = Path(
            database_path or DATABASE_DIR / "attachment_filter.sqlite3"
        )
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.logo_pixels = None
        if LOGO_IMAGE_PATH.exists():
            self.logo_pixels = self.normalized_pixels(
                LOGO_IMAGE_PATH.read_bytes()
            )
            if self.logo_pixels is None:
                raise ValueError(
                    f"Could not read a supported image from {LOGO_IMAGE_PATH}"
                )
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
    def normalized_pixels(image_data):
        try:
            with Image.open(BytesIO(image_data)) as image:
                if image.width * image.height > 40_000_000:
                    return None
                image = image.convert("RGBA")
                image.thumbnail(_COMPARISON_SIZE, Image.Resampling.LANCZOS)
                canvas = Image.new("RGB", _COMPARISON_SIZE, "white")
                canvas.paste(
                    image,
                    (
                        (_COMPARISON_SIZE[0] - image.width) // 2,
                        (_COMPARISON_SIZE[1] - image.height) // 2,
                    ),
                    image.getchannel("A"),
                )
                return canvas.tobytes()
        except (Image.DecompressionBombError, OSError, ValueError):
            return None

    def matches_logo(self, image_data):
        if self.logo_pixels is None:
            return False
        image_pixels = self.normalized_pixels(image_data)
        if image_pixels is None:
            return False
        mean_pixel_difference = sum(
            abs(reference - candidate)
            for reference, candidate in zip(self.logo_pixels, image_pixels)
        ) / len(self.logo_pixels)
        return mean_pixel_difference <= LOGO_IMAGE_MAX_PIXEL_DIFFERENCE

    def record_hit(self, digest):
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "UPDATE ignored_attachments SET hit_count = hit_count + 1 WHERE sha256 = ?",
                (digest.lower(),),
            )
