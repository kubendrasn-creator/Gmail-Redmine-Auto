import requests
from datetime import date

from backend.config.settings import (
    REDMINE_API_KEY,
    REDMINE_URL,
    REDMINE_SUPPORT_TRACKER_ID,
    REDMINE_ASSIGNEE_ID,
    REDMINE_CATEGORY_CUSTOM_FIELD_ID,
    REDMINE_CATEGORY_VALUE,
    REDMINE_ISSUES_CATEGORY,
    REDMINE_ISSUES_CATEGORY_VALUE,
    REDMINE_SEVERITY_CUSTOM_FIELD_ID,
    REDMINE_SEVERITY_VALUE,
    REDMINE_ESTIMATED_HOURS,
    REDMINE_START_DATE,
    REDMINE_END_DATE,
)
from backend.utils.logger import get_logger

logger = get_logger("redmine")


class RedmineClient:
    """
    Handles communication with the Redmine REST API.
    """

    def __init__(self):
        self.base_url = REDMINE_URL.rstrip("/")

        self.headers = {
            "X-Redmine-API-Key": REDMINE_API_KEY,
            "Content-Type": "application/json",
        }

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

    def create_issue(
        self,
        project_id,
        subject,
        description,
    ):
        """
        Create a new Support issue in Redmine.
        """
        url = f"{self.base_url}/issues.json"

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

        issue = {
            "project_id": project_id,
            "tracker_id": REDMINE_SUPPORT_TRACKER_ID,
            "assigned_to_id": REDMINE_ASSIGNEE_ID,
            "subject": subject,
            "description": description,
            "estimated_hours": REDMINE_ESTIMATED_HOURS,
            "start_date": start_date,
            "due_date": end_date,
        }

        # Add built-in category if configured
        if REDMINE_ISSUES_CATEGORY:
            issue["category_id"] = REDMINE_ISSUES_CATEGORY

        # Add custom fields
        custom_fields = []
        if REDMINE_CATEGORY_CUSTOM_FIELD_ID and REDMINE_CATEGORY_VALUE:
            custom_fields.append({
                "id": REDMINE_CATEGORY_CUSTOM_FIELD_ID,
                "value": REDMINE_CATEGORY_VALUE,
            })
        if REDMINE_SEVERITY_CUSTOM_FIELD_ID and REDMINE_SEVERITY_VALUE:
            custom_fields.append({
                "id": REDMINE_SEVERITY_CUSTOM_FIELD_ID,
                "value": REDMINE_SEVERITY_VALUE,
            })

        if custom_fields:
            issue["custom_fields"] = custom_fields

        payload = {
            "issue": issue
        }

        response = requests.post(
            url,
            headers=self.headers,
            json=payload,
            timeout=10,
        )

        if not response.ok:
            logger.error(f"Redmine create issue failed (HTTP {response.status_code}): {response.text}")
            response.raise_for_status()

        return response.json()

    def upload_attachment(self, file_path):
        """
        Upload a file to Redmine and return the upload token.
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
                timeout=30,
            )

        if not response.ok:
            logger.error(f"Redmine upload failed (HTTP {response.status_code}): {response.text}")
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