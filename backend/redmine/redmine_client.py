import requests
from datetime import date
from typing import Optional

from backend.config.settings import (
    REDMINE_API_KEY,
    REDMINE_URL,
    REDMINE_PROJECT_ID,
    REDMINE_SUPPORT_TRACKER_ID,
    REDMINE_FEEDBACK_TRACKER_ID,
    REDMINE_ASSIGNEE_ID,
    REDMINE_CENTER_CUSTOM_FIELD_ID,
    REDMINE_MODULE_CUSTOM_FIELD_ID,
    REDMINE_MODULE_VALUE,
    REDMINE_ISSUES_CATEGORY,
    REDMINE_TICKET_FIELD_DEFAULTS,
    REDMINE_MESSAGEID_CUSTOM_FIELD_ID,
    REDMINE_SEVERITY_CUSTOM_FIELD_ID,
    REDMINE_SEVERITY_VALUE,
    REDMINE_ESTIMATED_HOURS,
    REDMINE_START_DATE,
    REDMINE_END_DATE,
)
from backend.utils.logger import get_logger
from backend.utils.retry import RedmineUploadError
from backend.storage.review_metadata import ReviewMetadataStore

logger = get_logger("redmine")


class RedmineClient:
    """
    Handles communication with the Redmine REST API.
    """

    def __init__(self, dry_run: bool = False):
        self.base_url = REDMINE_URL.rstrip("/")
        self.dry_run = dry_run

        self.headers = {
            "X-Redmine-API-Key": REDMINE_API_KEY,
            "Content-Type": "application/json",
        }
        self._fake_issue_counter = 1000

    def _redact_api_key(self, text: str) -> str:
        """Redact API key from text for logging."""
        if REDMINE_API_KEY:
            return text.replace(REDMINE_API_KEY, "***REDACTED***")
        return text

    def get_projects(self):
        """
        Retrieve projects from Redmine.
        """
        url = f"{self.base_url}/projects.json"
        response = requests.get(
            url,
            headers=self.headers,
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def get_current_user(self):
        """
        Retrieve the Redmine user associated with the API key.
        """
        url = f"{self.base_url}/users/current.json"
        response = requests.get(
            url,
            headers=self.headers,
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def get_trackers(self):
        """
        Retrieve all trackers configured in Redmine.
        """
        url = f"{self.base_url}/trackers.json"
        response = requests.get(
            url,
            headers=self.headers,
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def get_custom_fields(self):
        store = ReviewMetadataStore()
        cached = store.cached_metadata("custom_fields")
        if cached is not None:
            return cached
        response = requests.get(
            f"{self.base_url}/custom_fields.json",
            headers=self.headers,
            timeout=10,
        )
        response.raise_for_status()
        value = response.json()
        store.cache_metadata("custom_fields", value)
        return value

    def get_issue_priorities(self):
        response = requests.get(
            f"{self.base_url}/enumerations/issue_priorities.json",
            headers=self.headers,
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def get_user(self, user_id):
        response = requests.get(
            f"{self.base_url}/users/{int(user_id)}.json",
            headers=self.headers,
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _message_marker(message_id: str) -> str:
        safe_id = "".join(
            character for character in message_id
            if character.isalnum() or character in "-_"
        )
        if not safe_id:
            raise ValueError("A valid Gmail message ID is required")
        return f"<!-- gmail-message-id: {safe_id} -->"

    @staticmethod
    def build_issue_payload(
        project_id,
        subject,
        description,
        message_id=None,
        ticket_type="support",
        center=None,
        assignee_id=None,
        module=None,
        classification_category=None,
    ):
        if message_id:
            marker = RedmineClient._message_marker(str(message_id))
            description = f"{description.rstrip()}\n\n{marker}".strip()

        start_date = (
            date.today().isoformat()
            if REDMINE_START_DATE == "SYSTEM"
            else REDMINE_START_DATE
        )

        end_date = (
            date.today().isoformat()
            if REDMINE_END_DATE == "SYSTEM"
            else REDMINE_END_DATE
        )

        tracker_id = (
            REDMINE_FEEDBACK_TRACKER_ID
            if ticket_type == "feedback" and REDMINE_FEEDBACK_TRACKER_ID
            else REDMINE_SUPPORT_TRACKER_ID
        )
        issue = {
            "project_id": project_id,
            "tracker_id": tracker_id,
            "assigned_to_id": assignee_id or REDMINE_ASSIGNEE_ID,
            "subject": subject,
            "description": description,
            "estimated_hours": REDMINE_ESTIMATED_HOURS,
            "start_date": start_date,
            "due_date": end_date,
        }
        ticket_defaults = REDMINE_TICKET_FIELD_DEFAULTS.get(
            classification_category,
            {},
        )
        category_id = ticket_defaults.get(
            "category_id",
            REDMINE_ISSUES_CATEGORY,
        )
        if category_id:
            issue["category_id"] = category_id

        custom_fields = []
        module_value = REDMINE_MODULE_VALUE
        if classification_category == "center_creation":
            module_value = module or ticket_defaults.get("module_value")
        if REDMINE_MODULE_CUSTOM_FIELD_ID and module_value:
            custom_fields.append({
                "id": REDMINE_MODULE_CUSTOM_FIELD_ID,
                "value": module_value,
            })
        if REDMINE_SEVERITY_CUSTOM_FIELD_ID and REDMINE_SEVERITY_VALUE:
            custom_fields.append({
                "id": REDMINE_SEVERITY_CUSTOM_FIELD_ID,
                "value": REDMINE_SEVERITY_VALUE,
            })
        if message_id and REDMINE_MESSAGEID_CUSTOM_FIELD_ID:
            custom_fields.append({
                "id": REDMINE_MESSAGEID_CUSTOM_FIELD_ID,
                "value": str(message_id),
            })
        if center and REDMINE_CENTER_CUSTOM_FIELD_ID:
            custom_fields.append({
                "id": REDMINE_CENTER_CUSTOM_FIELD_ID,
                "value": center,
            })
        if custom_fields:
            issue["custom_fields"] = custom_fields

        return {"issue": issue}

    def create_issue(
        self,
        project_id,
        subject,
        description,
        message_id=None,
        ticket_type="support",
        center=None,
        assignee_id=None,
        module=None,
        classification_category=None,
    ):
        """
        Create a new Support issue in Redmine.
        """
        url = f"{self.base_url}/issues.json"

        payload = self.build_issue_payload(
            project_id,
            subject,
            description,
            message_id,
            ticket_type=ticket_type,
            center=center,
            assignee_id=assignee_id,
            module=module,
            classification_category=classification_category,
        )

        if self.dry_run:
            logger.info(f"[DRY-RUN] Would create issue with payload: {self._redact_api_key(str(payload))}")
            fake_issue_id = self._fake_issue_counter
            self._fake_issue_counter += 1
            return {"issue": {"id": fake_issue_id, "subject": subject}}

        try:
            response = requests.post(
                url,
                headers=self.headers,
                json=payload,
                timeout=10,
            )
        except requests.Timeout as timeout_error:
            if message_id:
                try:
                    issue_id = self.find_issue_by_message_id(str(message_id))
                except Exception as lookup_error:
                    logger.error(
                        "Could not verify timed-out Redmine create for Gmail message %s: %s",
                        message_id,
                        lookup_error,
                    )
                else:
                    if issue_id:
                        return {
                            "issue": {
                                "id": issue_id,
                                "subject": subject,
                            }
                        }
            raise timeout_error

        if not response.ok:
            logger.error(
                f"Redmine create issue failed (HTTP {response.status_code}): "
                f"{self._redact_api_key(response.text[:200])}"
            )
            response.raise_for_status()

        return response.json()

    def find_issue_by_message_id(self, message_id: str) -> Optional[int]:
        marker = self._message_marker(message_id)
        offset = 0
        limit = 100

        while True:
            response = requests.get(
                f"{self.base_url}/issues.json",
                headers=self.headers,
                params={
                    "project_id": REDMINE_PROJECT_ID,
                    "status_id": "*",
                    "limit": limit,
                    "offset": offset,
                },
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            for issue in data.get("issues", []):
                if marker in (issue.get("description") or ""):
                    return issue.get("id")

            offset += len(data.get("issues", []))
            total_count = data.get("total_count")
            if (
                not data.get("issues")
                or (total_count is not None and offset >= total_count)
                or len(data.get("issues", [])) < limit
            ):
                return None

    def get_issue(self, issue_id, include=None):
        response = requests.get(
            f"{self.base_url}/issues/{issue_id}.json",
            headers=self.headers,
            params={"include": include} if include else None,
            timeout=10,
        )
        response.raise_for_status()
        return response.json().get("issue", {})

    def update_issue_description(self, issue_id, description):
        response = requests.put(
            f"{self.base_url}/issues/{issue_id}.json",
            headers=self.headers,
            json={"issue": {"description": description}},
            timeout=10,
        )
        response.raise_for_status()
        return response.json() if response.text.strip() else None

    def upload_attachment(self, file_path):
        """
        Upload a file to Redmine and return the upload token.
        Streams the file from disk to handle large files.
        """
        url = f"{self.base_url}/uploads.json"

        with open(file_path, "rb") as file:
            response = requests.post(
                url,
                headers={
                    "X-Redmine-API-Key": REDMINE_API_KEY,
                    "Content-Type": "application/octet-stream",
                },
                data=file,
                timeout=(10, 300),  # (connect timeout, read timeout)
            )

        if not response.ok:
            logger.error(f"Redmine upload failed (HTTP {response.status_code}): {response.text[:200]}")
            if response.status_code in (413, 422):
                body = self._redact_api_key(response.text[:200])
                raise RedmineUploadError(response.status_code, body)
            response.raise_for_status()

        return response.json()["upload"]["token"]

    def attach_file_to_issue(self, issue_id, attachment):
        """
        Attach an uploaded Redmine file to an existing issue.
        """
        url = f"{self.base_url}/issues/{issue_id}.json"

        payload = {
            "issue": {
                "uploads": [
                    {
                        "token": attachment["token"],
                        "filename": attachment["filename"],
                        "content_type": attachment["content_type"],
                    }
                ]
            }
        }

        response = requests.put(
            url,
            headers=self.headers,
            json=payload,
            timeout=10,
        )

        if not response.ok:
            logger.error(f"Redmine attachment failed (HTTP {response.status_code}): {response.text}")
            response.raise_for_status()

        if response.text.strip():
            return response.json()
        return None

    def add_issue_note(self, issue_id, note):
        """
        Add a note/comment to an existing Redmine issue.
        """
        url = f"{self.base_url}/issues/{issue_id}.json"

        payload = {
            "issue": {
                "notes": note
            }
        }

        response = requests.put(
            url,
            headers=self.headers,
            json=payload,
            timeout=10,
        )

        if not response.ok:
            logger.error(f"Redmine note update failed (HTTP {response.status_code}): {response.text}")
            response.raise_for_status()

        if response.text.strip():
            return response.json()
        return None