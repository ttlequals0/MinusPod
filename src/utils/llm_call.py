"""Shared LLM-call helper with retry, rate-limit, and auth-error handling."""
import logging
import random
import time
from typing import Union

import run_context
from llm_capabilities import supports_json_schema
from llm_client import (
    is_retryable_error,
    is_connectivity_error,
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
    MAX_RESET_SECONDS, MIN_HOLD_RESET_SECONDS, enforce_provider_rate_limit,
    is_rate_limit_hold_enabled, reserve_provider_request,
)
from utils.shutdown import shutdown_event
from utils.time import parse_iso_utc, utc_now
# webhook_service, database and cancel are lazy-imported at the call sites
# below (database pulls in Flask via AuthLockoutMixin; cancel pulls in
# database transitively). Keeping them out of this module's import-time
# graph lets the offline benchmark in benchmarks/llm/ import
# ad_detector -> utils.llm_call without pulling in jinja2/flask transitively.
from utils.llm_response import json_object_is_blank
from utils.retry import calculate_backoff
from utils.circuit_breaker import CircuitBreakerOpen

logger = logging.getLogger(__name__)

# Longest in-process wait: a reset past MIN_HOLD_RESET_SECONDS becomes a
# queue-level hold instead, so sleeping longer than that here skips the review
# that hold exists to trigger.
FALLBACK_RETRY_AFTER_CAP_SECONDS = float(MIN_HOLD_RESET_SECONDS)
# Slice that wait so a container stop is not sat out.
RETRY_SLEEP_SLICE_SECONDS = 5.0
# Headroom past the breaker's reported cooldown so jitter never lands a
# retry back inside the cooldown window.
BREAKER_RETRY_MARGIN_SECONDS = 1.0
# Longest a per-window retry will wait out a breaker cooldown; past this the
# outage is treated as ongoing and the window is given up on as before.
BREAKER_WAIT_CAP_SECONDS = 90.0


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
    "no ads" (issue #358). Carries the raw response when one was returned so
    the ledger can still record the tokens the provider billed for it.
    """

    def __init__(self, *args, response=None):
        super().__init__(*args)
        self.response = response


class ReasoningExhaustedError(EmptyCompletionError):
    """The response budget was exhausted by reasoning before an answer."""


class OutputTruncatedError(Exception):
    """The output budget ran out with no reasoning to blame: the answer itself
    was cut off. Terminal, not retryable, since the same budget and prompt
    truncate again."""

    def __init__(self, *args, response=None):
        super().__init__(*args)
        self.response = response


def _completion_is_empty(response) -> bool:
    """True when the model returned no usable content (empty or whitespace)."""
    content = getattr(response, 'content', None)
    return not (content or "").strip()


def _reasoning_present(response) -> bool:
    """True when the provider reported reasoning on this response."""
    if (getattr(response, 'reasoning_exhausted', False)
            or getattr(response, 'reasoning_present', False)):
        return True
    usage = getattr(response, 'usage', None)
    return bool(isinstance(usage, dict) and usage.get('reasoning_tokens'))


def _output_budget_spent(response, max_tokens) -> bool:
    """True when the provider cut the output off at the configured budget."""
    if getattr(response, 'finish_reason', None) in ('max_tokens', 'length'):
        return True
    usage = getattr(response, 'usage', None)
    output_tokens = usage.get('output_tokens') if isinstance(usage, dict) else None
    return (isinstance(max_tokens, int) and isinstance(output_tokens, (int, float))
            and output_tokens >= max_tokens)


def _call_once(llm_client, llm_kwargs, model, blank_json_is_failure=False):
    """One LLM call; raise EmptyCompletionError if it comes back content-less."""
    response = llm_client.messages_create(**llm_kwargs)
    budget_spent = _output_budget_spent(response, llm_kwargs.get('max_tokens'))
    if _completion_is_empty(response):
        if _reasoning_present(response) and (
                getattr(response, 'reasoning_exhausted', False) or budget_spent):
            raise ReasoningExhaustedError(
                f"empty completion from {model} after reasoning exhausted the output budget",
                response=response,
            )
        if budget_spent:
            raise OutputTruncatedError(
                f"empty completion from {model} cut off at its output budget",
                response=response,
            )
        raise EmptyCompletionError(
            f"empty completion from {model} (no content returned)", response=response)
    # A cut-off completion that names no answer ("{}") is a lost window: the
    # extractor would otherwise read the blank object as "no ads". Only window
    # calls opt in; a blank repair/chapters answer is not a coverage gap.
    if (blank_json_is_failure and budget_spent
            and json_object_is_blank(getattr(response, 'content', None))):
        if _reasoning_present(response):
            raise ReasoningExhaustedError(
                f"{model} spent its output budget on reasoning without producing an answer",
                response=response,
            )
        raise OutputTruncatedError(
            f"{model} spent its output budget without producing an answer",
            response=response,
        )
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


# Rough provider-agnostic prompt estimate for the token reservation; the
# attempt reconciles to real usage when it finalizes.
_CHARS_PER_TOKEN = 4


def _reserved_tokens(llm_kwargs) -> int:
    """Tokens to reserve for one dispatch: a prompt estimate plus the whole
    output budget, so a TPM cap sees a large call before it is billed."""
    chars = len(llm_kwargs.get('system') or '')
    for message in llm_kwargs.get('messages') or []:
        content = message.get('content')
        chars += len(content if isinstance(content, str) else str(content))
    return chars // _CHARS_PER_TOKEN + int(llm_kwargs.get('max_tokens') or 0)


def _ledger_call_once(llm_client, llm_kwargs, model, *, phase_key, invoking_pass,
                      provider_key, credential_slot, slug, episode_id, call_label,
                      blank_json_is_failure=False):
    """One ledger-tracked adapter dispatch.

    Reserves the request by creating its attempt row before the network call
    and finalizes it after, so every real dispatch (including each retry) is
    its own billable ledger row: the single writer of token counters,
    replacing the retired adapter usage callback. A manual cap refuses the
    reservation and raises instead of dispatching.
    """
    from cancel import ProcessingCancelled
    from database import Database
    db = Database()
    ctx = run_context.current()
    attempt_id, hold_until = reserve_provider_request(
        db, provider_key, credential_slot,
        attempt=dict(
            run_id=ctx.run_id if ctx else None,
            podcast_id=_resolve_podcast_id(slug),
            episode_id=episode_id,
            phase_key=phase_key,
            invoking_pass=invoking_pass,
            configured_model=model,
            window_label=call_label,
            reserved_tokens=_reserved_tokens(llm_kwargs),
        ),
    )
    if attempt_id is None:
        raise _reservation_refused(provider_key, credential_slot, hold_until,
                                   slug, episode_id, call_label, phase_key)

    run_context.begin_dispatch(attempt_id)
    try:
        response = _call_once(llm_client, llm_kwargs, model, blank_json_is_failure)
    except ProcessingCancelled:
        db.finalize_llm_attempt(attempt_id, state='cancelled')
        raise
    except Exception as e:
        # An empty/reasoning-exhausted completion still carries the usage the
        # provider billed; record it on the failed attempt instead of zero.
        _finalize_attempt(db, attempt_id, 'failure', getattr(e, 'response', None), ctx)
        raise
    finally:
        run_context.end_dispatch()

    _finalize_attempt(db, attempt_id, 'success', response, ctx)
    return response


def _reservation_refused(provider_key, credential_slot, hold_until, slug,
                         episode_id, call_label, phase_key):
    """The typed error for a request a manual cap refused to reserve."""
    reset_at = parse_iso_utc(hold_until) if hold_until else None
    retry_after = max(0.0, (reset_at - utc_now()).total_seconds()) if reset_at else 0.0
    logger.warning(
        f"[{slug}:{episode_id}] {call_label} refused by the {provider_key} "
        f"manual rate limit; holding queue {retry_after:.0f}s"
    )
    return ProviderRateLimitedError(
        f"manual rate limit for {provider_key} resets in {retry_after:.0f}s",
        retry_after_seconds=retry_after, provider_key=provider_key,
        credential_slot=credential_slot, manual=True, phase=phase_key)


def _finalize_attempt(db, attempt_id, state, response, ctx) -> None:
    """Finalize one ledger attempt with the response's usage (if any) and
    add the resulting cost to the run accumulator."""
    cost = db.finalize_llm_attempt_from_response(attempt_id, state, response)
    if ctx is None:
        return
    usage = getattr(response, 'usage', None)
    if not isinstance(usage, dict):
        usage = {}
    ctx.tokens.add(usage.get('input_tokens') or 0, usage.get('output_tokens') or 0, cost)


def _apply_reasoning_fallback(llm_kwargs, *, slug, episode_id, call_label) -> None:
    """Turn reasoning off before the retry an exhausted budget earns. An unset
    effort flips too: the model reasoned unasked, so 'none' is a different
    request; an effort already at 'none' retries the same request."""
    if llm_kwargs.get('reasoning_effort') in ('', 'none'):
        return
    llm_kwargs['reasoning_effort'] = 'none'
    logger.warning(
        f"[{slug}:{episode_id}] {call_label} reasoning exhausted the output budget; "
        "retrying with reasoning disabled"
    )


def _is_manual_cap_error(error) -> bool:
    """True for a refusal by a MinusPod-configured cap (not a provider 429)."""
    return isinstance(error, ProviderRateLimitedError) and getattr(error, 'manual', False)


def _is_retryable(error) -> bool:
    # A truncated answer is terminal: the same budget truncates again.
    if isinstance(error, OutputTruncatedError):
        return False
    if isinstance(error, CircuitBreakerOpen):
        return True
    return isinstance(error, EmptyCompletionError) or is_retryable_error(error)


# Loss classes recorded when a window is given up on: a 5xx and a 429 both
# leave the window unexamined, and the run stats used to report them alike.
LOSS_RATE_LIMIT = 'rate_limit'
LOSS_SERVER_ERROR = 'server_error'
LOSS_CONNECTIVITY = 'connectivity'
LOSS_REASONING_EXHAUSTED = 'reasoning_exhausted'
LOSS_OUTPUT_TRUNCATED = 'output_truncated'
LOSS_EMPTY_COMPLETION = 'empty_completion'
LOSS_OTHER = 'other'


def window_loss_class(error) -> str:
    """Name the status class a window was lost to."""
    if error is None:
        return LOSS_OTHER
    if isinstance(error, ReasoningExhaustedError):
        return LOSS_REASONING_EXHAUSTED
    if isinstance(error, OutputTruncatedError):
        return LOSS_OUTPUT_TRUNCATED
    if isinstance(error, EmptyCompletionError):
        return LOSS_EMPTY_COMPLETION
    if isinstance(error, (StructuralRateLimitError, ProviderRateLimitedError)):
        return LOSS_RATE_LIMIT
    if is_rate_limit_error(error):
        return LOSS_RATE_LIMIT
    status = getattr(error, 'status_code', None)
    if isinstance(status, int) and 500 <= status < 600:
        return LOSS_SERVER_ERROR
    if is_connectivity_error(error):
        return LOSS_CONNECTIVITY
    return LOSS_OTHER


def _lost_window(error, is_window, slug, episode_id, call_label):
    """Log a lost detection/review window and hand the error back. Other call
    sites (chapters, repairs) degrade on their own and are not coverage gaps."""
    if is_window:
        _log_window_loss(error, slug=slug, episode_id=episode_id,
                         call_label=call_label)
    return error


def _log_window_loss(error, *, slug, episode_id, call_label):
    """Log one window lost after every retry, named by status class."""
    status = getattr(error, 'status_code', None)
    detail = f" status={status}" if status else ""
    # The error text is on the preceding per-attempt line; this names the class.
    logger.warning(
        f"[{slug}:{episode_id}] {call_label} lost after all retries "
        f"({window_loss_class(error)}{detail})"
    )


def _shutdown_requested() -> bool:
    """True once the process has been asked to shut down."""
    return shutdown_event.is_set()


def _sleep_before_retry(delay: float) -> bool:
    """Wait `delay` in slices, ending early on shutdown; False when interrupted."""
    remaining = delay
    while remaining > 0:
        if _shutdown_requested():
            return False
        slice_seconds = min(RETRY_SLEEP_SLICE_SECONDS, remaining)
        time.sleep(slice_seconds)
        remaining -= slice_seconds
    return not _shutdown_requested()


def _fallback_delay(error, base_delay: float, honor_retry_after: bool) -> float:
    """Per-window retry wait: a rate limit's own reset beats the fixed backoff.

    Only the first retry honors the reset, capped, so a long hint cannot park
    a worker for the sum of both iterations.
    """
    if honor_retry_after and is_rate_limit_error(error):
        retry_after = extract_retry_after(
            error, max_seconds=FALLBACK_RETRY_AFTER_CAP_SECONDS)
        if retry_after is not None:
            return retry_after + random.uniform(0.0, 2.0)
    return base_delay


def _wait_past_breaker_cooldown(remaining_seconds: float) -> bool:
    """Sleep past a breaker cooldown plus margin; False without sleeping when
    that wait exceeds BREAKER_WAIT_CAP_SECONDS."""
    wait = remaining_seconds + BREAKER_RETRY_MARGIN_SECONDS
    if wait > BREAKER_WAIT_CAP_SECONDS:
        logger.warning(
            f"Breaker cooldown wait {wait:.1f}s exceeds the "
            f"{BREAKER_WAIT_CAP_SECONDS:.0f}s cap; giving up"
        )
        return False
    return _sleep_before_retry(wait)


def _breaker_retry_delay(llm_client, error, base_delay: float) -> float:
    """Wait past the active breaker cooldown without shortening backoff."""
    remaining = (error.seconds_until_retry
                 if isinstance(error, CircuitBreakerOpen) else None)
    if remaining is None:
        retry_after = getattr(llm_client, 'circuit_retry_after', None)
        remaining = retry_after() if callable(retry_after) else None
    if remaining is None:
        return base_delay
    return (max(base_delay, float(remaining) + BREAKER_RETRY_MARGIN_SECONDS)
            + random.uniform(0.0, 0.25))


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
                    credential_slot='primary', phase=None):
    """Return a terminal or normalized provider error, else None.

    ``provider``, when given, is the call's resolved route provider; omitted,
    error context falls back to the global effective provider.
    ``credential_slot`` is that route's account ('primary'/'secondary'), so a
    held 429 pauses only the account that actually hit the limit. ``phase``
    labels the call so a hold can fall back to the run's route for it.
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
                credential_slot=credential_slot, phase=phase)
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


def _manual_rate_limit_error(provider_key, credential_slot, slug, episode_id,
                             phase=None):
    """ProviderRateLimitedError when the manual RPM/RPD cap is hit, else None.

    Records the provider+slot hold as a side effect (via
    enforce_provider_rate_limit) so admission and the probe tick see it too.
    """
    try:
        from database import Database
        reset_iso = enforce_provider_rate_limit(
            Database(), provider_key, credential_slot,
            slug=slug, episode_id=episode_id)
    except Exception:
        logger.exception("Manual rate-limit check failed; allowing the call")
        return None
    if reset_iso is None:
        return None
    reset_at = parse_iso_utc(reset_iso)
    retry_after = max(0.0, (reset_at - utc_now()).total_seconds()) if reset_at else 0.0
    logger.warning(
        f"[{slug}:{episode_id}] {provider_key} manual rate limit reached; "
        f"holding queue {retry_after:.0f}s until {reset_iso}"
    )
    return ProviderRateLimitedError(
        f"manual rate limit for {provider_key} resets in {retry_after:.0f}s",
        retry_after_seconds=retry_after, provider_key=provider_key,
        credential_slot=credential_slot, manual=True, phase=phase)


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
    blank_json_is_failure: bool = False,
    is_window: bool = False,
) -> tuple[object | None, Exception | None]:
    """Call LLM with an in-loop retry then a per-window fallback retry.

    Both retry loops stay on the same route/slot; this is not a cross-provider
    failover chain (see ``provider``/``credential_slot`` below).

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

    ``blank_json_is_failure`` treats a budget-truncated blank JSON object as a
    failed call; only window calls, where it means an unexamined span, opt in.

    ``is_window`` marks a detection/review window, the only calls whose loss
    is a coverage gap worth its own log line.

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
    reasoning_retried = False

    def dispatch():
        """One dispatch, plus the single retry an exhausted reasoning budget
        earns wherever in the ladder it lands. A ReasoningExhaustedError out of
        here has already spent that retry and is terminal for the window."""
        nonlocal reasoning_retried
        call = dict(
            phase_key=phase_key, invoking_pass=invoking_pass,
            provider_key=provider_key, credential_slot=credential_slot,
            slug=slug, episode_id=episode_id, call_label=call_label,
            blank_json_is_failure=blank_json_is_failure)
        try:
            return _ledger_call_once(llm_client, llm_kwargs, model, **call)
        except ReasoningExhaustedError:
            if reasoning_retried:
                logger.warning(
                    f"[{slug}:{episode_id}] {call_label} exhausted its output "
                    f"budget again; giving up"
                )
                raise
            reasoning_retried = True
            _apply_reasoning_fallback(llm_kwargs, slug=slug, episode_id=episode_id,
                                      call_label=call_label)
        held = _manual_rate_limit_error(provider_key, credential_slot, slug,
                                        episode_id, phase=phase_key)
        if held is not None:
            raise held
        return _ledger_call_once(llm_client, llm_kwargs, model, **call)

    for attempt in range(max_retries + 1):
        # Manual rate-limit backstop (#747): re-checked before every dispatch,
        # not once up front, so a cap crossed mid-retry defers instead of
        # burning more requests. Admission is still the primary gate.
        held = _manual_rate_limit_error(provider_key, credential_slot, slug,
                                        episode_id, phase=phase_key)
        if held is not None:
            return None, _lost_window(held, is_window, slug, episode_id, call_label)
        try:
            response = dispatch()
            return response, None
        except Exception as e:
            last_error = e
            if isinstance(e, ReasoningExhaustedError):
                break
            # A cap that refused the reservation already recorded its hold;
            # re-classifying it would only retry into the same refusal.
            if _is_manual_cap_error(e):
                return None, _lost_window(e, is_window, slug, episode_id, call_label)
            terminal = _terminal_error(
                e, model=model, slug=slug, episode_id=episode_id,
                call_label=call_label, provider=provider,
                credential_slot=credential_slot, phase=phase_key)
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
                    delay = _breaker_retry_delay(llm_client, e, delay)
                    logger.warning(
                        f"[{slug}:{episode_id}] {call_label} API error: {e}. "
                        f"Retrying in {delay:.1f}s"
                    )
                if not _sleep_before_retry(delay):
                    break
                continue
            logger.warning(f"[{slug}:{episode_id}] {call_label} failed: {e}")
            break

    if (response is None and last_error is not None and _is_retryable(last_error)
            and not isinstance(last_error, ReasoningExhaustedError)):
        # A CircuitBreakerOpen on the last fixed rung earns one more rung once
        # its cooldown clears, appended here rather than pre-planned: no other
        # error qualifies for it.
        rungs = [2, 5]
        retry_num = 0
        while retry_num < len(rungs):
            retry_num += 1
            held = _manual_rate_limit_error(provider_key, credential_slot, slug,
                                            episode_id, phase=phase_key)
            if held is not None:
                return None, _lost_window(held, is_window, slug, episode_id, call_label)
            rung = rungs[retry_num - 1]
            if rung == 'breaker':
                if not _wait_past_breaker_cooldown(last_error.seconds_until_retry):
                    break
                wait = last_error.seconds_until_retry + BREAKER_RETRY_MARGIN_SECONDS
                logger.warning(
                    f"[{slug}:{episode_id}] {call_label} per-window retry "
                    f"{retry_num}/{len(rungs)} after breaker cooldown ({wait:.1f}s)"
                )
            else:
                delay = _fallback_delay(last_error, rung, retry_num == 1)
                delay = _breaker_retry_delay(llm_client, last_error, delay)
                logger.warning(
                    f"[{slug}:{episode_id}] {call_label} per-window retry "
                    f"{retry_num}/{len(rungs)} after {delay:.1f}s backoff"
                )
                if not _sleep_before_retry(delay):
                    break
            try:
                response = dispatch()
                logger.info(
                    f"[{slug}:{episode_id}] {call_label} succeeded on retry {retry_num}"
                )
                return response, None
            except Exception as e:
                last_error = e
                if isinstance(e, ReasoningExhaustedError):
                    break
                if _is_manual_cap_error(e):
                    return None, _lost_window(e, is_window, slug, episode_id,
                                              call_label)
                terminal = _terminal_error(
                    e, model=model, slug=slug, episode_id=episode_id,
                    call_label=call_label, provider=provider,
                    credential_slot=credential_slot, phase=phase_key)
                if terminal is not None:
                    last_error = terminal
                    break
                if not _is_retryable(e):
                    break
                logger.warning(
                    f"[{slug}:{episode_id}] {call_label} retry {retry_num} failed: {e}"
                )
                if (retry_num == len(rungs) and rungs[-1] != 'breaker'
                        and isinstance(last_error, CircuitBreakerOpen)):
                    rungs = rungs + ['breaker']

    return None, _lost_window(last_error, is_window, slug, episode_id, call_label)


def call_llm_for_window(
    *, window_label: str, response_format: dict | None = None, **kwargs
) -> tuple[object | None, Exception | None]:
    """Detection-window flavor of ``call_llm``: JSON response format.

    ``response_format`` defaults to json_object; call sites with a defined
    response schema (#694) pass a json_schema dict when the capability gate
    passes. A blank JSON answer at the output budget counts as a lost window.
    """
    return call_llm(
        call_label=window_label,
        response_format=response_format or {"type": "json_object"},
        blank_json_is_failure=True,
        is_window=True,
        **kwargs,
    )
