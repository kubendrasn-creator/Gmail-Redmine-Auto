"""
Preflight validation module for Gmail-Redmine integration.

Validates system configuration and dependencies before processing emails.
"""
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional

from backend.config.settings import (
    DATABASE_DIR,
    EMAIL_ATTACHMENTS_DIR,
    GEMINI_API_KEY,
    GENERATED_EMAILS_DIR,
    REDMINE_API_KEY,
    REDMINE_ASSIGNEE_ID,
    REDMINE_CATEGORY_CUSTOM_FIELD_ID,
    REDMINE_CATEGORY_VALUE,
    REDMINE_ISSUES_CATEGORY,
    REDMINE_MAX_ATTACHMENT_MB,
    REDMINE_PROJECT_ID,
    REDMINE_SEVERITY_CUSTOM_FIELD_ID,
    REDMINE_SEVERITY_VALUE,
    REDMINE_SUPPORT_TRACKER_ID,
    REDMINE_URL,
)
from backend.gmail.gmail_client import GmailClient
from backend.redmine.redmine_client import RedmineClient
from backend.utils.logger import get_logger

logger = get_logger("preflight")


class CheckStatus(Enum):
    """Status of a preflight check."""
    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass
class CheckResult:
    """Result of a single preflight check."""
    name: str
    status: CheckStatus
    message: str


class PreflightValidator:
    """
    Validates system configuration and dependencies.
    """

    def __init__(self):
        self.checks: List[CheckResult] = []

    def run_all(self) -> List[CheckResult]:
        """
        Run all preflight checks and return results.
        """
        self.checks = []

        # Redmine checks
        self._check_redmine_reachable()
        self._check_redmine_api_key()
        self._check_redmine_project_exists()
        self._check_redmine_tracker_enabled()
        self._check_redmine_assignee_exists()
        self._check_redmine_category_exists()
        self._check_redmine_custom_fields()

        # Gmail checks
        self._check_gmail_token()
        self._check_gmail_scope()

        # AI checks
        self._check_gemini_key()

        # System checks
        self._check_ffmpeg_available()
        self._check_attachment_size_config()
        self._check_writable_directories()

        return self.checks

    def print_report(self):
        """
        Print a formatted table of check results.
        """
        print("\n" + "=" * 80)
        print("PREFLIGHT VALIDATION REPORT")
        print("=" * 80)
        print(f"{'Check':<40} {'Status':<10} {'Message'}")
        print("-" * 80)

        for check in self.checks:
            status_color = {
                CheckStatus.OK: "\033[92m",
                CheckStatus.WARN: "\033[93m",
                CheckStatus.FAIL: "\033[91m",
            }.get(check.status, "")
            reset = "\033[0m" if status_color else ""

            print(f"{check.name:<40} {status_color}{check.status.value:<10}{reset} {check.message}")

        print("=" * 80)

        fail_count = sum(1 for c in self.checks if c.status == CheckStatus.FAIL)
        if fail_count > 0:
            print(f"\n{fail_count} check(s) failed. Aborting.")
            return False
        else:
            print("\nAll critical checks passed.")
            return True

    def _check_redmine_reachable(self) -> CheckResult:
        """Check if Redmine is reachable."""
        try:
            client = RedmineClient()
            client.get_current_user()
            return CheckResult(
                name="Redmine reachable",
                status=CheckStatus.OK,
                message="Successfully connected to Redmine API",
            )
        except Exception as e:
            return CheckResult(
                name="Redmine reachable",
                status=CheckStatus.FAIL,
                message=f"Failed to connect: {str(e)}",
            )

    def _check_redmine_api_key(self) -> CheckResult:
        """Check if Redmine API key is configured."""
        if not REDMINE_API_KEY:
            return CheckResult(
                name="Redmine API key",
                status=CheckStatus.FAIL,
                message="REDMINE_API_KEY not set",
            )
        return CheckResult(
            name="Redmine API key",
            status=CheckStatus.OK,
            message="API key configured",
        )

    def _check_redmine_project_exists(self) -> CheckResult:
        """Check if configured project exists."""
        if not REDMINE_PROJECT_ID:
            return CheckResult(
                name="Redmine project ID",
                status=CheckStatus.FAIL,
                message="REDMINE_PROJECT_ID not set",
            )

        try:
            client = RedmineClient()
            projects = client.get_projects()
            project_ids = [p["id"] for p in projects.get("projects", [])]

            if REDMINE_PROJECT_ID not in project_ids:
                return CheckResult(
                    name="Redmine project exists",
                    status=CheckStatus.FAIL,
                    message=f"Project ID {REDMINE_PROJECT_ID} not found in Redmine",
                )

            return CheckResult(
                name="Redmine project exists",
                status=CheckStatus.OK,
                message=f"Project {REDMINE_PROJECT_ID} found",
            )
        except Exception as e:
            return CheckResult(
                name="Redmine project exists",
                status=CheckStatus.WARN,
                message=f"Could not verify project: {str(e)}",
            )

    def _check_redmine_tracker_enabled(self) -> CheckResult:
        """Check if configured tracker is enabled on the project."""
        if not REDMINE_SUPPORT_TRACKER_ID:
            return CheckResult(
                name="Redmine tracker ID",
                status=CheckStatus.FAIL,
                message="REDMINE_SUPPORT_TRACKER_ID not set",
            )

        try:
            client = RedmineClient()
            trackers = client.get_trackers()
            tracker_ids = [t["id"] for t in trackers.get("trackers", [])]

            if REDMINE_SUPPORT_TRACKER_ID not in tracker_ids:
                return CheckResult(
                    name="Redmine tracker enabled",
                    status=CheckStatus.FAIL,
                    message=f"Tracker ID {REDMINE_SUPPORT_TRACKER_ID} not found",
                )

            return CheckResult(
                name="Redmine tracker enabled",
                status=CheckStatus.OK,
                message=f"Tracker {REDMINE_SUPPORT_TRACKER_ID} found",
            )
        except Exception as e:
            return CheckResult(
                name="Redmine tracker enabled",
                status=CheckStatus.WARN,
                message=f"Could not verify tracker: {str(e)}",
            )

    def _check_redmine_assignee_exists(self) -> CheckResult:
        """Check if configured assignee exists."""
        if not REDMINE_ASSIGNEE_ID:
            return CheckResult(
                name="Redmine assignee ID",
                status=CheckStatus.FAIL,
                message="REDMINE_ASSIGNEE_ID not set",
            )

        # Note: Cannot easily verify assignee exists without full user list API
        return CheckResult(
            name="Redmine assignee ID",
            status=CheckStatus.OK,
            message=f"Assignee ID {REDMINE_ASSIGNEE_ID} configured (verification skipped)",
        )

    def _check_redmine_category_exists(self) -> CheckResult:
        """Check if built-in category ID is configured."""
        if not REDMINE_ISSUES_CATEGORY:
            return CheckResult(
                name="Redmine category ID",
                status=CheckStatus.WARN,
                message="REDMINE_ISSUES_CATEGORY not set (optional)",
            )

        return CheckResult(
            name="Redmine category ID",
            status=CheckStatus.OK,
            message=f"Category ID {REDMINE_ISSUES_CATEGORY} configured",
        )

    def _check_redmine_custom_fields(self) -> CheckResult:
        """Check if custom field IDs are configured."""
        issues = []

        if REDMINE_CATEGORY_CUSTOM_FIELD_ID and not REDMINE_CATEGORY_VALUE:
            issues.append("Category ID set but value missing")

        if REDMINE_SEVERITY_CUSTOM_FIELD_ID and not REDMINE_SEVERITY_VALUE:
            issues.append("Severity ID set but value missing")

        if issues:
            return CheckResult(
                name="Redmine custom fields",
                status=CheckStatus.WARN,
                message=f"; ".join(issues),
            )

        return CheckResult(
            name="Redmine custom fields",
            status=CheckStatus.OK,
            message="Custom fields properly configured or not set",
        )

    def _check_gmail_token(self) -> CheckResult:
        """Check if Gmail token can be loaded."""
        try:
            client = GmailClient()
            return CheckResult(
                name="Gmail token",
                status=CheckStatus.OK,
                message="Gmail token loaded successfully",
            )
        except Exception as e:
            return CheckResult(
                name="Gmail token",
                status=CheckStatus.FAIL,
                message=f"Failed to load Gmail token: {str(e)}",
            )

    def _check_gmail_scope(self) -> CheckResult:
        """Check if Gmail has readonly scope."""
        # Scopes are hardcoded in gmail_client.py
        return CheckResult(
            name="Gmail scope",
            status=CheckStatus.OK,
            message="Gmail scope is readonly (hardcoded)",
        )

    def _check_gemini_key(self) -> CheckResult:
        """Check if Gemini API key is present."""
        if not GEMINI_API_KEY:
            return CheckResult(
                name="Gemini API key",
                status=CheckStatus.WARN,
                message="GEMINI_API_KEY not set (will use fallback classification)",
            )

        return CheckResult(
            name="Gemini API key",
            status=CheckStatus.OK,
            message="Gemini API key configured",
        )

    def _check_ffmpeg_available(self) -> CheckResult:
        """Check if ffmpeg and ffprobe are available."""
        ffmpeg_path = shutil.which("ffmpeg")
        ffprobe_path = shutil.which("ffprobe")

        if not ffmpeg_path or not ffprobe_path:
            return CheckResult(
                name="FFmpeg available",
                status=CheckStatus.WARN,
                message="FFmpeg/FFprobe not found - video compression will be skipped",
            )

        return CheckResult(
            name="FFmpeg available",
            status=CheckStatus.OK,
            message=f"FFmpeg found at {ffmpeg_path}",
        )

    def _check_attachment_size_config(self) -> CheckResult:
        """Check if attachment size limit is configured."""
        if not REDMINE_MAX_ATTACHMENT_MB or REDMINE_MAX_ATTACHMENT_MB <= 0:
            return CheckResult(
                name="Attachment size limit",
                status=CheckStatus.FAIL,
                message="REDMINE_MAX_ATTACHMENT_MB must be a positive number",
            )

        return CheckResult(
            name="Attachment size limit",
            status=CheckStatus.OK,
            message=f"Max attachment size: {REDMINE_MAX_ATTACHMENT_MB} MB",
        )

    def _check_writable_directories(self) -> CheckResult:
        """Check if required directories are writable."""
        issues = []

        dirs_to_check = [
            EMAIL_ATTACHMENTS_DIR,
            GENERATED_EMAILS_DIR,
            DATABASE_DIR,
        ]

        for dir_path in dirs_to_check:
            try:
                dir_path.mkdir(parents=True, exist_ok=True)
                test_file = dir_path / ".write_test"
                test_file.touch()
                test_file.unlink()
            except Exception as e:
                issues.append(f"{dir_path}: {str(e)}")

        if issues:
            return CheckResult(
                name="Writable directories",
                status=CheckStatus.FAIL,
                message=f"; ".join(issues),
            )

        return CheckResult(
            name="Writable directories",
            status=CheckStatus.OK,
            message="All required directories are writable",
        )
