"""
Link detection module for extracting external links from email bodies.
"""
import re
from typing import List
from dataclasses import dataclass

from backend.utils.logger import get_logger

logger = get_logger("links")


@dataclass
class LinkInfo:
    """Information about a detected link."""
    url: str
    link_type: str


class LinkDetector:
    """
    Detects external service links in email bodies.
    """

    # Patterns for common file sharing and video services
    LINK_PATTERNS = {
        "Google Drive": r"https?://drive\.google\.com/[^\s]+",
        "Google Docs": r"https?://docs\.google\.com/[^\s]+",
        "Dropbox": r"https?://(?:www\.)?dropbox\.com/[^\s]+",
        "WeTransfer": r"https?://(?:www\.)?we\.tl/[^\s]+",
        "Loom": r"https?://(?:www\.)?loom\.com/[^\s]+",
        "OneDrive": r"https?://(?:www\.)?1drv\.ms/[^\s]+",
        "SharePoint": r"https?://(?:www\.)?sharepoint\.com/[^\s]+",
        "YouTube": r"https?://(?:www\.)?(?:youtube\.com/watch\?v=|youtu\.be/)[^\s]+",
    }

    def __init__(self):
        # Compile all patterns into a single regex for efficiency
        self.pattern = re.compile(
            "|".join(self.LINK_PATTERNS.values()),
            re.IGNORECASE,
        )

    def detect_links(self, body: str) -> List[LinkInfo]:
        """
        Detect all external links in the email body.
        """
        if not body:
            return []

        links = []
        for match in self.pattern.finditer(body):
            url = match.group(0)
            link_type = self._classify_link(url)
            links.append(LinkInfo(url=url, link_type=link_type))

        return links

    def has_external_links(self, body: str) -> bool:
        """
        Check if the email body contains any external links.
        """
        return bool(self.detect_links(body))

    def is_attachment_only(self, body: str, has_attachments: bool) -> bool:
        """
        Determine if the email is attachment/link only.
        True if: body is short (<40 chars after stripping signatures/quotes)
              AND has at least one attachment or link.
        """
        if not has_attachments and not self.has_external_links(body):
            return False

        # Strip signatures and quotes (simple heuristic)
        stripped = self._strip_signatures_and_quotes(body)
        return len(stripped) < 40

    def _classify_link(self, url: str) -> str:
        """
        Classify a link by service type.
        """
        for service, pattern in self.LINK_PATTERNS.items():
            if re.search(pattern, url, re.IGNORECASE):
                return service
        return "Other"

    def _strip_signatures_and_quotes(self, body: str) -> str:
        """
        Strip common signature patterns and quoted content.
        Simple heuristic for attachment-only detection.
        """
        lines = body.splitlines()
        content_lines = []

        for line in lines:
            line = line.strip()
            # Skip signature lines
            if line.lower().startswith(("regards", "thanks", "best", "sincerely", "--")):
                continue
            # Skip quote lines
            if line.startswith(">"):
                continue
            content_lines.append(line)

        return " ".join(content_lines)
