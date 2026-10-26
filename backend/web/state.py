import asyncio
import concurrent.futures
import logging
import secrets
import threading
from concurrent.futures import InvalidStateError

from backend.web.queue_store import PendingItemStore


class AppState:
    def __init__(
        self,
        port=8000,
        preflight=None,
        default_dry_run=False,
        store=None,
        message_id=None,
        lookback_hours=None,
        max_results=None,
    ):
        self.port = port
        self.session_token = secrets.token_urlsafe(32)
        self.preflight = preflight or []
        self.default_dry_run = default_dry_run
        self.message_id = message_id
        self.lookback_hours = lookback_hours
        self.max_results = max_results
        self.store = store or PendingItemStore()
        self.lock = threading.RLock()
        self.run_state = "IDLE"
        self.is_running = False
        self.shutdown_requested = False
        self.run_summary = {}
        self.items = {}
        self.futures = {}
        self.subscribers = set()
        self.worker_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="gmail-redmine-worker",
        )
        self.worker_future = None
        self.log_handler = None

        self.store.mark_stale_pending_superseded()
        for saved in self.store.list_items():
            item = saved["payload"]
            item.update({
                "id": saved["id"],
                "message_id": saved["message_id"],
                "status": saved["status"],
                "created_at": saved["created_at"],
                "updated_at": saved["updated_at"],
            })
            self.items[saved["id"]] = item

    def publish(self, event_type, payload):
        event = {"type": event_type, "data": payload}
        with self.lock:
            subscribers = tuple(self.subscribers)
        for loop, queue in subscribers:
            if loop.is_closed():
                continue
            loop.call_soon_threadsafe(self._put_event, queue, event)

    @staticmethod
    def _put_event(queue, event):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass

    def save_item(self, item):
        with self.lock:
            self.items[item["id"]] = item
            self.store.save(
                item["id"],
                item["message_id"],
                item,
                item["status"],
            )

    def update_item(self, item_id, status=None, **fields):
        with self.lock:
            item = self.items.get(item_id)
            if item is None:
                return None
            if status is not None:
                item["status"] = status
            item.update(fields)
            item["updated_at"] = PendingItemStore._now()
            self.store.save(
                item_id,
                item["message_id"],
                item,
                item["status"],
            )
            return item

    def close(self):
        with self.lock:
            self.shutdown_requested = True
            pending = tuple(self.futures.values())
        from backend.processor.confirmation import (
            ConfirmAction,
            ConfirmResult,
            ReplyAction,
            ReplyDecision,
        )

        for future, reply in pending:
            if not future.done():
                try:
                    future.set_result(
                        ReplyDecision(ReplyAction.SKIP)
                        if reply else ConfirmResult(ConfirmAction.SKIP)
                    )
                except InvalidStateError:
                    pass
        self.worker_executor.shutdown(wait=True, cancel_futures=True)
        if self.log_handler is not None:
            logging.getLogger("gmail_redmine").removeHandler(self.log_handler)
