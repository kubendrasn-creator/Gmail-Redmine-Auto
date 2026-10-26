from pathlib import Path

import pytest
import requests

from backend.redmine import redmine_client as redmine_module
from backend.redmine.redmine_client import RedmineClient
from backend.utils.retry import RedmineUploadError


def test_create_issue_timeout_recovers_by_message_marker(monkeypatch):
    monkeypatch.setattr(redmine_module, "REDMINE_URL", "https://redmine.example.test")
    monkeypatch.setattr(redmine_module, "REDMINE_API_KEY", "redmine-test-key")
    client = RedmineClient()
    monkeypatch.setattr(
        redmine_module.requests,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(requests.Timeout("timed out")),
    )
    monkeypatch.setattr(client, "find_issue_by_message_id", lambda message_id: 123)

    result = client.create_issue(
        project_id=1,
        subject="Test issue",
        description="Body",
        message_id="gmail-id-1",
    )

    assert result["issue"]["id"] == 123
    payload = client.build_issue_payload(1, "subject", "body", "gmail-id-1")
    assert "<!-- gmail-message-id: gmail-id-1 -->" in payload["issue"]["description"]


def test_timed_out_create_without_match_reraises_timeout(monkeypatch):
    monkeypatch.setattr(redmine_module, "REDMINE_URL", "https://redmine.example.test")
    client = RedmineClient()
    monkeypatch.setattr(
        redmine_module.requests,
        "post",
        lambda *args, **kwargs: (_ for _ in ()).throw(requests.Timeout("timed out")),
    )
    monkeypatch.setattr(client, "find_issue_by_message_id", lambda message_id: None)

    with pytest.raises(requests.Timeout):
        client.create_issue(1, "subject", "description", "gmail-id-2")


def test_upload_streams_file_and_converts_proxy_413_to_redmine_upload_error(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(redmine_module, "REDMINE_URL", "https://redmine.example.test")
    monkeypatch.setattr(redmine_module, "REDMINE_API_KEY", "redmine-test-key")
    client = RedmineClient()
    payload = tmp_path / "file.bin"
    payload.write_bytes(b"on-disk-data")

    class Response:
        ok = False
        status_code = 413
        text = "<html><title>Payload too large</title></html>"

        def raise_for_status(self):
            raise requests.HTTPError("413")

    call = {}

    def fake_post(*args, **kwargs):
        call.update(kwargs)
        return Response()

    monkeypatch.setattr(redmine_module.requests, "post", fake_post)
    with pytest.raises(RedmineUploadError) as raised:
        client.upload_attachment(payload)

    assert call["data"].closed
    assert raised.value.status == 413
    assert "Payload too large" in raised.value.body
