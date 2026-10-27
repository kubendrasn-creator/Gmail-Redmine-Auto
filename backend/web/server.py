import asyncio
from contextlib import asynccontextmanager
import json
import logging
import re
import socket
import threading
import time
import uuid
import webbrowser
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.config.settings import (
    ALLOWED_MODULES,
    REDMINE_MAX_ATTACHMENT_MB,
    REDMINE_URL,
    REDMINE_ASSIGNEE_ID,
    REDMINE_FEEDBACK_TRACKER_ID,
    REDMINE_TICKET_FIELD_DEFAULTS,
    VIDEO_COMPRESSION_ENABLED,
    WEB_AUTO_OPEN_BROWSER,
    WEB_PORT,
)
from backend.gmail.attachment_filter import AttachmentFilterStore
from backend.ticketing.assignees import load_people
from backend.ticketing.validation import missing_required_fields
from backend.processor.confirmation import (
    ConfirmAction,
    ConfirmResult,
    ReplyAction,
    ReplyDecision,
)
from backend.processor.ticket_processor import TicketProcessor
from backend.storage.attachment_retention import prune_expired_attachments
from backend.utils.logger import SecretRedactionFilter, get_logger
from backend.web.confirmer import WebConfirmer, redact_secrets
from backend.web.state import AppState

logger = get_logger("web")
STATIC_DIR = Path(__file__).resolve().parent / "static"
ITEM_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunRequest(StrictRequest):
    dry_run: bool = False


class ApproveRequest(StrictRequest):
    subject: str = Field(min_length=1, max_length=255)
    description: str = Field(max_length=50_000)
    attach_to_issue_id: int | None = Field(default=None, ge=1)
    ticket_type: str = Field(default="support")
    center: str = Field(default="", max_length=100)
    module: str = Field(default="", max_length=100)
    assignee_id: int | None = Field(default=None, ge=1)

    @field_validator("subject")
    @classmethod
    def subject_must_not_be_blank(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("Subject cannot be blank")
        return value


class IgnoreAttachmentRequest(StrictRequest):
    sha256: str = Field(pattern=r"^[a-fA-F0-9]{64}$")
    filename: str = Field(default="", max_length=255)
    size: int = Field(default=0, ge=0)


def _safe_text(value):
    return redact_secrets(str(value))


def _set_run_state(state: AppState, value: str):
    with state.lock:
        state.run_state = value
    state.publish("run_state", {"state": value})


def _get_item_for_message(state: AppState, message_id):
    with state.lock:
        matches = [
            item for item in state.items.values()
            if item.get("message_id") == message_id
        ]
    return max(matches, key=lambda item: item.get("created_at", "")) if matches else None


def _finish_item(state: AppState, item_id, status, **details):
    item = state.update_item(item_id, status, **details)
    if item:
        state.publish("item_updated", item)
        state.publish("item_resolved", {
            "id": item_id,
            "message_id": item.get("message_id"),
            "status": status,
        })


def _save_failed_message(state, message_id, error, dry_run):
    item_id = f"failed_{uuid.uuid4().hex}"
    item = {
        "id": item_id,
        "message_id": message_id,
        "sender": "",
        "received_at": "",
        "subject": "(Message could not be loaded)",
        "suggested_action": "REVIEW",
        "category": None,
        "is_duplicate": False,
        "is_reply": False,
        "has_attachments": False,
        "status": "FAILED",
        "email": {"body": "", "subject": "", "sender_email": "", "received_at": ""},
        "classification": {},
        "attachment_plans": [],
        "detected_links": [],
        "dry_run": dry_run,
        "error": _safe_text(error),
        "created_at": state.store._now(),
    }
    state.save_item(item)
    state.publish("item_added", item)
    state.publish("item_updated", item)


async def _event_stream(state, subscriber, queue):
    try:
        while not state.shutdown_requested:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
                data = json.dumps(redact_secrets(event["data"]), ensure_ascii=False)
                yield f"event: {event['type']}\ndata: {data}\n\n"
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
    finally:
        with state.lock:
            state.subscribers.discard(subscriber)


def _run_cycle(state: AppState, dry_run: bool, message_ids=None):
    summary = {
        "fetched": 0,
        "duplicates_skipped": 0,
        "ignored": 0,
        "created": 0,
        "attached": 0,
        "failed": 0,
        "ai_fallback": 0,
        "attachments_compressed": 0,
        "attachments_skipped": 0,
    }
    token_usage_before = state.token_usage_snapshot()
    try:
        prune_expired_attachments()
        _set_run_state(state, "FETCHING")
        processor = TicketProcessor(
            dry_run=dry_run,
            confirmer=WebConfirmer(state, dry_run=dry_run),
            token_usage_callback=state.add_token_usage,
        )
        gmail = processor.gmail
        messages = (
            [{"id": message_id} for message_id in message_ids]
            if message_ids is not None
            else (
                [{"id": state.message_id}]
                if state.message_id
                else gmail.get_messages(
                    lookback_hours=state.lookback_hours,
                    max_results=state.max_results,
                )
            )
        )
        summary["fetched"] = len(messages)

        for message in messages:
            if state.shutdown_requested:
                break
            message_id = str(message["id"])
            _set_run_state(state, "CLASSIFYING")
            item = None
            try:
                raw_email = gmail.get_message(message_id)
                email = processor.parser.parse(raw_email)
                result = processor.process_message(
                    message_id,
                    email=email,
                    interactive=True,
                )
                item = _get_item_for_message(state, message_id)
                if item is None:
                    if result.get("action") == "already_processed":
                        summary["duplicates_skipped"] += 1
                    elif result.get("action") == "not_a_ticket":
                        summary["ignored"] += 1
                    continue

                status_map = {
                    "ticket_created": "CREATED",
                    "ticket_preview": "PREVIEWED",
                    "reply_attached": "ATTACHED",
                    "reply_preview": "PREVIEWED",
                    "rejected": "REJECTED",
                    "duplicate": "REJECTED",
                    "skipped_by_user": "SKIPPED",
                }
                status = status_map.get(result.get("action"), "DONE")
                _finish_item(
                    state,
                    item["id"],
                    status,
                    result=redact_secrets(result),
                    error=None,
                )
                if status == "CREATED":
                    summary["created"] += 1
                elif status == "ATTACHED":
                    summary["attached"] += 1
                elif status == "REJECTED":
                    summary["ignored"] += 1
                if result.get("attachment_handling"):
                    summary["attachments_compressed"] += sum(
                        line.startswith("Compressed ")
                        for line in result["attachment_handling"]
                    )
                    summary["attachments_skipped"] += sum(
                        line.startswith("Skipped ")
                        for line in result["attachment_handling"]
                    )
                classification = item.get("classification", {})
                if classification.get("source") == "fallback":
                    summary["ai_fallback"] += 1
            except Exception as exc:
                logger.exception("Failed to process Gmail message %s", message_id)
                summary["failed"] += 1
                item = item or _get_item_for_message(state, message_id)
                if item:
                    _finish_item(
                        state,
                        item["id"],
                        "FAILED",
                        error=_safe_text(exc),
                    )
                else:
                    _save_failed_message(state, message_id, exc, dry_run)
            if not state.shutdown_requested:
                _set_run_state(state, "FETCHING")
    except Exception as exc:
        logger.exception("Web processing cycle failed")
        summary["error"] = _safe_text(exc)
        summary["failed"] += 1
    finally:
        token_usage_after = state.token_usage_snapshot()
        summary["token_usage"] = {
            key: token_usage_after[key] - token_usage_before[key]
            for key in token_usage_before
        }
        with state.lock:
            state.is_running = False
            state.run_summary = summary
        final_state = (
            "IDLE" if state.shutdown_requested
            else "ERROR" if summary.get("error")
            else "DONE"
        )
        _set_run_state(state, final_state)
        state.publish("run_summary", summary)


def create_app(state: AppState | None = None) -> FastAPI:
    state = state or AppState(
        port=WEB_PORT,
        default_dry_run=False,
    )
    @asynccontextmanager
    async def lifespan(_app):
        yield
        state.close()

    app = FastAPI(
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.review = state

    class WebLogHandler(logging.Handler):
        def emit(self, record):
            try:
                message = self.format(record)
                state.publish("log", {"line": _safe_text(message)})
            except Exception:
                self.handleError(record)

    log_handler = WebLogHandler(level=logging.INFO)
    log_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log_handler.addFilter(SecretRedactionFilter())
    logging.getLogger("gmail_redmine").addHandler(log_handler)
    state.log_handler = log_handler

    @app.middleware("http")
    async def enforce_local_security(request: Request, call_next):
        host = request.headers.get("host", "").lower()
        valid_hosts = {
            f"127.0.0.1:{state.port}",
            f"localhost:{state.port}",
        }
        if host not in valid_hosts:
            response = JSONResponse({"detail": "Invalid Host header"}, status_code=400)
        elif request.url.path.startswith("/api/"):
            if request.url.path == "/api/events":
                token = request.query_params.get("t")
            else:
                token = request.headers.get("x-session-token")
            if not token or not secrets_compare(token, state.session_token):
                response = JSONResponse({"detail": "Invalid session token"}, status_code=401)
            elif request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                origin = request.headers.get("origin", "").lower()
                valid_origins = {
                    f"http://127.0.0.1:{state.port}",
                    f"http://localhost:{state.port}",
                }
                if origin not in valid_origins:
                    response = JSONResponse({"detail": "Invalid Origin"}, status_code=403)
                else:
                    response = await call_next(request)
            else:
                response = await call_next(request)
        else:
            response = await call_next(request)

        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/")
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/app.js")
    async def app_js():
        return FileResponse(STATIC_DIR / "app.js", media_type="application/javascript")

    @app.get("/styles.css")
    async def styles_css():
        return FileResponse(STATIC_DIR / "styles.css", media_type="text/css")

    @app.get("/api/status")
    async def status():
        with state.lock:
            items = list(state.items.values())
            checks = [
                {
                    "name": check.name,
                    "status": check.status.value,
                    "message": _safe_text(check.message),
                }
                for check in state.preflight
            ]
            counts = {
                "pending": sum(item.get("status") == "PENDING" for item in items),
                "created": sum(item.get("status") == "CREATED" for item in items),
                "rejected": sum(item.get("status") == "REJECTED" for item in items),
            }
            return {
                "checks": checks,
                "run_state": state.run_state,
                "ffmpeg": shutil_which("ffmpeg") is not None,
                "attachment_limit_mb": REDMINE_MAX_ATTACHMENT_MB,
                "video_compression_enabled": VIDEO_COMPRESSION_ENABLED,
                "redmine_url": REDMINE_URL.rstrip("/") if REDMINE_URL else "",
                "dry_run_default": state.default_dry_run,
                "counts": counts,
                "running": state.is_running,
                "token_usage": state.token_usage_snapshot(),
            }

    @app.post("/api/run", status_code=202)
    async def start_run(request: RunRequest):
        with state.lock:
            if state.is_running:
                raise HTTPException(status_code=409, detail="A processing run is already active")
            state.is_running = True
            state.run_summary = {}
        _set_run_state(state, "FETCHING")
        state.worker_future = state.worker_executor.submit(
            _run_cycle,
            state,
            request.dry_run,
            [state.message_id] if state.message_id else None,
        )
        return {"state": "FETCHING", "dry_run": request.dry_run}

    @app.get("/api/queue")
    async def get_queue():
        with state.lock:
            return [
                {
                    key: item.get(key)
                    for key in (
                        "id", "sender", "received_at", "subject",
                        "suggested_action", "category", "is_duplicate",
                        "is_reply", "has_attachments", "status",
                    )
                }
                for item in sorted(
                    state.items.values(),
                    key=lambda value: value.get("created_at", ""),
                    reverse=True,
                )
            ]

    def require_item(item_id):
        if not ITEM_ID_PATTERN.fullmatch(item_id):
            raise HTTPException(status_code=422, detail="Invalid item ID")
        with state.lock:
            item = state.items.get(item_id)
            if item is None:
                raise HTTPException(status_code=404, detail="Item not found")
            return item

    @app.get("/api/item/{item_id}")
    async def get_item(item_id: str):
        item = require_item(item_id)
        return redact_secrets(item)

    def resolve_item(item_id, decision, status):
        item = require_item(item_id)
        with state.lock:
            entry = state.futures.get(item_id)
            if entry is None or entry[0].done():
                raise HTTPException(status_code=409, detail="Item is no longer awaiting review")
            future, _ = entry
            state.update_item(item_id, status)
            future.set_result(decision)
        state.publish("item_resolved", {
            "id": item_id,
            "message_id": item.get("message_id"),
            "status": status,
        })
        return {"id": item_id, "status": status}

    @app.post("/api/item/{item_id}/approve")
    async def approve_item(item_id: str, payload: ApproveRequest):
        item = require_item(item_id)
        missing_fields = missing_required_fields({
            "subject": payload.subject,
            "description": payload.description,
            "ticket_type": payload.ticket_type,
            "center": payload.center,
            "module": payload.module,
            "assignee": payload.assignee_id,
        })
        if missing_fields:
            raise HTTPException(
                status_code=422,
                detail="Required ticket fields are missing: "
                + ", ".join(missing_fields),
            )
        if payload.ticket_type not in {"support", "feedback"}:
            raise HTTPException(status_code=422, detail="Ticket type must be support or feedback")
        if payload.ticket_type == "feedback" and not REDMINE_FEEDBACK_TRACKER_ID:
            raise HTTPException(status_code=422, detail="Feedback tracker is not configured")
        classification = item.get("classification", {})
        if (
            ALLOWED_MODULES
            and payload.module
            and payload.module not in ALLOWED_MODULES
            and not (
                classification.get("category") == "center_creation"
                and payload.module
                == REDMINE_TICKET_FIELD_DEFAULTS["center_creation"]["module"]
            )
        ):
            raise HTTPException(status_code=422, detail="Select an allowed module")
        people = load_people()
        allowed_assignees = set(people.values())
        if REDMINE_ASSIGNEE_ID:
            allowed_assignees.add(REDMINE_ASSIGNEE_ID)
        if payload.assignee_id is not None and payload.assignee_id not in allowed_assignees:
            raise HTTPException(status_code=422, detail="Select an assignee from the configured people mapping")
        allowed_center_values = classification.get("center_list_values")
        if (
            payload.center
            and allowed_center_values is not None
            and payload.center not in allowed_center_values
        ):
            raise HTTPException(status_code=422, detail="Select a valid Redmine center value")
        if item.get("is_reply_review"):
            decision = (
                ReplyDecision(
                    ReplyAction.ATTACH,
                    issue_id=payload.attach_to_issue_id,
                )
                if payload.attach_to_issue_id
                else ReplyDecision(
                    ReplyAction.CREATE_NEW,
                    edited_subject=payload.subject,
                    edited_description=payload.description,
                    ticket_type=payload.ticket_type,
                    center=payload.center,
                    module=payload.module,
                    assignee_id=payload.assignee_id,
                )
            )
        else:
            edited = (
                payload.subject != item.get("suggested_subject")
                or payload.description != item.get("suggested_description")
                or payload.ticket_type != classification.get("ticket_type", "support")
                or payload.center != classification.get("center_or_enterprise", "")
                or payload.module != (classification.get("module") or "")
                or payload.assignee_id != classification.get("assignee_id")
            )
            decision = ConfirmResult(
                ConfirmAction.EDIT if edited else ConfirmAction.APPROVE,
                edited_subject=payload.subject,
                edited_description=payload.description,
                attach_to_issue_id=payload.attach_to_issue_id,
                ticket_type=payload.ticket_type,
                center=payload.center,
                module=payload.module,
                assignee_id=payload.assignee_id,
            )
        return resolve_item(item_id, decision, "APPROVED")

    @app.post("/api/item/{item_id}/reject")
    async def reject_item(item_id: str):
        item = require_item(item_id)
        decision = (
            ReplyDecision(ReplyAction.REJECT)
            if item.get("is_reply_review")
            else ConfirmResult(ConfirmAction.REJECT)
        )
        return resolve_item(item_id, decision, "REJECTED")

    @app.post("/api/item/{item_id}/skip")
    async def skip_item(item_id: str):
        item = require_item(item_id)
        decision = (
            ReplyDecision(ReplyAction.SKIP)
            if item.get("is_reply_review")
            else ConfirmResult(ConfirmAction.SKIP)
        )
        return resolve_item(item_id, decision, "SKIPPED")

    @app.post("/api/item/{item_id}/retry", status_code=202)
    async def retry_item(item_id: str):
        item = require_item(item_id)
        if item.get("status") != "FAILED":
            raise HTTPException(status_code=409, detail="Only failed items can be retried")
        with state.lock:
            if state.is_running:
                raise HTTPException(status_code=409, detail="A processing run is already active")
            state.is_running = True
        _set_run_state(state, "FETCHING")
        state.worker_future = state.worker_executor.submit(
            _run_cycle,
            state,
            item.get("dry_run", False),
            [item["message_id"]],
        )
        return {"state": "FETCHING", "message_id": item["message_id"]}

    @app.get("/api/events")
    async def events():
        loop = asyncio.get_running_loop()
        queue = asyncio.Queue(maxsize=200)
        subscriber = (loop, queue)
        with state.lock:
            state.subscribers.add(subscriber)

        return StreamingResponse(
            _event_stream(state, subscriber, queue),
            media_type="text/event-stream",
            headers={"Connection": "keep-alive"},
        )

    @app.post("/api/shutdown")
    async def shutdown():
        state.close()
        if getattr(app.state, "uvicorn_server", None):
            app.state.uvicorn_server.should_exit = True
        return {"state": "SHUTTING_DOWN"}

    @app.post("/api/attachments/ignore")
    async def ignore_attachment(payload: IgnoreAttachmentRequest):
        AttachmentFilterStore().ignore(
            payload.sha256,
            filename=payload.filename,
            size=payload.size,
            reason="manual",
        )
        return {"sha256": payload.sha256.lower(), "ignored": True}

    return app


def secrets_compare(left, right):
    import hmac

    return hmac.compare_digest(left, right)


def shutil_which(name):
    import shutil

    return shutil.which(name)


def _available_port(start_port, attempts=10):
    if not 1 <= start_port <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    for port in range(start_port, min(65536, start_port + attempts)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
            candidate.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                candidate.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No available localhost port in range {start_port}-{start_port + attempts - 1}")


def run_server(
    port=None,
    no_browser=False,
    auto_run=False,
    default_dry_run=False,
    preflight=None,
    message_id=None,
    lookback_hours=None,
    max_results=None,
):
    selected_port = _available_port(port or WEB_PORT)
    state = AppState(
        port=selected_port,
        preflight=preflight,
        default_dry_run=default_dry_run,
        message_id=message_id,
        lookback_hours=lookback_hours,
        max_results=max_results,
    )
    app = create_app(state)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=selected_port,
            log_config=None,
            access_log=False,
        )
    )
    app.state.uvicorn_server = server
    server_thread = threading.Thread(
        target=server.run,
        name="gmail-redmine-web-server",
        daemon=True,
    )
    server_thread.start()
    url = f"http://127.0.0.1:{selected_port}/?t={state.session_token}"

    deadline = time.monotonic() + 10
    while not server.started and server_thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        state.close()
        raise RuntimeError(f"Local web server failed to start on port {selected_port}")

    print(f"Local review UI: http://127.0.0.1:{selected_port}/")
    if not no_browser and WEB_AUTO_OPEN_BROWSER:
        webbrowser.open(url)
    if auto_run:
        with state.lock:
            state.is_running = True
        state.worker_future = state.worker_executor.submit(
            _run_cycle,
            state,
            default_dry_run,
            [message_id] if message_id else None,
        )

    try:
        server_thread.join()
    except KeyboardInterrupt:
        logger.info("Shutting down local review UI")
        server.should_exit = True
        state.close()
        server_thread.join(timeout=10)
        if server_thread.is_alive():
            logger.warning("Local review UI did not stop gracefully; forcing shutdown")
            server.force_exit = True
            server_thread.join(timeout=2)
    finally:
        state.close()
