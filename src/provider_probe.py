"""Shared provider connection probes (#806), used by api/providers.py and
failover.py. Importable without Flask so failover.py's background tick can
use it directly.
"""
from urllib.parse import urlparse

from config import HTTP_MAX_REDIRECTS_API, HTTP_TIMEOUT_PROBE
from llm_client import _opencode_headers
from utils.connection_probe import parse_probe_json, rejected_detail, run_probe
from utils.http import safe_url_for_log
from utils.safe_http import URLTrust, safe_get

# Fixed public endpoints per provider: probe URL + auth header builder.
# Shared by /test and /test-connection so the contract lives once. These
# providers accept no baseUrl input anywhere, so the key can only ever be
# sent to the canonical host.
FIXED_PROVIDER_PROBES = {
    'anthropic': (
        'https://api.anthropic.com/v1/models',
        lambda key: {'x-api-key': key, 'anthropic-version': '2023-06-01'} if key else {},
    ),
    'openrouter': (
        'https://openrouter.ai/api/v1/auth/key',
        lambda key: {'Authorization': f'Bearer {key}'} if key else {},
    ),
}


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
    headers.update(_opencode_headers(base_url))
    return url, headers


def probe_models_endpoint(base_url: str, api_key: str) -> dict:
    """Staged connection probe for an OpenAI-compatible LLM endpoint.

    GET {base}/models -- the same discovery route the real client uses on
    startup -- with the same optional bearer auth. Unlike /test it needs no
    stored key (local Ollama has none) and reports which failure class the
    caller is in rather than a bare pass/fail.
    """
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
                                'the saved one -- save your key and base '
                                'URL, then test again.')
    elif status == 404:
        result['detail'] = ('The server is running, but there is no models '
                            'endpoint at this path (HTTP 404). The base URL '
                            'usually ends in /v1.')
    else:
        result['detail'] = rejected_detail(status, body_bytes)
    return result


def probe_fixed_endpoint(provider: str, api_key: str) -> dict:
    """Staged connection probe for a provider with a fixed public endpoint.

    Answers two questions the bare /test cannot: can this container reach
    the provider at all (egress/DNS), and if not ok, is the problem the
    key or the network. No baseUrl is accepted, so the saved key only ever
    travels to the canonical host.
    """
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
    if status < 400:
        if isinstance(parse_probe_json(body_bytes), dict):
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
