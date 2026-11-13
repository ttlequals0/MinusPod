"""Shared plumbing for endpoint connection tests (#544).

Each external endpoint (remote whisper, LLM provider base URL, PodcastIndex) has a
"test connection" probe. Request shapes differ; transport-failure classification,
the capped body read and the result dict are shared here.

Result dict: ok (endpoint accepted the probe), reachable (server responded at all),
status (HTTP code if received), detail (user-facing message).
"""
import json
import logging

import requests

from utils.safe_http import (
    IncompleteResponseError, read_response_capped, ResponseTooLargeError,
)
from utils.url import SSRFError

logger = logging.getLogger(__name__)

# Probe responses are tiny (a short transcript, a one-result search); anything
# bigger means the URL points at something else. Model catalogs pass their own cap.
PROBE_RESPONSE_CAP_BYTES = 64 * 1024


def run_probe(send, timeout_seconds: float, log_context: str = '',
              slow_hint: str = '',
              cap_bytes: int = PROBE_RESPONSE_CAP_BYTES):
    """Run ``send`` (a zero-arg callable doing the ``stream=True`` request) and normalize transport failures.

    ``slow_hint`` is appended to the read-timeout message where a slow first answer is benign (model cold-load).
    Returns ``(error_result, status, body_bytes)``: error_result is a full result dict on failure
    or an unreadable accepted reply, else None; body_bytes is None when a rejected reply was unreadable.
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
    body_bytes = None
    unread = ''
    try:
        body_bytes = read_response_capped(response, cap_bytes)
    except ResponseTooLargeError:
        cap = f'{cap_bytes // (1024 * 1024)} MB' if cap_bytes >= 1024 * 1024 else f'{cap_bytes // 1024} KB'
        unread = f'with a reply larger than {cap}, so the test could not read it'
    except (IncompleteResponseError, requests.RequestException):
        unread = 'but the connection closed before the full reply arrived'
    finally:
        response.close()
    # An error status keeps its own message in the caller; only an accepted
    # probe whose body could not be read is reported here.
    if unread and status < 400:
        return ({'ok': False, 'reachable': True, 'status': status,
                 'detail': f'The server answered HTTP {status} {unread}.'}, status, None)
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
