"""Compose the native adapter with standalone benchmark accounting."""
from __future__ import annotations

import json
import random
import threading
import time
from decimal import Decimal

import httpx
from config import TYPESAFE_SYSTEMONE_URL
from utils.constants import SEED_SPONSORS
from utils.retry import calculate_backoff
from llm_client import extract_retry_after, is_retryable_error, is_rate_limit_error
from openai import APITimeoutError
from systemone.admission import operation_admission
from systemone.adapter import SystemOneSettings, run_chat_completion
from systemone.transport import SystemOneTransport, _safe_token_count

from .config import ProviderConfig, secret


class NativeCallError(RuntimeError):
    def __init__(self, error, accounting):
        super().__init__(f'System One {type(error).__name__}')
        self.native_accounting = accounting


def call_native(*, provider: ProviderConfig, model_id, system_prompt, user_prompt,
                timeout, max_retries, cancel_event: threading.Event):
    settings = SystemOneSettings(model=model_id, request_timeout=timeout, **dict(provider.systemone))
    deadline_at = time.monotonic() + settings.request_deadline_seconds
    url = (TYPESAFE_SYSTEMONE_URL if provider.client == 'typesafe'
           else provider.base_url.rstrip('/') + '/systemone')
    api_key = secret(provider.api_key_env) if provider.api_key_env else None
    accounting = {
        'request_count': 0, 'input_tokens': 0, 'output_tokens': 0,
        'unknown_usage_request_count': 0, 'unknown_cost_request_count': 0,
        'known_cost_usd': '0', 'cost_source': 'unknown',
    }
    sponsors = tuple({'name': row['name'],
                      'candidates': tuple([row['name'], *(row.get('aliases') or [])])}
                     for row in SEED_SPONSORS
                     if not row['name'].lower().startswith(('jev-', 'sys1-')))
    cost_sources = set()
    known_cost = Decimal('0')

    def record_usage(response):
        nonlocal known_cost
        usage = getattr(response, 'usage', None) or {}
        input_tokens = _safe_token_count(usage.get('input_tokens'))
        output_tokens = _safe_token_count(usage.get('output_tokens'))
        accounting['input_tokens'] += input_tokens or 0
        accounting['output_tokens'] += output_tokens or 0
        accounting['unknown_usage_request_count'] += int(input_tokens is None or output_tokens is None)
        cost = getattr(response, 'provider_reported_cost_usd', None)
        if cost is not None:
            cost = Decimal(str(cost))
            if not cost.is_finite() or cost < 0:
                cost = None
        source = 'reported'
        if cost is None and provider.client == 'typesafe' and input_tokens is not None:
            cost = Decimal(input_tokens) * Decimal('0.042') / Decimal('1000000')
            source = 'estimated'
        if cost is None:
            accounting['unknown_cost_request_count'] += 1
        else:
            known_cost += cost
            cost_sources.add(source)
        accounting['known_cost_usd'] = str(known_cost)
        accounting['cost_source'] = (next(iter(cost_sources)) if len(cost_sources) == 1
                                     else 'mixed' if cost_sources else 'unknown')

    def check():
        if cancel_event.is_set():
            raise RuntimeError('benchmark call cancelled')
        if time.monotonic() >= deadline_at:
            raise APITimeoutError(request=httpx.Request('POST', url))

    with httpx.Client(timeout=timeout) as http_client:
        transport = SystemOneTransport(url=url, api_key=api_key, model=model_id,
                                       timeout=timeout, http_client=http_client)

        def dispatched():
            accounting['request_count'] += 1

        def fetcher(payload, *, request_kind, window_label=None, deadline_at=None, validate=None):
            for attempt in range(max_retries + 1):
                check()
                before_requests = accounting['request_count']
                try:
                    body, response = transport.probe_once(payload, deadline_at=deadline_at,
                                                           validate=validate, on_dispatch=dispatched)
                except Exception as error:
                    response = getattr(error, 'usage_response', None)
                    if getattr(response, 'actual_dispatch_count', 1) == 0:
                        accounting['request_count'] = before_requests
                    elif accounting['request_count'] > before_requests:
                        record_usage(response)
                    check()
                    if attempt >= max_retries or not is_retryable_error(error):
                        raise
                    remaining = deadline_at - time.monotonic()
                    delay = (extract_retry_after(error, max_seconds=min(remaining, settings.retry_after_max_seconds))
                             if attempt == 0 and is_rate_limit_error(error) else None)
                    if delay is None:
                        delay = calculate_backoff(attempt)
                    else:
                        delay = min(delay + random.uniform(0.0, 2.0), settings.retry_after_max_seconds)
                    cancel_event.wait(min(delay, remaining))
                    continue
                record_usage(response)
                check()
                return body

        def admission_check(waited):
            if cancel_event.is_set():
                raise RuntimeError('benchmark call cancelled')
            if waited and provider.api_key_env and secret(provider.api_key_env) != api_key:
                raise RuntimeError('benchmark credentials changed while waiting for local capacity')

        try:
            with operation_admission(url, api_key, settings.max_concurrent_operations,
                                     deadline_at=deadline_at, check=admission_check):
                result = run_chat_completion(
                    messages=[{'role': 'system', 'content': system_prompt},
                              {'role': 'user', 'content': user_prompt}],
                    request_model=model_id, settings=settings, fetcher=fetcher,
                    sponsor_lookup=lambda: sponsors, deadline_at=deadline_at, diagnostics={})
        except Exception as error:
            raise NativeCallError(error, dict(accounting)) from error
    content = result['choices'][0]['message']['content']
    if not isinstance(content, str):
        content = json.dumps(content, separators=(',', ':'))
    return content, accounting
