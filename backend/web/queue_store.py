import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from backend.config.settings import DATABASE_DIR


class PendingItemStore:
    def __init__(self, database_path: Path | None = None):
        self.database_path = Path(database_path or DATABASE_DIR / "pending_items.sqlite3")
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_items (
                    id TEXT PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat()

    def save(self, item_id, message_id, payload, status):
        now = self._now()
        encoded = json.dumps(payload, ensure_ascii=False)
        with self._connection() as connection:
            connection.execute(
                """
                INSERT INTO pending_items (id, message_id, payload_json, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                (item_id, message_id, encoded, status, now, now),
            )

    def list_items(self):
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, message_id, payload_json, status, created_at, updated_at "
                "FROM pending_items ORDER BY created_at DESC"
            ).fetchall()
        return [
            {
                "id": row["id"],
                "message_id": row["message_id"],
                "payload": json.loads(row["payload_json"]),
                "status": row["status"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]

    def mark_stale_pending_failed(self):
        now = self._now()
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT id, payload_json FROM pending_items "
                "WHERE status IN ('PENDING', 'WAITING_FOR_USER', 'SUPERSEDED')"
            ).fetchall()
            for row in rows:
                payload = json.loads(row["payload_json"])
                payload["status"] = "FAILED"
                payload["error"] = (
                    "This email was not approved or created. Its review was "
                    "interrupted when the app closed. Retry to reopen it."
                )
                connection.execute(
                    "UPDATE pending_items SET payload_json = ?, status = 'FAILED', "
                    "updated_at = ? WHERE id = ?",
                    (json.dumps(payload, ensure_ascii=False), now, row["id"]),
                )
