"""HTTP retry utilities."""
import logging
import time
from typing import Optional
from urllib.parse import urlsplit

import requests


def safe_url_for_log(url: str) -> str:
    """Return only scheme+host for logging; drops path, query, fragment so tokens
    embedded anywhere in the URL never reach logs."""
    parts = urlsplit(url)
    host = parts.hostname or ''
    scheme = parts.scheme or 'http'
    return f"{scheme}://{host}" if host else '<url>'

logger = logging.getLogger(__name__)


def is_retryable_status(status_code: int) -> bool:
    """Check if an HTTP status code is retryable (transient)."""
    return status_code == 429 or status_code >= 500


def _request_with_retry(
    method: str,
    url: str,
    max_retries: int = 3,
    timeout: int = 300,
    log_prefix: str = "HTTP",
    **kwargs,
) -> Optional[requests.Response]:
    """HTTP request with exponential backoff retry on transient errors.

    Retries on 429, 5xx, Timeout, and ConnectionError.
    Returns None if all retries are exhausted.

    Args:
        method: HTTP method ("get" or "post").
        url: The URL to request.
        max_retries: Maximum number of attempts.
        timeout: Request timeout in seconds.
        log_prefix: Prefix for log messages.
        **kwargs: Passed through to requests.get()/post().

    Returns:
        The successful Response (2xx), or None on permanent failure.
    """
    request_fn = requests.get if method == "get" else requests.post
    response = None
    for attempt in range(max_retries):
        try:
            response = request_fn(url, timeout=timeout, **kwargs)

            if response.ok:
                return response

            if is_retryable_status(response.status_code) and attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                logger.warning(
                    f"{log_prefix} returned {response.status_code}, retrying in {wait}s"
                )
                time.sleep(wait)
                continue

            # Non-retryable error or last attempt
            body_len = len(response.text) if response.text else 0
            logger.error(f"{log_prefix} error {response.status_code} (body {body_len} chars)")
            return None

        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                logger.warning(f"{log_prefix} timeout (attempt {attempt + 1}/{max_retries}), retrying")
                continue
            logger.error(f"{log_prefix} timed out after {max_retries} attempts")
            return None

        except requests.exceptions.ConnectionError as e:
            if attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                logger.warning(
                    f"{log_prefix} connection failed (attempt {attempt + 1}/{max_retries}), "
                    f"retrying in {wait}s: {e}"
                )
                time.sleep(wait)
                continue
            logger.error(f"{log_prefix} connection failed after {max_retries} attempts: {e}")
            return None

    # All retries exhausted with retryable status
    if response is not None:
        logger.error(f"{log_prefix} failed after {max_retries} attempts: {response.status_code}")
    return None


def post_with_retry(
    url: str,
    max_retries: int = 3,
    timeout: int = 300,
    log_prefix: str = "HTTP",
    **kwargs,
) -> Optional[requests.Response]:
    """POST with exponential backoff retry. See _request_with_retry for details."""
    return _request_with_retry("post", url, max_retries=max_retries,
                               timeout=timeout, log_prefix=log_prefix, **kwargs)


def get_with_retry(
    url: str,
    max_retries: int = 3,
    timeout: int = 30,
    log_prefix: str = "HTTP",
    **kwargs,
) -> Optional[requests.Response]:
    """GET with exponential backoff retry. See _request_with_retry for details."""
    return _request_with_retry("get", url, max_retries=max_retries,
                               timeout=timeout, log_prefix=log_prefix, **kwargs)
