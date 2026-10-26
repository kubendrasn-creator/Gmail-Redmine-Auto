import base64
from unittest.mock import Mock

from backend.gmail.gmail_client import GmailClient


def test_download_attachment_strips_path_components(tmp_path):
    client = GmailClient.__new__(GmailClient)
    client.service = Mock()
    client.service.users.return_value.messages.return_value.get.return_value.execute.return_value = {
        "payload": {
            "filename": "",
            "parts": [
                {
                    "filename": r"..\..\outside.txt",
                    "mimeType": "text/plain",
                    "body": {"attachmentId": "attachment-1"},
                }
            ],
        }
    }
    client.service.users.return_value.messages.return_value.attachments.return_value.get.return_value.execute.return_value = {
        "data": base64.urlsafe_b64encode(b"safe content").decode("ascii").rstrip("=")
    }

    output_dir = tmp_path / "attachments"
    attachments = client.download_attachments("message-1", output_dir)

    assert len(attachments) == 1
    path = tmp_path / attachments[0]["path"]
    assert path.parent == output_dir
    assert path.name == "outside.txt"
    assert path.read_bytes() == b"safe content"
    assert not (tmp_path / "outside.txt").exists()
