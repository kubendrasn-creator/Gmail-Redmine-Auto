import asyncio
import concurrent.futures
import threading
import time

from fastapi.testclient import TestClient

from backend.confirm import ConfirmAction, ConfirmResult, ReplyAction, ReplyDecision
from backend.web.confirmer import WebConfirmer
from backend.web.queue_store import PendingItemStore
from backend.web.server import _event_stream, create_app
from backend.web.state import AppState


PORT = 8765
ORIGIN = f"http://127.0.0.1:{PORT}"


def make_state(tmp_path):
    return AppState(
        port=PORT,
        store=PendingItemStore(tmp_path / "pending.sqlite3"),
    )


def headers(state, origin=False):
    values = {
        "host": f"127.0.0.1:{PORT}",
        "X-Session-Token": state.session_token,
    }
    if origin:
        values["Origin"] = ORIGIN
    return values


def add_item(state, item_id="item-1", body="<script>alert(1)</script><img onerror=alert(2)>"):
    item = {
        "id": item_id,
        "message_id": "gmail-id",
        "sender": "sender@example.test",
        "received_at": "2026-10-26",
        "subject": "Review subject",
        "suggested_subject": "Review subject",
        "suggested_description": "Suggested body",
        "suggested_action": "REVIEW",
        "category": "issue",
        "is_duplicate": False,
        "is_reply": False,
        "has_attachments": False,
        "status": "PENDING",
        "email": {"body": body, "subject": "Original"},
        "classification": {
            "category": "issue",
            "issue_evidence": "<script>alert(1)</script>",
        },
    }
    state.save_item(item)
    return item


def test_host_token_origin_and_security_headers(tmp_path):
    state = make_state(tmp_path)
    app = create_app(state)
    with TestClient(app, base_url=ORIGIN) as client:
        assert client.get("/api/status").status_code == 401
        assert client.get(
            "/api/status",
            headers={"host": "attacker.example", "X-Session-Token": state.session_token},
        ).status_code == 400
        assert client.get(
            "/api/status",
            headers={**headers(state), "X-Session-Token": "wrong"},
        ).status_code == 401

        response = client.get("/api/status", headers=headers(state))
        assert response.status_code == 200
        assert response.headers["content-security-policy"] == (
            "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
        )
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"

        foreign_origin = client.post(
            "/api/shutdown",
            headers={**headers(state), "Origin": "http://evil.example"},
        )
        assert foreign_origin.status_code == 403


def test_untrusted_email_is_returned_as_data_and_js_never_uses_html_insertion(tmp_path):
    state = make_state(tmp_path)
    add_item(state)
    app = create_app(state)
    with TestClient(app, base_url=ORIGIN) as client:
        response = client.get("/api/item/item-1", headers=headers(state))
        assert response.status_code == 200
        assert response.json()["email"]["body"] == (
            "<script>alert(1)</script><img onerror=alert(2)>"
        )
        script = client.get("/app.js", headers={"host": f"127.0.0.1:{PORT}"})
        assert script.status_code == 200
        assert "innerHTML" not in script.text


def test_web_confirmer_approve_returns_edited_values(tmp_path):
    state = make_state(tmp_path)
    app = create_app(state)
    confirmer = WebConfirmer(state)
    draft = {
        "message_id": "gmail-id",
        "email": {
            "subject": "Original subject",
            "sender_email": "sender@example.test",
            "body": "Issue text",
        },
        "classification": {
            "is_ticket": True,
            "category": "issue",
            "suggested_subject": "Suggested subject",
            "suggested_description": "Suggested description",
        },
        "suggested_action": "CREATE",
    }
    output = {}
    worker = threading.Thread(
        target=lambda: output.setdefault(
            "result",
            confirmer.confirm_new_ticket(draft),
        )
    )
    with TestClient(app, base_url=ORIGIN) as client:
        worker.start()
        deadline = time.monotonic() + 3
        while not state.futures and time.monotonic() < deadline:
            time.sleep(0.01)
        item_id = next(iter(state.futures))
        response = client.post(
            f"/api/item/{item_id}/approve",
            headers=headers(state, origin=True),
            json={
                "subject": "Edited subject",
                "description": "Edited description",
            },
        )
        assert response.status_code == 200
        worker.join(timeout=2)

    assert not worker.is_alive()
    assert output["result"] == ConfirmResult(
        ConfirmAction.EDIT,
        edited_subject="Edited subject",
        edited_description="Edited description",
    )


def test_reply_approval_can_attach_to_chosen_issue(tmp_path):
    state = make_state(tmp_path)
    app = create_app(state)
    future = concurrent.futures.Future()
    item = add_item(state)
    item["is_reply_review"] = True
    state.futures[item["id"]] = (future, True)

    with TestClient(app, base_url=ORIGIN) as client:
        response = client.post(
            f"/api/item/{item['id']}/approve",
            headers=headers(state, origin=True),
            json={
                "subject": "Reply",
                "description": "Description",
                "attach_to_issue_id": 123,
            },
        )
        assert response.status_code == 200

    assert future.result() == ReplyDecision(ReplyAction.ATTACH, issue_id=123)


def test_run_conflict_and_validation(tmp_path):
    state = make_state(tmp_path)
    state.is_running = True
    app = create_app(state)
    with TestClient(app, base_url=ORIGIN) as client:
        conflict = client.post(
            "/api/run",
            headers=headers(state, origin=True),
            json={"dry_run": False},
        )
        assert conflict.status_code == 409

        long_subject = client.post(
            "/api/item/not!valid/approve",
            headers=headers(state, origin=True),
            json={"subject": "x" * 256, "description": ""},
        )
        assert long_subject.status_code == 422

        extra_field = client.post(
            "/api/run",
            headers=headers(state, origin=True),
            json={"dry_run": False, "unexpected": True},
        )
        assert extra_field.status_code == 422


def test_shutdown_releases_pending_future_as_skip_without_deadlock(tmp_path):
    state = make_state(tmp_path)
    item = add_item(state)
    future = concurrent.futures.Future()
    state.futures[item["id"]] = (future, False)
    waited = {}
    worker = threading.Thread(target=lambda: waited.setdefault("decision", future.result(timeout=3)))
    worker.start()
    app = create_app(state)

    with TestClient(app, base_url=ORIGIN) as client:
        response = client.post(
            "/api/shutdown",
            headers=headers(state, origin=True),
        )
        assert response.status_code == 200

    worker.join(timeout=2)
    assert not worker.is_alive()
    assert waited["decision"] == ConfirmResult(ConfirmAction.SKIP)


def test_sse_delivers_events_in_order_and_unsubscribes(tmp_path):
    state = make_state(tmp_path)
    async def exercise_stream():
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue(maxsize=10)
        subscriber = (loop, queue)
        state.subscribers.add(subscriber)
        stream = _event_stream(state, subscriber, queue)

        def publish():
            state.publish("item_added", {"id": "one"})
            state.publish("item_resolved", {"id": "one"})

        publisher = threading.Thread(target=publish)
        publisher.start()
        first = await asyncio.wait_for(stream.__anext__(), timeout=2)
        second = await asyncio.wait_for(stream.__anext__(), timeout=2)
        await stream.aclose()
        publisher.join(timeout=2)
        return first, second

    first, second = asyncio.run(exercise_stream())
    assert first.startswith("event: item_added\n")
    assert second.startswith("event: item_resolved\n")
    assert not state.subscribers
