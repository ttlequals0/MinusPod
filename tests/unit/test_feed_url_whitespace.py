"""A source URL with raw whitespace or a control character is refused where it enters, never at refresh."""
import io
import logging
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('feed_url_whitespace_test_')
from utils.http import safe_url_for_log
from utils.url import SSRFError, reject_url_whitespace, validate_url

SPACES = [chr(c) for c in range(0x110000) if chr(c).isspace()]
CONTROLS = ['\x00', '\x01', '\x1b', '\x7f', '\x9f']
BAD_URL = 'https://alice:pass word@feeds.example.com/f.xml'


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')


def _csrf(client):
    for cookie in client._cookies.values():
        if cookie.key == 'minuspod_csrf':
            return {'X-CSRF-Token': cookie.value}
    return {}


def test_every_whitespace_code_point_is_covered():
    assert len(SPACES) == 29


@pytest.mark.parametrize('char', SPACES + CONTROLS, ids=[f'U+{ord(c):04X}' for c in SPACES + CONTROLS])
def test_whitespace_and_control_characters_are_refused(char):
    with pytest.raises(SSRFError, match='whitespace or control'):
        reject_url_whitespace(f'https://feeds.example.com/a{char}b.xml')


def test_a_clean_url_passes():
    reject_url_whitespace('https://feeds.example.com/a%20b.xml?x=1')


def test_refresh_validation_still_accepts_a_stored_url_with_a_space():
    with patch('utils.url.resolve_and_check'):
        assert validate_url('https://feeds.example.com/a b.xml') == 'https://feeds.example.com/a b.xml'


def test_safe_url_for_log_drops_userinfo_with_a_space():
    assert safe_url_for_log(BAD_URL) == 'https://feeds.example.com'
    assert safe_url_for_log(BAD_URL, keep_path=True) == 'https://feeds.example.com/f.xml'


def test_add_feed_refuses_whitespace_and_logs_no_credential(app_client, caplog):
    _authed(app_client)
    with patch('api.feeds.validate_url'), patch('main_app.feeds.refresh_rss_feed'), caplog.at_level(logging.INFO):
        resp = app_client.post('/api/v1/feeds', json={'sourceUrl': BAD_URL, 'slug': 'ws-add'},
                               headers=_csrf(app_client))
    assert resp.status_code == 400
    assert 'whitespace or control' in resp.get_json()['error']
    assert 'pass' not in caplog.text
    from api import get_database
    assert get_database().get_podcast_by_slug('ws-add') is None


def test_add_feed_logs_the_source_url_without_userinfo_or_query(app_client, caplog):
    _authed(app_client)
    from api import get_database
    with patch('api.feeds.validate_url'), patch('main_app.feeds.refresh_rss_feed'), caplog.at_level(logging.INFO):
        resp = app_client.post('/api/v1/feeds', json={
            'sourceUrl': 'https://alice:SECRET@feeds.example.com/f.xml?key=SECRET', 'slug': 'ws-log'},
            headers=_csrf(app_client))
    try:
        assert resp.status_code == 201, resp.get_json()
        assert 'Created new feed: ws-log -> https://feeds.example.com' in caplog.text
        assert 'SECRET' not in caplog.text
    finally:
        get_database().delete_podcast('ws-log')


def test_update_feed_refuses_whitespace(app_client):
    _authed(app_client)
    from api import get_database
    db = get_database()
    db.create_podcast('ws-update', 'https://feeds.example.com/old.xml')
    try:
        with patch('api.feeds.validate_url'):
            resp = app_client.patch('/api/v1/feeds/ws-update', json={'sourceUrl': 'https://feeds.example.com/a\tb.xml'},
                                    headers=_csrf(app_client))
        assert resp.status_code == 400
        assert 'whitespace or control' in resp.get_json()['error']
        assert db.get_podcast_by_slug('ws-update')['source_url'] == 'https://feeds.example.com/old.xml'
    finally:
        db.delete_podcast('ws-update')


def test_opml_import_refuses_whitespace(app_client):
    _authed(app_client)
    opml = ('<opml version="2.0"><body><outline type="rss" text="WS Import" '
            'xmlUrl="https://alice:pass&#32;word@feeds.example.com/f.xml"/></body></opml>')
    with patch('api.feeds.validate_url'):
        resp = app_client.post('/api/v1/feeds/import-opml', data={'opml': (io.BytesIO(opml.encode()), 'f.opml')},
                               content_type='multipart/form-data', headers=_csrf(app_client))
    body = resp.get_json()
    assert body['failed'] == 1
    assert 'whitespace or control' in body['feeds']['failed'][0]['error']
    from api import get_database
    assert get_database().get_podcast_by_slug('ws-import') is None


def test_config_import_refuses_whitespace():
    from config_transfer import ConfigTransferError, _validate_import_feed_urls
    feeds = [{'slug': 'ws-config', 'feedType': 'subscribed', 'settings': {'source_url': BAD_URL}}]
    with patch('config_transfer._validate_for_tier'), pytest.raises(ConfigTransferError, match='whitespace or control'):
        _validate_import_feed_urls(feeds)


class _HtmlResponse:
    status_code = 200
    headers = {'Content-Type': 'text/html'}

    def raise_for_status(self):
        pass

    def close(self):
        pass


@pytest.mark.parametrize('conditional', [False, True])
def test_feed_content_type_rejection_logs_no_credential(monkeypatch, caplog, conditional):
    import rss_parser
    monkeypatch.setattr(rss_parser, 'safe_get', lambda *a, **k: _HtmlResponse())
    parser = rss_parser.RSSParser()
    url = 'https://alice:SECRET@feeds.example.com/f.xml?key=SECRET'
    with caplog.at_level(logging.WARNING):
        parser.fetch_feed_conditional(url) if conditional else parser.fetch_feed(url)
    assert 'rejected on content-type' in caplog.text
    assert 'SECRET' not in caplog.text and 'url=https://feeds.example.com ' in caplog.text
