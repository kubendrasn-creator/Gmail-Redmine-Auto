import requests

from backend.config.settings import (
    REDMINE_API_KEY,
    REDMINE_URL,
    REDMINE_SUPPORT_TRACKER_ID,
    REDMINE_ASSIGNEE_ID,
)


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

        issue = {
            "project_id": project_id,
            "tracker_id": REDMINE_SUPPORT_TRACKER_ID,
            "assigned_to_id": REDMINE_ASSIGNEE_ID,
            "subject": subject,
            "description": description,
        }

        payload = {
            "issue": issue
        }

        response = requests.post(
            url,
            headers=self.headers,
            json=payload,
            timeout=10,
        )

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
            print("Redmine upload failed")
            print("Status:", response.status_code)
            print("Response:", response.text)

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
            print("Redmine attachment failed")
            print("Status:", response.status_code)
            print("Response:", response.text)
            response.raise_for_status()

        # Redmine may return an empty successful response.
        if not response.text.strip():
            return None

        return response.json()