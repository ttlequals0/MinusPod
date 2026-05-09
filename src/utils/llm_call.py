"""Shared LLM-call helper with retry, rate-limit, and auth-error handling."""
import logging
import random
import time
from typing import Optional, Tuple

from llm_client import (
    is_retryable_error,
    is_rate_limit_error,
    is_auth_error,
    extract_retry_after,
    get_effective_provider,
)
from webhook_service import fire_auth_failure_event
from utils.retry import calculate_backoff

logger = logging.getLogger(__name__)


def call_llm_for_window(
    *,
    llm_client,
    model: str,
    system_prompt: str,
    prompt: str,
    llm_timeout: float,
    max_retries: int,
    max_tokens: int,
    slug: Optional[str],
    episode_id: Optional[str],
    window_label: str,
    temperature: float = 0.0,
) -> Tuple[Optional[object], Optional[Exception]]:
    """Call LLM with primary retry + per-window fallback retry.

    Returns:
        Tuple of (response, last_error). response is None if all retries failed.
    """
    llm_kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system_prompt,
        messages=[{"role": "user", "content": prompt}],
        timeout=llm_timeout,
        response_format={"type": "json_object"},
    )
    response = None
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            response = llm_client.messages_create(**llm_kwargs)
            return response, None
        except Exception as e:
            last_error = e
            if is_retryable_error(e) and attempt < max_retries:
                if is_rate_limit_error(e):
                    retry_after = extract_retry_after(e)
                    if retry_after is not None:
                        delay = retry_after + random.uniform(0.0, 2.0)
                        source = f"retry-after={retry_after:.1f}s"
                    else:
                        delay = calculate_backoff(attempt, base_delay=30.0, max_delay=120.0)
                        source = "backoff"
                    logger.warning(
                        f"[{slug}:{episode_id}] {window_label} rate limit ({source}), "
                        f"waiting {delay:.1f}s"
                    )
                else:
                    delay = calculate_backoff(attempt)
                    logger.warning(
                        f"[{slug}:{episode_id}] {window_label} API error: {e}. "
                        f"Retrying in {delay:.1f}s"
                    )
                time.sleep(delay)
                continue
            else:
                logger.warning(f"[{slug}:{episode_id}] {window_label} failed: {e}")
                if is_auth_error(e):
                    provider = get_effective_provider()
                    fire_auth_failure_event(
                        provider, model, str(e),
                        getattr(e, 'status_code', None),
                    )
                break

    if response is None and last_error is not None and is_retryable_error(last_error):
        for retry_num, delay in enumerate([2, 5], 1):
            logger.warning(
                f"[{slug}:{episode_id}] {window_label} per-window retry "
                f"{retry_num}/2 after {delay}s backoff"
            )
            time.sleep(delay)
            try:
                response = llm_client.messages_create(**llm_kwargs)
                logger.info(
                    f"[{slug}:{episode_id}] {window_label} succeeded on retry {retry_num}"
                )
                return response, None
            except Exception as e:
                last_error = e
                logger.warning(
                    f"[{slug}:{episode_id}] {window_label} retry {retry_num} failed: {e}"
                )

    return None, last_error
