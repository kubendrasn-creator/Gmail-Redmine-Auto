import difflib
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from backend.config.settings import DATABASE_DIR, EDIT_LOG_STORE_TEXT, REDMINE_METADATA_TTL_MIN
from backend.ticketing.ticket_classifier import normalize_center


class ReviewMetadataStore:
    def __init__(self, database_path=None):
        self.database_path = Path(
            database_path or DATABASE_DIR / "review_metadata.sqlite3"
        )
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS known_centers (
                    center TEXT PRIMARY KEY COLLATE NOCASE,
                    use_count INTEGER NOT NULL DEFAULT 1,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS pdf_filenames (
                    message_id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS edit_log (
                    id INTEGER PRIMARY KEY,
                    message_id TEXT NOT NULL,
                    field TEXT NOT NULL,
                    ai_value TEXT,
                    final_value TEXT,
                    source TEXT,
                    status TEXT,
                    changed INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    model TEXT,
                    prompt_hash TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS decision_log (
                    message_id TEXT PRIMARY KEY,
                    suggested_action TEXT,
                    ai_category TEXT,
                    ai_confidence REAL,
                    ai_source TEXT,
                    human_action TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS redmine_metadata (
                    cache_key TEXT PRIMARY KEY,
                    value_json TEXT NOT NULL,
                    expires_at REAL NOT NULL
                );
                """
            )
            decision_columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(decision_log)")
            }
            if "ai_source" not in decision_columns:
                connection.execute(
                    "ALTER TABLE decision_log ADD COLUMN ai_source TEXT"
                )

    def centers(self):
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            return [
                row[0]
                for row in connection.execute(
                    "SELECT center FROM known_centers ORDER BY center COLLATE NOCASE"
                )
            ]

    def resolve_center(self, raw, allowed_values=None):
        value = normalize_center(raw)
        if not value:
            return {"value": "", "status": "missing", "suggestion": None}
        if allowed_values is not None:
            matched = next(
                (item for item in allowed_values if item.casefold() == value.casefold()),
                None,
            )
            if matched:
                return {"value": matched, "status": "validated", "suggestion": None}
            return {"value": "", "status": "missing", "suggestion": None}
        known = self.centers()
        exact = next((item for item in known if item.casefold() == value.casefold()), None)
        if exact:
            return {"value": exact, "status": "validated", "suggestion": None}
        closest = max(
            known,
            key=lambda item: difflib.SequenceMatcher(
                None,
                value.casefold(),
                item.casefold(),
            ).ratio(),
            default=None,
        )
        if closest and difflib.SequenceMatcher(
            None,
            value.casefold(),
            closest.casefold(),
        ).ratio() >= 0.9:
            return {"value": value, "status": "suggested", "suggestion": closest}
        return {"value": value, "status": "new", "suggestion": None}

    def add_center(self, center):
        value = normalize_center(center)
        if not value:
            return
        now = datetime.now(timezone.utc).isoformat()
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                """
                INSERT INTO known_centers (center, use_count, first_seen, last_seen)
                VALUES (?, 1, ?, ?)
                ON CONFLICT(center) DO UPDATE SET
                    use_count = known_centers.use_count + 1,
                    last_seen = excluded.last_seen
                """,
                (value, now, now),
            )

    def filename(self, message_id):
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            row = connection.execute(
                "SELECT filename FROM pdf_filenames WHERE message_id = ?",
                (message_id,),
            ).fetchone()
        return row[0] if row else None

    def save_filename(self, message_id, filename):
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                "INSERT OR REPLACE INTO pdf_filenames (message_id, filename) VALUES (?, ?)",
                (message_id, filename),
            )

    def cached_metadata(self, key):
        now = datetime.now(timezone.utc).timestamp()
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            row = connection.execute(
                "SELECT value_json FROM redmine_metadata WHERE cache_key = ? AND expires_at > ?",
                (key, now),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def cache_metadata(self, key, value):
        now = datetime.now(timezone.utc).timestamp()
        expires = now + REDMINE_METADATA_TTL_MIN * 60
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO redmine_metadata
                    (cache_key, value_json, expires_at)
                VALUES (?, ?, ?)
                """,
                (key, json.dumps(value, ensure_ascii=False), expires),
            )

    def log_decision(
        self,
        message_id,
        suggested_action,
        ai_category,
        ai_confidence,
        human_action,
        fields,
        ai_source=None,
        model=None,
        prompt_hash=None,
    ):
        now = datetime.now(timezone.utc).isoformat()
        try:
            with closing(sqlite3.connect(self.database_path)) as connection, connection:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO decision_log
                        (message_id, suggested_action, ai_category, ai_confidence,
                         ai_source, human_action, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message_id,
                        suggested_action,
                        ai_category,
                        ai_confidence,
                        ai_source,
                        human_action,
                        now,
                    ),
                )
                connection.execute(
                    "DELETE FROM edit_log WHERE message_id = ?",
                    (message_id,),
                )
                for field_name, values in fields.items():
                    ai_value = values.get("ai")
                    final_value = values.get("final")
                    if field_name in {"subject", "description"} and not EDIT_LOG_STORE_TEXT:
                        ai_value = None
                        final_value = None
                    connection.execute(
                        """
                        INSERT INTO edit_log
                            (message_id, field, ai_value, final_value, source, status,
                             changed, action, model, prompt_hash, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            message_id,
                            field_name,
                            (
                                json.dumps(ai_value, ensure_ascii=False)
                                if ai_value is not None
                                else None
                            ),
                            (
                                json.dumps(final_value, ensure_ascii=False)
                                if final_value is not None
                                else None
                            ),
                            values.get("source"),
                            values.get("status"),
                            int(str(values.get("ai")) != str(values.get("final"))),
                            human_action,
                            model,
                            prompt_hash,
                            now,
                        ),
                    )
        except sqlite3.Error:
            raise

    def edit_stats(self, days=30):
        with closing(sqlite3.connect(self.database_path)) as connection, connection:
            rows = connection.execute(
                """
                SELECT field, COUNT(*), SUM(CASE WHEN changed = 0 THEN 1 ELSE 0 END),
                       SUM(changed)
                FROM edit_log
                WHERE julianday(created_at) >= julianday('now') - ? / 86400.0
                GROUP BY field ORDER BY field
                """,
                (days * 86400,),
            ).fetchall()
            actions = connection.execute(
                """
                SELECT human_action, COUNT(*) FROM decision_log
                WHERE julianday(created_at) >= julianday('now') - ? / 86400.0
                GROUP BY human_action
                """,
                (days * 86400,),
            ).fetchall()
            fallback = connection.execute(
                """
                SELECT COUNT(*), SUM(CASE WHEN ai_source = 'fallback' THEN 1 ELSE 0 END)
                FROM decision_log
                WHERE julianday(created_at) >= julianday('now') - ? / 86400.0
                """,
                (days * 86400,),
            ).fetchone()
            corrections = connection.execute(
                """
                SELECT field, ai_value, final_value, COUNT(*) AS frequency
                FROM edit_log
                WHERE changed = 1 AND julianday(created_at) >= julianday('now') - ? / 86400.0
                  AND ai_value IS NOT NULL AND final_value IS NOT NULL
                GROUP BY field, ai_value, final_value
                ORDER BY frequency DESC
                LIMIT 5
                """,
                (days * 86400,),
            ).fetchall()
            new_centers = connection.execute(
                """
                SELECT COUNT(*) FROM edit_log
                WHERE field = 'center' AND status = 'new'
                  AND julianday(created_at) >= julianday('now') - ? / 86400.0
                """,
                (days * 86400,),
            ).fetchone()[0]
        decoded_corrections = []
        for field_name, ai_value, final_value, frequency in corrections:
            try:
                ai_value = json.loads(ai_value)
                final_value = json.loads(final_value)
            except (TypeError, json.JSONDecodeError):
                pass
            decoded_corrections.append(
                (field_name, ai_value, final_value, frequency)
            )
        return {
            "fields": rows,
            "actions": dict(actions),
            "new_centers": new_centers,
            "fallback_share": (fallback[1] or 0) / fallback[0] if fallback[0] else 0,
            "top_corrections": decoded_corrections,
        }
