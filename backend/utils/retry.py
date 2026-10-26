"""
Retry and backoff handling for external service calls.

Uses tenacity for exponential backoff with jitter.
"""
from functools import wraps
from typing import Callable, Optional

import requests
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from backend.config.settings import RETRY_MAX_ATTEMPTS
from backend.utils.logger import get_logger

logger = get_logger("retry")


class RedmineUploadError(Exception):
    """Custom error for Redmine upload failures."""
    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"Redmine upload failed: HTTP {status} - {body}")


def is_retryable_error(exception: Exception) -> bool:
    """
    Determine if an exception should trigger a retry.
    """
    # Connection errors and timeouts
    if isinstance(exception, (requests.ConnectionError, requests.Timeout)):
        return True

    # HTTP 5xx errors
    if isinstance(exception, requests.HTTPError):
        if hasattr(exception, 'response') and exception.response is not None:
            status = exception.response.status_code
            # Retry on 5xx and 429
            if status >= 500 or status == 429:
                return True

    return False


def should_not_retry(exception: Exception) -> bool:
    """
    Determine if an exception should NOT be retried.
    """
    if isinstance(exception, requests.HTTPError):
        if hasattr(exception, 'response') and exception.response is not None:
            status = exception.response.status_code
            # Never retry 4xx errors (except 429 which is handled above)
            if 400 <= status < 500 and status != 429:
                return True

    return False


def get_retry_after(exception: Exception) -> Optional[float]:
    """
    Extract Retry-After header from exception if present.
    Returns delay in seconds, or None.
    """
    if isinstance(exception, requests.HTTPError):
        if hasattr(exception, 'response') and exception.response is not None:
            retry_after = exception.response.headers.get('Retry-After')
            if retry_after:
                try:
                    return float(retry_after)
                except ValueError:
                    return None
    return None


def with_retry(service_name: str):
    """
    Decorator factory for adding retry logic to service calls.

    Args:
        service_name: Name of the service (for logging)
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            @retry(
                stop=stop_after_attempt(RETRY_MAX_ATTEMPTS),
                wait=wait_exponential(multiplier=1, min=1, max=30),
                retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
                before_sleep=before_sleep_log(logger, logger.level),
                reraise=True,
            )
            def retried_func():
                return func(*args, **kwargs)

            try:
                return retried_func()
            except requests.HTTPError as e:
                if should_not_retry(e):
                    logger.error(f"{service_name}: Non-retryable error {e.response.status_code}: {e.response.text[:200]}")
                    raise
                # Let the retry decorator handle 5xx and 429
                raise
            except Exception as e:
                logger.error(f"{service_name}: Unexpected error: {e}")
                raise

        return wrapper
    return decorator


class IdempotentRetry:
    """
    Helper for idempotent operations with deduplication.
    Used for create_issue to handle timeouts.
    """

    def __init__(self, redmine_client):
        self.redmine_client = redmine_client

    def find_issue_by_message_id(self, message_id: str) -> Optional[int]:
        """
        Search for an existing issue by Gmail message ID marker.
        Returns issue ID if found, None otherwise.
        """
        try:
            return self.redmine_client.find_issue_by_message_id(message_id)
        except Exception as e:
            logger.error(f"Failed to search for issue by message ID: {e}")
            raise
