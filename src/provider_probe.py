"""Provider connection probes shared by API handlers and background failover checks."""
from urllib.parse import urlparse
import time

import llm_client
from config import HTTP_MAX_REDIRECTS_API, HTTP_TIMEOUT_PROBE
from config import PROVIDER_TYPESAFE, TYPESAFE_BASE_URL
from provider_budget import manual_rate_limit_caps, request_token_estimate
from utils.connection_probe import parse_probe_json, rejected_detail, run_probe
from utils.http import safe_url_for_log
from utils.safe_http import URLTrust, safe_get

# Fixed providers send credentials only to their canonical endpoints.
FIXED_PROVIDER_PROBES = {
    'anthropic': (
        'https://api.anthropic.com/v1/models',
        lambda key: {'x-api-key': key, 'anthropic-version': '2023-06-01'} if key else {},
    ),
    'openrouter': (
        'https://openrouter.ai/api/v1/key',
        lambda key: {'Authorization': f'Bearer {key}'} if key else {},
    ),
    PROVIDER_TYPESAFE: (
        f'{TYPESAFE_BASE_URL}/models',
        lambda key: {'Authorization': f'Bearer {key}'} if key else {},
    ),
}


def _model_catalog_usable(body) -> bool:
    if not isinstance(body, dict):
        return False
    data = body.get('data', body.get('models'))
    return isinstance(data, list) and all(
        isinstance(model, dict)
        and isinstance(model.get('id') or model.get('name'), str)
        and bool(model.get('id') or model.get('name'))
        for model in data
    )


def _fixed_response_usable(provider: str, body) -> bool:
    if not isinstance(body, dict):
        return False
    if provider in ('anthropic', PROVIDER_TYPESAFE):
        return _model_catalog_usable(body)
    if provider == 'openrouter':
        data = body.get('data', body.get('models'))
        if not isinstance(data, dict):
            return False
        numeric_fields = ('usage', 'limit', 'limit_remaining')
        return any(
            isinstance(data.get(field), (int, float))
            and not isinstance(data.get(field), bool)
            and data[field] >= 0
            for field in numeric_fields
        ) or (isinstance(data.get('label'), str) and bool(data['label'])) \
            or isinstance(data.get('is_free_tier'), bool)
    return False


def same_server(url_a: str, url_b: str) -> bool:
    """True when two base URLs point at the same scheme/host/port."""
    if not url_a or not url_b:
        return False
    try:
        a, b = urlparse(url_a), urlparse(url_b)
        return (a.scheme, a.hostname, a.port) == (b.scheme, b.hostname, b.port)
    except ValueError:
        # Malformed port in a hand-typed URL; never a match.
        return False


def models_request(base_url: str, api_key: str):
    """URL + auth headers for an OpenAI-compatible /models request. Shared
    by /test and /test-connection so the discovery contract lives once."""
    url = base_url.rstrip('/') + '/models'
    headers = {'Authorization': f'Bearer {api_key}'} if api_key else {}
    headers.update(llm_client._opencode_headers(base_url))
    return url, headers


def probe_models_endpoint(base_url: str, api_key: str) -> dict:
    """Probe the same authenticated /models route used for startup discovery."""
    url, headers = models_request(base_url, api_key)
    error, status, body_bytes = run_probe(
        lambda: safe_get(
            url,
            trust=URLTrust.OPERATOR_CONFIGURED,
            timeout=HTTP_TIMEOUT_PROBE,
            max_redirects=HTTP_MAX_REDIRECTS_API,
            headers=headers,
            stream=True,
        ),
        HTTP_TIMEOUT_PROBE,
        log_context=safe_url_for_log(url),
    )
    if error:
        return error

    result = {'ok': False, 'reachable': True, 'status': status}
    if status < 400:
        body = parse_probe_json(body_bytes)
        # The real client reads response.data as the model array
        # (llm_client list_models); a green result must mean discovery
        # will actually work, not just that some JSON came back.
        if isinstance(body, dict) and isinstance(body.get('data'), list):
            result['ok'] = True
            result['detail'] = (f'Connected. The server returned its model '
                                f'list (HTTP {status}).')
        else:
            result['detail'] = (f'The server answered HTTP {status} but did '
                                'not return a model list. Check that the URL '
                                'points at an OpenAI-compatible API.')
    elif status in (401, 403):
        if api_key:
            result['detail'] = (f'The server rejected the saved API key '
                                f'(HTTP {status}). Check the key.')
        else:
            result['detail'] = (f'The endpoint requires an API key '
                                f'(HTTP {status}). The test sends the saved '
                                'key, and only when the tested URL matches '
                                'the saved one. Save your key and base '
                                'URL, then test again.')
    elif status == 404:
        result['detail'] = ('The server is running, but there is no models '
                            'endpoint at this path (HTTP 404). The base URL '
                            'usually ends in /v1.')
    else:
        result['detail'] = rejected_detail(status, body_bytes)
    return result


def probe_systemone_connection(provider: str, base_url: str | None, api_key: str) -> dict:
    """Check native model discovery without making an inference request."""
    result = {'ok': False, 'reachable': False,
              'validation': 'model_catalog', 'inferenceChecked': False}
    if provider == PROVIDER_TYPESAFE:
        if not api_key:
            return {**result, 'detail': 'Save a TypeSafe API key for the selected slot before testing.'}
        base_url = TYPESAFE_BASE_URL
    elif not isinstance(base_url, str) or not base_url.strip():
        return {**result, 'detail': 'Enter a base URL first.'}
    url, headers = models_request(base_url, api_key)
    error, status, body_bytes = run_probe(
        lambda: safe_get(
            url, trust=URLTrust.OPERATOR_CONFIGURED,
            timeout=HTTP_TIMEOUT_PROBE, max_redirects=HTTP_MAX_REDIRECTS_API,
            headers=headers, stream=True,
        ), HTTP_TIMEOUT_PROBE, log_context=safe_url_for_log(url),
    )
    if error:
        return {**result, **error}
    result.update(reachable=True, status=status)
    if 200 <= status < 300:
        if _model_catalog_usable(parse_probe_json(body_bytes)):
            result.update(ok=True, detail='Connection successful. Inference not tested.')
        else:
            result['detail'] = 'The server did not return a model list.'
    elif status in (401, 403):
        result['detail'] = (f'The server rejected the saved API key (HTTP {status}).'
                            if api_key else f'The endpoint requires an API key (HTTP {status}).')
    elif status == 404:
        result['detail'] = 'The model list endpoint was not found (HTTP 404).'
    else:
        result['detail'] = rejected_detail(status, b'')
    return result


def probe_systemone_endpoint(provider: str, base_url: str | None, api_key: str,
                             model: str, *, credential_slot='primary', db) -> dict:
    """Send one native inference probe and account its actual POST."""
    if not isinstance(model, str) or not model.strip():
        return {'ok': False, 'reachable': False,
                'detail': 'Select a model for a supported stage on this slot before testing.'}
    if provider == PROVIDER_TYPESAFE and not api_key:
        return {'ok': False, 'reachable': False,
                'detail': 'Save a TypeSafe API key for the selected slot before testing.'}
    client = llm_client.create_client_for_provider(
        provider, credential_slot=credential_slot, base_url=base_url,
        api_key_override=api_key)
    if client is None:
        return {'ok': False, 'reachable': False, 'detail': 'Provider unavailable.'}
    started = time.monotonic()
    refused = False

    def reserve(payload):
        nonlocal refused
        reservation = db.reserve_llm_attempt(
            run_id=None, podcast_id=None, episode_id=None, phase_key='probe',
            invoking_pass=None, provider_key=provider,
            credential_slot=credential_slot, configured_model=model,
            window_label='connection_probe', dispatch_count=0,
            reserved_tokens=request_token_estimate(payload),
            caps=manual_rate_limit_caps(credential_slot),
        )
        if reservation['attempt_id'] is None:
            refused = True
            raise RuntimeError('provider request budget reached')
        return reservation['attempt_id']

    try:
        probe_result = client.probe_once(model, reserve_request=reserve,
                                         note_dispatch=db.bump_llm_attempt_dispatches)
        attempts = probe_result['attempts']
        if not attempts:
            raise RuntimeError('probe did not dispatch a request')
        for index, (attempt_id, response, dispatch_latency_ms) in enumerate(attempts):
            db.finalize_llm_attempt_from_response(
                attempt_id, 'success', response,
                dispatch_latency_ms=dispatch_latency_ms,
                call_latency_ms=(round((time.monotonic() - started) * 1000)
                                 if index == 0 else None),
            )
        return {'ok': True, 'reachable': True,
                'detail': 'Connected. The System One request completed.'}
    except Exception as error:
        for index, (attempt_id, response, dispatch_latency_ms) in enumerate(
                getattr(error, 'systemone_probe_attempts', [])):
            db.finalize_llm_attempt_from_response(
                attempt_id, 'failure', response,
                dispatch_latency_ms=dispatch_latency_ms,
                call_latency_ms=(round((time.monotonic() - started) * 1000)
                                 if index == 0 else None),
            )
        response = getattr(error, 'response', None)
        ledger_response = getattr(error, 'usage_response', None)
        status = (getattr(response, 'status_code', None)
                  or getattr(ledger_response, 'status_code', None))
        result = {'ok': False, 'reachable': status is not None,
                  'status': status}
        if refused:
            result['detail'] = 'The account request limit has been reached.'
            return result
        if status in (401, 403):
            result['detail'] = f'The server rejected the saved API key (HTTP {status}).'
        elif status:
            result['detail'] = rejected_detail(status, b'')
        else:
            result['detail'] = 'The System One request could not be completed.'
        return result
    finally:
        client.close()


def probe_fixed_endpoint(provider: str, api_key: str) -> dict:
    """Probe a provider's canonical endpoint without sending its key elsewhere."""
    url, header_fn = FIXED_PROVIDER_PROBES[provider]
    error, status, body_bytes = run_probe(
        lambda: safe_get(
            url,
            trust=URLTrust.OPERATOR_CONFIGURED,
            timeout=HTTP_TIMEOUT_PROBE,
            max_redirects=HTTP_MAX_REDIRECTS_API,
            headers=header_fn(api_key),
            stream=True,
        ),
        HTTP_TIMEOUT_PROBE,
        log_context=safe_url_for_log(url),
    )
    if error:
        return error

    result = {'ok': False, 'reachable': True, 'status': status}
    if 200 <= status < 300:
        if _fixed_response_usable(provider, parse_probe_json(body_bytes)):
            result['ok'] = True
            result['detail'] = (f'Connected. The API accepted the request '
                                f'(HTTP {status}).')
        else:
            result['detail'] = (f'The API answered HTTP {status} but not '
                                'with the expected response.')
    elif status in (401, 403):
        if api_key:
            result['detail'] = (f'The API is reachable but rejected the '
                                f'saved key (HTTP {status}). Check the key.')
        else:
            result['detail'] = (f'The API is reachable and requires a key '
                                f'(HTTP {status}). Save an API key, then '
                                'test again.')
    else:
        result['detail'] = rejected_detail(status, body_bytes)
    return result
