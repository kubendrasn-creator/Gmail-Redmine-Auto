import asyncio
import concurrent.futures
import logging
import secrets
import threading
from concurrent.futures import Future, InvalidStateError

from backend.web.queue_store import PendingItemStore


class _DaemonExecutor(concurrent.futures.Executor):
    def __init__(self):
        self._lock = threading.Lock()
        self._shutdown = False
        self._threads = set()
        self._futures = set()

    def submit(self, fn, /, *args, **kwargs):
        future = Future()
        with self._lock:
            if self._shutdown:
                raise RuntimeError("Cannot schedule new futures after shutdown")
            self._futures.add(future)

            def run():
                try:
                    if future.set_running_or_notify_cancel():
                        future.set_result(fn(*args, **kwargs))
                except BaseException as exc:
                    future.set_exception(exc)
                finally:
                    with self._lock:
                        self._threads.discard(threading.current_thread())
                        self._futures.discard(future)

            thread = threading.Thread(
                target=run,
                name="gmail-redmine-worker",
                daemon=True,
            )
            self._threads.add(thread)
            thread.start()
        return future

    def shutdown(self, wait=True, *, cancel_futures=False):
        with self._lock:
            self._shutdown = True
            threads = tuple(self._threads)
            futures = tuple(self._futures) if cancel_futures else ()
        for future in futures:
            future.cancel()
        if wait:
            for thread in threads:
                thread.join()


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
        self.token_usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "thinking_tokens": 0,
            "tool_input_tokens": 0,
            "total_tokens": 0,
        }
        self.items = {}
        self.futures = {}
        self.subscribers = set()
        self.worker_executor = _DaemonExecutor()
        self.worker_future = None
        self.log_handler = None

        self.store.mark_stale_pending_failed()
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

    def add_token_usage(self, usage):
        with self.lock:
            for key in self.token_usage:
                self.token_usage[key] += max(0, int(usage.get(key, 0) or 0))
            totals = dict(self.token_usage)
        self.publish("token_usage", totals)

    def token_usage_snapshot(self):
        with self.lock:
            return dict(self.token_usage)

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
        self.worker_executor.shutdown(wait=False, cancel_futures=True)
        if self.log_handler is not None:
            logging.getLogger("gmail_redmine").removeHandler(self.log_handler)
