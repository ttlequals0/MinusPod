"""Shared LLM-call helper with retry, rate-limit, and auth-error handling."""
import logging
import random
import time
from typing import Union

import run_context
from llm_capabilities import supports_json_schema
from llm_client import (
    is_retryable_error,
    is_rate_limit_error,
    classify_structural_rate_limit,
    classify_daily_quota_exhaustion,
    is_auth_error,
    is_limit_exceeded_error,
    extract_retry_after,
    get_effective_provider,
    StructuralRateLimitError,
    ProviderRateLimitedError,
    supports_json_schema_for_calls,
)
from rate_limit_hold import (
    MAX_RESET_SECONDS, MIN_HOLD_RESET_SECONDS, is_rate_limit_hold_enabled,
)
# webhook_service, database and cancel are lazy-imported at the call sites
# below (database pulls in Flask via AuthLockoutMixin; cancel pulls in
# database transitively). Keeping them out of this module's import-time
# graph lets the offline benchmark in benchmarks/llm/ import
# ad_detector -> utils.llm_call without pulling in jinja2/flask transitively.
from utils.retry import calculate_backoff

logger = logging.getLogger(__name__)


def json_schema_format(name: str, schema: dict, description: str | None = None) -> dict:
    """response_format payload for schema-enforced structured output (#694)."""
    payload = {"name": name, "schema": schema}
    if description:
        payload["description"] = description
    return {"type": "json_schema", "json_schema": payload}


def schema_format_for(model, name: str, schema: dict,
                      description: str | None = None,
                      allow_provider_schema: bool = False,
                      provider: str | None = None) -> dict:
    """json_schema response_format when `model` supports it, else json_object.

    ``allow_provider_schema`` additionally accepts a provider with a proven
    schema path (Anthropic). Only for call sites that send no reasoning
    budget: see supports_json_schema_for_calls for why the two gates differ.

    ``provider``, when given, is the resolved route's provider for this
    call; omitted, this falls back to the global effective provider.
    """
    if supports_json_schema_for_calls(model) or (
            allow_provider_schema
            and supports_json_schema(provider or get_effective_provider())):
        return json_schema_format(name, schema, description)
    return {"type": "json_object"}


class EmptyCompletionError(Exception):
    """The provider returned a completion with no content.

    Distinct from a valid empty-ad-list (``[]``) response: an empty body means
    the call never produced an answer (truncation, refusal, or a flaky
    endpoint). Treated as a retryable failure so it is retried and, if it
    persists, surfaced as a failed window rather than silently recorded as
    "no ads" (issue #358).
    """


class ReasoningExhaustedError(EmptyCompletionError):
    """The response budget was exhausted by reasoning before an answer."""


def _completion_is_empty(response) -> bool:
    """True when the model returned no usable content (empty or whitespace)."""
    content = getattr(response, 'content', None)
    return not (content or "").strip()


def _call_once(llm_client, llm_kwargs, model):
    """One LLM call; raise EmptyCompletionError if it comes back content-less."""
    response = llm_client.messages_create(**llm_kwargs)
    if _completion_is_empty(response):
        if (getattr(response, 'reasoning_exhausted', False)
                or (getattr(response, 'reasoning_present', False)
                    and getattr(response, 'finish_reason', None) in ('max_tokens', 'length'))):
            raise ReasoningExhaustedError(
                f"empty completion from {model} after reasoning exhausted the output budget"
            )
        raise EmptyCompletionError(f"empty completion from {model} (no content returned)")
    return response


def _resolve_podcast_id(slug: str | None) -> int | None:
    """Podcast row id for a slug, or None (chapters calls may have no slug)."""
    if not slug:
        return None
    try:
        from database import Database
        podcast = Database().get_podcast_by_slug(slug)
    except Exception:
        logger.warning(f"Could not resolve podcast_id for slug '{slug}'")
        return None
    return podcast['id'] if podcast else None


def _invoking_pass_from_name(pass_name: str | None) -> int | None:
    """1 or 2 from a '..._pass_1' / '..._pass_2' pass_name, else None."""
    if pass_name and pass_name.endswith('_1'):
        return 1
    if pass_name and pass_name.endswith('_2'):
        return 2
    return None


def _ledger_call_once(llm_client, llm_kwargs, model, *, phase_key, invoking_pass,
                      provider_key, slug, episode_id, call_label):
    """One ledger-tracked adapter dispatch.

    Begins an attempt before the network call and finalizes it after, so
    every real dispatch (including each retry) is its own billable ledger
    row: the single writer of token counters, replacing the retired
    adapter usage callback.
    """
    from cancel import ProcessingCancelled
    from database import Database
    db = Database()
    ctx = run_context.current()
    attempt_id = db.begin_llm_attempt(
        run_id=ctx.run_id if ctx else None,
        podcast_id=_resolve_podcast_id(slug),
        episode_id=episode_id,
        phase_key=phase_key,
        invoking_pass=invoking_pass,
        provider_key=provider_key,
        configured_model=model,
        window_label=call_label,
    )
    try:
        response = _call_once(llm_client, llm_kwargs, model)
    except ProcessingCancelled:
        db.finalize_llm_attempt(attempt_id, state='cancelled')
        raise
    except Exception:
        cost = db.finalize_llm_attempt(attempt_id, state='failure')
        if ctx is not None:
            ctx.tokens.add(0, 0, cost)
        raise

    usage = getattr(response, 'usage', None)
    if not isinstance(usage, dict):
        usage = {}
    returned_model = getattr(response, 'returned_model', None)
    if not isinstance(returned_model, str):
        returned_model = None
    cost = db.finalize_llm_attempt(
        attempt_id, state='success', returned_model=returned_model,
        input_tokens=usage.get('input_tokens'), output_tokens=usage.get('output_tokens'),
        cache_read_tokens=usage.get('cache_read_tokens'),
        cache_write_tokens=usage.get('cache_write_tokens'),
        reasoning_tokens=usage.get('reasoning_tokens'),
    )
    if ctx is not None:
        ctx.tokens.add(usage.get('input_tokens') or 0, usage.get('output_tokens') or 0, cost)
    return response


def _apply_reasoning_fallback(error, llm_kwargs, *, slug, episode_id, call_label):
    """Disable reasoning after a truncated reasoning-only response."""
    if (not isinstance(error, ReasoningExhaustedError)
            or llm_kwargs.get('reasoning_effort') == 'none'):
        return
    llm_kwargs['reasoning_effort'] = 'none'
    logger.warning(
        f"[{slug}:{episode_id}] {call_label} reasoning exhausted the output budget; "
        "retrying with reasoning disabled"
    )


def _is_retryable(error) -> bool:
    return isinstance(error, EmptyCompletionError) or is_retryable_error(error)


def _fire_limit_exceeded_webhook(error, model, provider=None):
    try:
        from webhook_service import fire_limit_exceeded_event
        fire_limit_exceeded_event(
            provider or get_effective_provider(), model, str(error),
            getattr(error, 'status_code', None),
        )
    except Exception:
        logger.exception("Failed to fire limit-exceeded webhook")


def _fire_auth_failure_webhook(error, model, provider=None):
    try:
        from webhook_service import fire_auth_failure_event
        fire_auth_failure_event(
            provider or get_effective_provider(), model, str(error),
            getattr(error, 'status_code', None),
        )
    except Exception:
        logger.exception("Failed to fire auth-failure webhook")


def _terminal_error(error, *, model, slug, episode_id, call_label, provider=None,
                    credential_slot='primary'):
    """Return a terminal or normalized provider error, else None.

    ``provider``, when given, is the call's resolved route provider; omitted,
    error context falls back to the global effective provider.
    ``credential_slot`` is that route's account ('primary'/'secondary'), so a
    held 429 pauses only the account that actually hit the limit.
    """
    provider = provider or get_effective_provider()
    daily_quota = classify_daily_quota_exhaustion(error)
    if daily_quota is not None:
        limit = daily_quota.get('limit')
        actionable = (
            f"{provider} free-tier daily quota"
            + (f" (limit {limit})" if limit else "")
            + " exhausted; retry tomorrow, raise the tier, or switch provider."
        )
        logger.warning(
            f"[{slug}:{episode_id}] {call_label} daily quota exhausted: {actionable}"
        )
        return StructuralRateLimitError(actionable)

    structural = classify_structural_rate_limit(error)
    if structural is not None:
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
            f"[{slug}:{episode_id}] {call_label} structural rate limit: {actionable}"
        )
        try:
            from webhook_service import fire_structural_rate_limit_event
            fire_structural_rate_limit_event(
                provider, model, limit, used, requested, str(error),
            )
        except Exception:
            logger.exception("Failed to fire structural rate-limit webhook")
        return StructuralRateLimitError(actionable)

    if is_limit_exceeded_error(error):
        logger.warning(
            f"[{slug}:{episode_id}] {call_label} provider limit exceeded: {error}"
        )
        _fire_limit_exceeded_webhook(error, model, provider)
        return error

    if is_rate_limit_error(error) and is_rate_limit_hold_enabled():
        hold_after = extract_retry_after(error, max_seconds=MAX_RESET_SECONDS)
        if hold_after is not None and hold_after > MIN_HOLD_RESET_SECONDS:
            held = ProviderRateLimitedError(
                f"provider rate limit resets in {hold_after:.0f}s: {error}",
                retry_after_seconds=hold_after, provider_key=provider,
                credential_slot=credential_slot)
            logger.warning(
                f"[{slug}:{episode_id}] {call_label} rate limit: "
                f"holding queue {hold_after:.0f}s until provider reset"
            )
            return held

    if _is_retryable(error):
        return None

    logger.warning(f"[{slug}:{episode_id}] {call_label} failed: {error}")
    if is_auth_error(error):
        _fire_auth_failure_webhook(error, model, provider)
    return error


def call_llm(
    *,
    llm_client,
    model: str,
    system_prompt: str,
    prompt: str,
    llm_timeout: float,
    max_retries: int,
    max_tokens: int,
    slug: str | None,
    episode_id: str | None,
    call_label: str,
    phase_key: str,
    temperature: float = 0.0,
    reasoning_effort: Union[int, str] | None = None,
    pass_name: str | None = None,
    response_format: dict | None = None,
    provider: str | None = None,
    credential_slot: str = 'primary',
) -> tuple[object | None, Exception | None]:
    """Call LLM with primary retry + secondary fallback retry.

    Generic seam shared by ad detection/review (via ``call_llm_for_window``)
    and chapters generation. Never raises: all failures come back as the
    second tuple element so callers can degrade gracefully.

    ``provider``, when given, is the resolved route's provider for this
    call; error/webhook context uses it instead of the global effective
    provider. ``credential_slot`` is that route's account ('primary' or
    'secondary'), carried onto a held 429 so the queue pauses only that
    account, not every account on the same provider type.

    ``phase_key`` labels this call in the llm_call_usage ledger ('detection',
    'verification', 'review', 'chapters'); every real dispatch (including
    each retry below) is recorded as its own billable ledger row.

    Returns:
        Tuple of (response, last_error). response is None if all retries failed.
    """
    provider_key = provider or get_effective_provider()
    invoking_pass = _invoking_pass_from_name(pass_name)
    llm_kwargs = dict(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system_prompt,
        messages=[{"role": "user", "content": prompt}],
        timeout=llm_timeout,
        response_format=response_format,
        reasoning_effort=reasoning_effort,
        episode_id=episode_id,
        pass_name=pass_name,
    )
    response = None
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            response = _ledger_call_once(
                llm_client, llm_kwargs, model, phase_key=phase_key,
                invoking_pass=invoking_pass, provider_key=provider_key,
                slug=slug, episode_id=episode_id, call_label=call_label)
            return response, None
        except Exception as e:
            last_error = e
            _apply_reasoning_fallback(
                e, llm_kwargs, slug=slug, episode_id=episode_id,
                call_label=call_label)
            terminal = _terminal_error(
                e, model=model, slug=slug, episode_id=episode_id,
                call_label=call_label, provider=provider,
                credential_slot=credential_slot)
            if terminal is not None:
                last_error = terminal
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
                        f"[{slug}:{episode_id}] {call_label} rate limit ({source}), "
                        f"waiting {delay:.1f}s"
                    )
                else:
                    delay = calculate_backoff(attempt)
                    logger.warning(
                        f"[{slug}:{episode_id}] {call_label} API error: {e}. "
                        f"Retrying in {delay:.1f}s"
                    )
                time.sleep(delay)
                continue
            logger.warning(f"[{slug}:{episode_id}] {call_label} failed: {e}")
            break

    if response is None and last_error is not None and _is_retryable(last_error):
        for retry_num, delay in enumerate([2, 5], 1):
            logger.warning(
                f"[{slug}:{episode_id}] {call_label} per-window retry "
                f"{retry_num}/2 after {delay}s backoff"
            )
            time.sleep(delay)
            try:
                response = _ledger_call_once(
                    llm_client, llm_kwargs, model, phase_key=phase_key,
                    invoking_pass=invoking_pass, provider_key=provider_key,
                    slug=slug, episode_id=episode_id, call_label=call_label)
                logger.info(
                    f"[{slug}:{episode_id}] {call_label} succeeded on retry {retry_num}"
                )
                return response, None
            except Exception as e:
                last_error = e
                if retry_num < 2:
                    _apply_reasoning_fallback(
                        e, llm_kwargs, slug=slug, episode_id=episode_id,
                        call_label=call_label)
                terminal = _terminal_error(
                    e, model=model, slug=slug, episode_id=episode_id,
                    call_label=call_label, provider=provider,
                    credential_slot=credential_slot)
                if terminal is not None:
                    last_error = terminal
                    break
                logger.warning(
                    f"[{slug}:{episode_id}] {call_label} retry {retry_num} failed: {e}"
                )

    return None, last_error


def call_llm_for_window(
    *, window_label: str, response_format: dict | None = None, **kwargs
) -> tuple[object | None, Exception | None]:
    """Detection-window flavor of ``call_llm``: JSON response format.

    ``response_format`` defaults to json_object; call sites with a defined
    response schema (#694) pass a json_schema dict when the capability gate
    passes.
    """
    return call_llm(
        call_label=window_label,
        response_format=response_format or {"type": "json_object"},
        **kwargs,
    )
