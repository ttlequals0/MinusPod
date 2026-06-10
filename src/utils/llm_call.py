"""Shared LLM-call helper with retry, rate-limit, and auth-error handling."""
import logging
import random
import time
from typing import Optional, Tuple, Union

from llm_client import (
    is_retryable_error,
    is_rate_limit_error,
    classify_structural_rate_limit,
    is_auth_error,
    extract_retry_after,
    get_effective_provider,
    StructuralRateLimitError,
)
# webhook_service is lazy-imported at the call site below (only entered on
# is_auth_error or structural-429). Keeping it out of this module's import-time
# graph lets the offline benchmark in benchmarks/llm/ import ad_detector ->
# utils.llm_call without pulling in jinja2/flask transitively.
from utils.retry import calculate_backoff

logger = logging.getLogger(__name__)


class EmptyCompletionError(Exception):
    """The provider returned a completion with no content.

    Distinct from a valid empty-ad-list (``[]``) response: an empty body means
    the call never produced an answer (truncation, refusal, or a flaky
    endpoint). Treated as a retryable failure so it is retried and, if it
    persists, surfaced as a failed window rather than silently recorded as
    "no ads" (issue #358).
    """


def _completion_is_empty(response) -> bool:
    """True when the model returned no usable content (empty or whitespace)."""
    content = getattr(response, 'content', None)
    return not (content or "").strip()


def _call_once(llm_client, llm_kwargs, model):
    """One LLM call; raise EmptyCompletionError if it comes back content-less."""
    response = llm_client.messages_create(**llm_kwargs)
    if _completion_is_empty(response):
        raise EmptyCompletionError(f"empty completion from {model} (no content returned)")
    return response


def _is_retryable(error) -> bool:
    return isinstance(error, EmptyCompletionError) or is_retryable_error(error)


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
    reasoning_effort: Optional[Union[int, str]] = None,
    pass_name: Optional[str] = None,
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
        reasoning_effort=reasoning_effort,
        episode_id=episode_id,
        pass_name=pass_name,
    )
    response = None
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            response = _call_once(llm_client, llm_kwargs, model)
            return response, None
        except Exception as e:
            last_error = e
            structural = classify_structural_rate_limit(e)
            if structural is not None:
                provider = get_effective_provider()
                limit = structural.get('limit')
                used = structural.get('used')
                requested = structural.get('requested')
                actionable = (
                    f"{provider} rate limit: one detection window's token request "
                    f"(~{requested}) exceeds the per-minute cap ({limit}). "
                    f"Reduce the detection window size in Settings > LLM Tunables, "
                    f"or change provider/tier."
                )
                logger.warning(
                    f"[{slug}:{episode_id}] {window_label} structural rate limit: {actionable}"
                )
                # StructuralRateLimitError is excluded from is_retryable_error so
                # the post-loop secondary retry path skips it.
                last_error = StructuralRateLimitError(actionable)
                try:
                    from webhook_service import fire_structural_rate_limit_event
                    fire_structural_rate_limit_event(
                        provider, model, limit, used, requested, str(e),
                    )
                except Exception:
                    logger.exception("Failed to fire structural rate-limit webhook")
                break
            if _is_retryable(e) and attempt < max_retries:
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
                    from webhook_service import fire_auth_failure_event
                    provider = get_effective_provider()
                    fire_auth_failure_event(
                        provider, model, str(e),
                        getattr(e, 'status_code', None),
                    )
                break

    if response is None and last_error is not None and _is_retryable(last_error):
        for retry_num, delay in enumerate([2, 5], 1):
            logger.warning(
                f"[{slug}:{episode_id}] {window_label} per-window retry "
                f"{retry_num}/2 after {delay}s backoff"
            )
            time.sleep(delay)
            try:
                response = _call_once(llm_client, llm_kwargs, model)
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
