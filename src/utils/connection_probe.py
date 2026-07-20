"""Shared plumbing for endpoint connection tests (#544).

Each configured external endpoint (remote whisper transcriber, LLM provider
base URL, PodcastIndex) exposes a "test connection" probe. The request shape
differs per endpoint, but the transport-failure classification, the capped
body read, and the result dict contract are identical; they live here so the
probes cannot drift apart.

Result dict contract: ok (the endpoint accepted the probe), reachable (the
server responded at all), status (HTTP code when one was received), detail
(user-facing outcome message).
"""
import json
import logging

import requests

from utils.safe_http import read_response_capped, ResponseTooLargeError
from utils.url import SSRFError

logger = logging.getLogger(__name__)

# Probe responses are tiny (a model list, a short transcript, a one-result
# search); anything bigger means the URL points at something else entirely.
PROBE_RESPONSE_CAP_BYTES = 64 * 1024


def run_probe(send, timeout_seconds: float, log_context: str = '',
              slow_hint: str = ''):
    """Execute ``send`` (a zero-arg callable performing the HTTP request with
    ``stream=True``) and normalize transport failures.

    ``slow_hint`` is appended to the read-timeout message for endpoints
    where a slow first answer has a known benign cause (model cold-load).

    Returns ``(error_result, status, body_bytes)``. ``error_result`` is a
    complete result dict when the request never produced an HTTP response,
    else None with ``status`` and the capped ``body_bytes`` (None when the
    body was oversized or died mid-read) filled in.
    """
    try:
        response = send()
    except SSRFError:
        return ({'ok': False, 'reachable': False,
                 'detail': 'Base URL failed SSRF validation.'}, None, None)
    except ValueError:
        # urllib/requests raise ValueError for malformed URLs (e.g. a
        # non-numeric or out-of-range port typed into the form).
        return ({'ok': False, 'reachable': False,
                 'detail': 'The base URL is not a valid URL. Check the '
                           'format, including the port.'}, None, None)
    except requests.ConnectTimeout:
        return ({'ok': False, 'reachable': False,
                 'detail': 'Could not connect to the server within '
                           f'{int(timeout_seconds)} seconds. Check the host '
                           'and port.'}, None, None)
    except requests.Timeout:
        # Read timeout: the connection succeeded but the answer did not
        # arrive in time.
        detail = ('The server accepted the connection but did not answer '
                  f'within {int(timeout_seconds)} seconds.')
        if slow_hint:
            detail += ' ' + slow_hint
        return ({'ok': False, 'reachable': True, 'detail': detail},
                None, None)
    except requests.RequestException as e:
        logger.info(f"Connection probe failed for {log_context}: {e}")
        return ({'ok': False, 'reachable': False,
                 'detail': 'Could not connect to the server. Check the host '
                           'and port, and that the service is running.'},
                None, None)

    status = response.status_code
    try:
        body_bytes = read_response_capped(response, PROBE_RESPONSE_CAP_BYTES)
    except (ResponseTooLargeError, requests.RequestException):
        body_bytes = None
    finally:
        response.close()
    return None, status, body_bytes


def parse_probe_json(body_bytes):
    """Decode a probe body as JSON; None when absent or not JSON."""
    if body_bytes is None:
        return None
    try:
        return json.loads(body_bytes.decode('utf-8', 'replace'))
    except ValueError:
        return None


def probe_snippet(body_bytes) -> str:
    """First 200 bytes of a probe body, flattened for display."""
    if not body_bytes:
        return ''
    return ' '.join(body_bytes[:200].decode('utf-8', 'replace').split())


def rejected_detail(status: int, body_bytes) -> str:
    """Shared fallback message for a reachable server that rejected the
    probe with a status no specific branch explains."""
    snippet = probe_snippet(body_bytes)
    return (f'The server is reachable but rejected the test request '
            f'(HTTP {status})' + (f': {snippet}' if snippet else '.'))
