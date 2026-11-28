"""An SSRFError is reported by cause: a DNS failure is not logged or answered as a blocked address."""
import logging
import os
import sys
import tempfile
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='ssrf-classification-test-'))


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')


def _csrf_headers(client):
    for cookie in client._cookies.values():
        if cookie.key == 'minuspod_csrf':
            return {'X-CSRF-Token': cookie.value}
    return {}


_SSRF_CAUSES = [
    ("Cannot resolve hostname: 'feeds.example.com'", logging.INFO, 'Could not resolve host in add_feed',
     'Could not resolve the host name in the feed URL'),
    ('Blocked private IP: 10.0.0.5', logging.WARNING, 'Blocked address in add_feed',
     'Invalid feed URL: Blocked private IP: 10.0.0.5'),
    ('Blocked port: 6379', logging.WARNING, 'Rejected URL in add_feed',
     'Invalid feed URL: Blocked port: 6379'),
    ("Blocked URL scheme: 'ftp'", logging.WARNING, 'Rejected URL in add_feed',
     "Invalid feed URL: Blocked URL scheme: 'ftp'"),
    ('HTTPS -> HTTP redirect blocked: https://feeds.example.com', logging.WARNING,
     'Insecure redirect blocked in add_feed',
     'Invalid feed URL: HTTPS -> HTTP redirect blocked: https://feeds.example.com'),
]


@pytest.mark.parametrize('cause,level,log_text,message', _SSRF_CAUSES)
def test_add_feed_ssrf_failure_is_classified(app_client, caplog, cause, level, log_text, message):
    from utils.url import SSRFError
    _authed(app_client)
    with caplog.at_level(logging.DEBUG, logger='podcast.api'), \
            patch('api.feeds.validate_feed_source_url', side_effect=SSRFError(cause)):
        resp = app_client.post('/api/v1/feeds', json={
            'sourceUrl': 'https://user:pw@feeds.example.com/secret/path.xml?token=abc123',
        }, headers=_csrf_headers(app_client))
    assert resp.status_code == 400
    body = resp.get_json()
    assert body['error'] == message and body['status'] == 400
    records = [r for r in caplog.records if r.name == 'podcast.api' and log_text in r.getMessage()]
    assert len(records) == 1 and records[0].levelno == level
    assert 'SSRF blocked' not in caplog.text
    for secret in ('/secret/path', 'token=abc123', 'pw@', 'user:'):
        assert secret not in caplog.text


def test_ssrf_dns_failure_logs_info_at_other_call_site(caplog):
    from utils.http import log_ssrf_failure
    from utils.url import SSRFError
    log = logging.getLogger('ssrf-site-test')
    with caplog.at_level(logging.DEBUG, logger='ssrf-site-test'):
        log_ssrf_failure(log, 'download_audio', SSRFError("Cannot resolve hostname: 'cdn.example.com'"),
                         'https://cdn.example.com/a.mp3?sig=zzz')
        log_ssrf_failure(log, 'download_audio', SSRFError('Blocked loopback IP: 127.0.0.1'),
                         'https://cdn.example.com/a.mp3?sig=zzz')
    assert [(r.levelno, r.getMessage()) for r in caplog.records] == [
        (logging.INFO, 'Could not resolve host in download_audio (https://cdn.example.com)'),
        (logging.WARNING, 'Blocked address in download_audio: Blocked loopback IP: 127.0.0.1 (https://cdn.example.com)'),
    ]
