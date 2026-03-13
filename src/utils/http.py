"""HTTP retry utilities."""
import logging
import time
from typing import Callable, Optional

import requests

logger = logging.getLogger(__name__)


def is_retryable_status(status_code: int) -> bool:
    """Check if an HTTP status code is retryable (transient)."""
    return status_code == 429 or status_code >= 500


def post_with_retry(
    url: str,
    max_retries: int = 3,
    timeout: int = 300,
    log_prefix: str = "HTTP",
    **kwargs,
) -> Optional[requests.Response]:
    """POST with exponential backoff retry on transient errors.

    Retries on 429, 5xx, Timeout, and ConnectionError.
    Returns None if all retries are exhausted.

    Args:
        url: The URL to POST to.
        max_retries: Maximum number of attempts.
        timeout: Request timeout in seconds.
        log_prefix: Prefix for log messages.
        **kwargs: Passed through to requests.post().

    Returns:
        The successful Response, or None on permanent failure.
    """
    response = None
    for attempt in range(max_retries):
        try:
            response = requests.post(url, timeout=timeout, **kwargs)

            if response.status_code == 200:
                return response

            if is_retryable_status(response.status_code) and attempt < max_retries - 1:
                wait = 2 ** (attempt + 1)
                logger.warning(
                    f"{log_prefix} returned {response.status_code}, retrying in {wait}s"
                )
                time.sleep(wait)
                continue

            # Non-retryable error or last attempt
            logger.error(f"{log_prefix} error {response.status_code}: {response.text[:500]}")
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
