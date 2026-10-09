"""Account for System One's actual outbound requests."""
import json
import math
import time
from dataclasses import dataclass
from contextlib import nullcontext

import httpx
import run_context
from openai import (
    APIConnectionError, APIStatusError, APITimeoutError, AuthenticationError,
    InternalServerError, NotFoundError, PermissionDeniedError, RateLimitError,
    UnprocessableEntityError,
)
from systemone.protocol import JevReviewValidationError
from provider_budget import request_token_estimate


@dataclass
class DispatchResponse:
    usage: dict
    returned_model: str | None = None
    provider_reported_cost_usd: float | None = None
    headers: object | None = None
    body: object | None = None
    text: str | None = None
    status_code: int | None = None
    dispatch_latency_ms: int | None = None
    actual_dispatch_count: int = 1


class SystemOneTransport:
    def __init__(self, *, url, api_key, model, timeout, http_client=None):
        self.url = url
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.http_client = http_client or httpx.Client(timeout=timeout)
        self._owns_client = http_client is None

    def __call__(self, payload, *, request_kind, window_label=None,
                 deadline_at=None, validate=None):
        return dispatch_systemone_request(
            payload, url=self.url, api_key=self.api_key, model=self.model,
            timeout=self.timeout, request_kind=request_kind,
            window_label=window_label, deadline_at=deadline_at,
            validate=validate, http_client=self.http_client)

    def probe_once(self, payload, *, deadline_at=None, validate=None, on_dispatch=None):
        """Send one unreserved probe POST for a caller-owned ledger row."""
        started = time.monotonic()
        remaining = deadline_at - time.monotonic() if deadline_at is not None else self.timeout
        if remaining <= 0:
            raise APITimeoutError(request=httpx.Request('POST', self.url))
        headers = {'Content-Type': 'application/json'}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        if on_dispatch is not None:
            on_dispatch()
        remaining = deadline_at - time.monotonic() if deadline_at is not None else self.timeout
        if remaining <= 0:
            error = APITimeoutError(request=httpx.Request('POST', self.url))
            error.usage_response = DispatchResponse(usage={}, actual_dispatch_count=0)
            raise error
        try:
            response = self.http_client.post(
                self.url, json=payload, headers=headers,
                timeout=min(self.timeout, remaining))
        except httpx.TimeoutException as exc:
            request = getattr(exc, 'request', None) or httpx.Request('POST', self.url)
            raise APITimeoutError(request=request) from exc
        except httpx.TransportError as exc:
            request = getattr(exc, 'request', None) or httpx.Request('POST', self.url)
            raise APIConnectionError(message=str(exc), request=request) from exc
        dispatch_latency_ms = round((time.monotonic() - started) * 1000)
        ledger_response = _failure_response(response.text) or DispatchResponse(
            usage={}, headers=response.headers, text=response.text,
            status_code=response.status_code,
            dispatch_latency_ms=dispatch_latency_ms)
        ledger_response.headers = response.headers
        ledger_response.text = response.text
        ledger_response.status_code = response.status_code
        ledger_response.dispatch_latency_ms = dispatch_latency_ms
        if not response.is_success:
            error = _request_error(
                response.status_code, response,
                ledger_response.body if ledger_response.body is not None else response.text)
            error.usage_response = ledger_response
            raise error
        try:
            body = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            error = APIStatusError(
                'System One returned invalid JSON', response=response,
                body=ledger_response.body)
            error.usage_response = ledger_response
            raise error from exc
        ledger_response = _ledger_response(body, response=response)
        ledger_response.dispatch_latency_ms = dispatch_latency_ms
        if deadline_at is not None and time.monotonic() >= deadline_at:
            error = APITimeoutError(request=httpx.Request('POST', self.url))
            error.usage_response = ledger_response
            raise error
        if validate:
            try:
                validate(body)
            except Exception as error:
                error.usage_response = ledger_response
                raise
        return body, ledger_response

    def close(self):
        if self._owns_client:
            self.http_client.close()


def _response_usage(body):
    usage = body.get('usage') if isinstance(body, dict) else None
    if not isinstance(usage, dict):
        return {}, None
    normalized = {
        'input_tokens': _safe_token_count(usage.get('input_tokens')),
        'output_tokens': _safe_token_count(usage.get('output_tokens')),
    }
    cost = usage.get('cost_usd', usage.get('cost'))
    try:
        valid_cost = (isinstance(cost, (int, float)) and not isinstance(cost, bool)
                      and math.isfinite(cost) and cost >= 0)
    except OverflowError:
        valid_cost = False
    return normalized, cost if valid_cost else None


def _ledger_response(body, *, response=None):
    usage, cost = _response_usage(body)
    return DispatchResponse(
        usage=usage,
        returned_model=body.get('model') if isinstance(body, dict) else None,
        provider_reported_cost_usd=cost,
        headers=getattr(response, 'headers', None),
        body=body,
        text=getattr(response, 'text', None),
        status_code=getattr(response, 'status_code', None),
    )


def _check_cancel(metadata):
    metadata['cancel_check']()
    account_check = metadata.get('account_check')
    if account_check:
        account_check(metadata)


def _reserved_tokens(payload):
    return request_token_estimate(payload)


def _failure_response(body):
    try:
        parsed = body if isinstance(body, dict) else json.loads(body)
    except (TypeError, ValueError):
        return None
    return _ledger_response(parsed)


def _request_error(status, response, body):
    message = f'System One HTTP {status}'
    error_type = {
        401: AuthenticationError,
        403: PermissionDeniedError,
        404: NotFoundError,
        422: UnprocessableEntityError,
        429: RateLimitError,
    }.get(status)
    if status >= 500:
        error_type = InternalServerError
    error_type = error_type or APIStatusError
    error = error_type(message, response=response, body=body)
    error.usage_response = _failure_response(body)
    error.systemone_dispatch_exhausted = True
    return error


def _finalize_attempt(db, attempt_id, state, response, started):
    latency = (round((time.monotonic() - started) * 1000)
               if getattr(response, "actual_dispatch_count", 1) else None)
    cost = db.finalize_llm_attempt_from_response(
        attempt_id, state, response, dispatch_latency_ms=latency)
    usage = getattr(response, 'usage', None)
    if not isinstance(usage, dict):
        usage = {}
    input_tokens = _safe_token_count(usage.get('input_tokens'))
    output_tokens = _safe_token_count(usage.get('output_tokens'))
    ctx = run_context.current()
    if ctx:
        ctx.tokens.add(input_tokens or 0, output_tokens or 0, cost)
    return cost


def _safe_token_count(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
        return None
    return int(value) if 0 <= value <= 2**63 - 1 else None


def _sleep_retry(delay, metadata, deadline_at):
    return metadata['sleep_retry'](
        delay, before_wait=lambda: _check_cancel(metadata),
        deadline_at=deadline_at)


def dispatch_systemone_request(payload, *, url, api_key, model, timeout,
                               request_kind, window_label=None,
                               max_retries=None, deadline_at=None,
                               validate=None, http_client=None):
    """Send one payload with per-attempt reservation, retry, and accounting."""
    metadata = run_context.current_llm_dispatch_context() or {}
    provider = metadata.get('provider_key', 'systemone-compatible')
    slot = metadata.get('credential_slot', 'primary')
    if max_retries is None:
        max_retries = metadata.get('max_retries', 0)
    retries = max_retries
    retries = max(0, int(retries))
    deadline_at = deadline_at or metadata.get('deadline_at')
    db = metadata['database']
    last_error = None

    for attempt in range(retries + 1):
        _check_cancel(metadata)
        if deadline_at is not None and time.monotonic() >= deadline_at:
            request = httpx.Request('POST', url)
            raise APITimeoutError(request=request) from last_error
        attempt_id, hold_until = metadata['reserve_request'](
            db, provider, slot,
            attempt={
                'run_id': metadata.get('run_id'),
                'logical_call_id': metadata.get('logical_call_id'),
                'podcast_id': metadata.get('podcast_id'),
                'episode_id': metadata.get('episode_id'),
                'phase_key': metadata.get('phase_key', 'detection'),
                'invoking_pass': metadata.get('invoking_pass'),
                'configured_model': model,
                'window_label': (
                    f"{window_label or metadata.get('call_label')}:{request_kind}"),
                'reserved_tokens': _reserved_tokens(payload),
                'dispatch_count': 0,
            },
        )
        if attempt_id is None:
            raise metadata['reservation_refused'](provider, slot, hold_until,
                                       metadata.get('slug'), metadata.get('episode_id'),
                                       window_label or metadata.get('call_label'),
                                       metadata.get('phase_key', 'detection'))
        run_context.note_llm_dispatch(attempt_id)
        run_context.begin_dispatch(attempt_id)
        started = time.monotonic()
        response = None
        result = None
        state = 'failure'
        sent = False
        try:
            headers = {'Content-Type': 'application/json'}
            if api_key:
                headers['Authorization'] = f'Bearer {api_key}'
            client_context = httpx.Client(timeout=timeout) if http_client is None else nullcontext(http_client)
            with client_context as client:
                _check_cancel(metadata)
                if deadline_at is not None and time.monotonic() >= deadline_at:
                    raise APITimeoutError(request=httpx.Request('POST', url))
                db.bump_llm_attempt_dispatches(attempt_id)
                _check_cancel(metadata)
                remaining = deadline_at - time.monotonic() if deadline_at else timeout
                if remaining <= 0:
                    raise APITimeoutError(request=httpx.Request('POST', url))
                sent = True
                result = client.post(url, json=payload, headers=headers,
                                     timeout=min(timeout, remaining))
            response = _failure_response(result.text) or DispatchResponse(
                usage={}, headers=result.headers, body=None,
                text=result.text, status_code=result.status_code)
            response.headers = result.headers
            response.text = result.text
            response.status_code = result.status_code
            if not result.is_success:
                status = result.status_code
                body = response.body if response else result.text
                error = _request_error(status, result, body)
                error.usage_response = response
                state = 'inconclusive' if metadata.get('inconclusive', lambda _e: False)(error) else 'failure'
                raise error
            body = result.json()
            response = _ledger_response(body, response=result)
            if deadline_at is not None and time.monotonic() >= deadline_at:
                error = APITimeoutError(request=httpx.Request('POST', url))
                error.usage_response = response
                error.systemone_dispatch_exhausted = True
                raise error
            _check_cancel(metadata)
            if validate:
                try:
                    validate(body)
                except JevReviewValidationError as error:
                    error.usage_response = response
                    raise
                except Exception:
                    last_error = APIStatusError(
                        'System One returned an invalid protocol response',
                        response=result, body=body)
                    last_error.usage_response = response
                    last_error.systemone_protocol_failure = True
                else:
                    state = 'success'
                    return body
            else:
                state = 'success'
                return body
        except metadata['cancel_exceptions']:
            state = 'cancelled'
            raise
        except JevReviewValidationError:
            raise
        except APIStatusError as error:
            last_error = error
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            request = getattr(exc, 'request', None) or httpx.Request('POST', url)
            last_error = (APITimeoutError(request=request)
                          if isinstance(exc, httpx.TimeoutException)
                          else APIConnectionError(message=str(exc), request=request))
            last_error.usage_response = response
            last_error.systemone_dispatch_exhausted = True
        except (ValueError, json.JSONDecodeError):
            if result is None:
                raise
            last_error = APIStatusError(
                'System One returned invalid JSON', response=result,
                body=response.body if response else None)
            last_error.usage_response = response
            last_error.systemone_dispatch_exhausted = True
        except Exception:
            raise
        finally:
            try:
                if not sent:
                    response = DispatchResponse(usage={}, actual_dispatch_count=0)
                _finalize_attempt(db, attempt_id, state, response, started)
            finally:
                run_context.end_dispatch()

        if getattr(last_error, 'systemone_protocol_failure', False):
            raise last_error
        terminal = metadata['terminal_error'](
            last_error, model=model, slug=metadata.get('slug'),
            episode_id=metadata.get('episode_id'),
            call_label=metadata.get('call_label'), provider=provider,
            credential_slot=slot, phase=metadata.get('phase_key'))
        if terminal is not None:
            last_error = terminal
        if attempt >= retries or not metadata['retryable'](last_error):
            raise last_error
        delay = metadata['retry_delay'](last_error, attempt)
        if not _sleep_retry(delay, metadata, deadline_at):
            raise last_error

    raise last_error
