import json
from pathlib import Path


class ProcessedMessageStore:
    """
    Keeps track of Gmail message IDs that have already been processed.
    """

    def __init__(self, file_path="data/database/processed_messages.json"):
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)

        if not self.file_path.exists():
            self._save([])

    def _load(self):
        with self.file_path.open("r", encoding="utf-8") as file:
            return json.load(file)

    def _save(self, message_ids):
        with self.file_path.open("w", encoding="utf-8") as file:
            json.dump(
                message_ids,
                file,
                indent=2,
            )

    def contains(self, message_id):
        return message_id in self._load()

    def add(self, message_id):
        message_ids = self._load()

        if message_id not in message_ids:
            message_ids.append(message_id)
            self._save(message_ids)