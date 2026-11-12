"""A source URL with raw whitespace or a control character is refused where it enters, never at refresh."""
import io
import logging
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('feed_url_whitespace_test_')
import config_transfer
import rss_parser
from api import feeds as api_feeds
from api import get_database
from config_transfer import ConfigTransferError
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


@pytest.mark.parametrize('password', ['pa ss', 'pa\tss', 'pa/ss', 'pa#ss'])
def test_safe_url_for_log_hides_a_password_the_userinfo_pass_cannot_match(password):
    # Raw whitespace, "/" or "#" in a password ends the userinfo pass, so log sites rely on this helper.
    out = safe_url_for_log(f'https://alice:{password}@feeds.example.com/f.xml?k=SECRET')
    assert 'pa' not in out and 'ss' not in out and 'SECRET' not in out


def test_add_feed_refuses_whitespace_and_logs_no_credential(app_client, caplog):
    _authed(app_client)
    with patch('api.feeds.validate_url'), patch('main_app.feeds.refresh_rss_feed'), caplog.at_level(logging.INFO):
        resp = app_client.post('/api/v1/feeds', json={'sourceUrl': BAD_URL, 'slug': 'ws-add'},
                               headers=_csrf(app_client))
    assert resp.status_code == 400
    assert 'whitespace or control' in resp.get_json()['error']
    assert 'pass' not in caplog.text
    assert get_database().get_podcast_by_slug('ws-add') is None


def test_add_feed_logs_the_source_url_without_userinfo_or_query(app_client, caplog):
    _authed(app_client)
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


def test_add_feed_strips_leading_and_trailing_whitespace(app_client):
    _authed(app_client)
    with patch('api.feeds.validate_url'), patch('main_app.feeds.refresh_rss_feed'):
        resp = app_client.post('/api/v1/feeds', json={
            'sourceUrl': ' \thttps://feeds.example.com/strip.xml\n', 'slug': 'ws-strip'}, headers=_csrf(app_client))
    try:
        assert resp.status_code == 201, resp.get_json()
        assert get_database().get_podcast_by_slug('ws-strip')['source_url'] == 'https://feeds.example.com/strip.xml'
    finally:
        get_database().delete_podcast('ws-strip')


def test_update_feed_strips_leading_and_trailing_whitespace():
    parsed = MagicMock(feed={'title': 'Example'}, entries=[])
    parser = MagicMock(parse_feed=MagicMock(return_value=parsed))
    with patch('api.feeds.validate_url'), patch('api.feeds._fetch_feed_content', return_value=(parser, b'<rss/>')):
        assert api_feeds._validate_source_url('\n https://feeds.example.com/new.xml\t ') == (
            'https://feeds.example.com/new.xml', None)


def test_opml_import_strips_leading_and_trailing_whitespace(app_client):
    _authed(app_client)
    opml = ('<opml version="2.0"><body><outline type="rss" text="WS Strip OPML" '
            'xmlUrl="  https://feeds.example.com/o.xml&#10;"/></body></opml>')
    with patch('api.feeds.validate_url'), patch('main_app.feeds.refresh_rss_feed'):
        resp = app_client.post('/api/v1/feeds/import-opml', data={'opml': (io.BytesIO(opml.encode()), 'f.opml')},
                               content_type='multipart/form-data', headers=_csrf(app_client))
    body = resp.get_json()
    assert body['failed'] == 0, body
    slug = body['feeds']['imported'][0]['slug']
    try:
        assert get_database().get_podcast_by_slug(slug)['source_url'] == 'https://feeds.example.com/o.xml'
    finally:
        get_database().delete_podcast(slug)


def test_update_feed_refuses_whitespace(app_client):
    _authed(app_client)
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
    assert get_database().get_podcast_by_slug('ws-import') is None


def _config_document(source, slug='ws-config'):
    return {
        'format': config_transfer.FORMAT, 'formatVersion': config_transfer.FORMAT_VERSION,
        'appVersion': '2.98.2', 'exportedAt': '2026-10-08T00:00:00+00:00', 'containsSecrets': False,
        'settings': {}, 'feeds': [{'slug': slug, 'feedType': 'subscribed', 'settings': {'source_url': source}}],
    }


def _import(db, document, monkeypatch):
    monkeypatch.setattr(config_transfer, '_validate_for_tier', lambda *_args: None)
    monkeypatch.setattr(config_transfer, '_after_feed_commit', lambda *_args: [])
    preview = config_transfer.build_preview(db, document, 'feeds')
    config_transfer.apply_config(db, document, 'feeds', None, preview['previewToken'])


@pytest.mark.parametrize('raw,stored', [
    (' https://feeds.example.com/f x.xml\n', 'https://feeds.example.com/f%20x.xml'),
    ('https://feeds.example.com/f\tx.xml', 'https://feeds.example.com/f%09x.xml'),
    ('https://feeds.example.com/f\u00a0x.xml', 'https://feeds.example.com/f%C2%A0x.xml'),
    ('https://alice:pass word@h.example/f x.xml', 'https://alice:pass%20word@h.example/f%20x.xml'),
    ('https://feeds.example.com/feed.xml?a=1', 'https://feeds.example.com/feed.xml?a=1'),
    ('https://b\u00fccher.example/f.xml', 'https://b\u00fccher.example/f.xml'),
    ('https://feeds.example.com/f.xml?d=%41&t=%7e', 'https://feeds.example.com/f.xml?d=%41&t=%7e'),
    ('https://feeds.example.com/100%.xml?q=a b', 'https://feeds.example.com/100%.xml?q=a%20b'),
    ('https://feeds.example.com/f\x01x.xml', 'https://feeds.example.com/f%01x.xml'),
], ids=['space', 'tab', 'nbsp', 'userinfo', 'clean', 'idn-host', 'escapes', 'percent-literal', 'control'])
def test_config_import_encodes_only_whitespace_and_controls(temp_db, monkeypatch, raw, stored):
    _import(temp_db, _config_document(raw), monkeypatch)
    assert temp_db.get_podcast_by_slug('ws-config')['source_url'] == stored


def test_config_import_restores_a_backup_over_its_own_stored_whitespace_url(temp_db, monkeypatch):
    raw = 'https://feeds.example.com/f x.xml'
    temp_db.create_podcast('ws-config', raw, 'Example')
    document = config_transfer.export_config(temp_db, '2.98.2')
    _import(temp_db, document, monkeypatch)
    assert temp_db.get_podcast_by_slug('ws-config')['source_url'] == raw


def test_config_import_sees_a_stored_whitespace_url_on_another_slug_as_the_same_feed(temp_db, monkeypatch):
    temp_db.create_podcast('ws-other', 'https://feeds.example.com/f x.xml', 'Example')
    with pytest.raises(ConfigTransferError, match='already belongs to another feed'):
        _import(temp_db, _config_document('https://feeds.example.com/f x.xml'), monkeypatch)
    assert temp_db.get_podcast_by_slug('ws-config') is None
    assert temp_db.get_podcast_by_slug('ws-other')['source_url'] == 'https://feeds.example.com/f x.xml'


def test_config_import_apply_sees_a_stored_whitespace_url_on_another_slug(temp_db, monkeypatch):
    document = _config_document('https://feeds.example.com/f x.xml')
    monkeypatch.setattr(config_transfer, '_validate_for_tier', lambda *_args: None)
    preview = config_transfer.build_preview(temp_db, document, 'feeds')
    temp_db.create_podcast('ws-other', 'https://feeds.example.com/f x.xml', 'Example')
    with pytest.raises(ConfigTransferError, match='already belongs to another feed'):
        config_transfer.apply_config(temp_db, document, 'feeds', None, preview['previewToken'])
    assert temp_db.get_podcast_by_slug('ws-config') is None


def _multi_feed_document(sources):
    document = _config_document('x')
    document['feeds'] = [{'slug': f'ws-multi-{i}', 'feedType': 'subscribed', 'settings': {'source_url': source}}
                         for i, source in enumerate(sources)]
    return document


def _count_podcast_scans(conn, scans):
    conn.set_trace_callback(
        lambda sql: scans.append(sql) if 'FROM podcasts WHERE source_url IS NOT NULL' in sql else None)


def test_config_import_scans_podcasts_once_per_pass(temp_db, monkeypatch):
    document = _multi_feed_document([f'https://feeds.example.com/{i}.xml' for i in range(5)])
    monkeypatch.setattr(config_transfer, '_validate_for_tier', lambda *_args: None)
    monkeypatch.setattr(config_transfer, '_after_feed_commit', lambda *_args: [])
    scans = []
    _count_podcast_scans(temp_db.get_connection(), scans)
    preview = config_transfer.build_preview(temp_db, document, 'feeds')
    assert len(scans) == 1
    config_transfer.apply_config(temp_db, document, 'feeds', None, preview['previewToken'])
    # Apply re-runs the preview, then scans once for its own write pass.
    assert len(scans) == 3
    assert temp_db.get_podcast_by_slug('ws-multi-4') is not None


@pytest.mark.parametrize('sources', [
    ['https://feeds.example.com/a.xml', 'https://feeds.example.com/a.xml'],
    ['https://feeds.example.com/f x.xml', 'https://feeds.example.com/f%20x.xml'],
], ids=['identical', 'space-vs-percent-20'])
def test_config_import_preview_catches_two_feeds_with_one_url_in_a_backup(temp_db, monkeypatch, sources):
    monkeypatch.setattr(config_transfer, '_validate_for_tier', lambda *_args: None)
    with pytest.raises(ConfigTransferError, match='already belongs to another feed'):
        config_transfer.build_preview(temp_db, _multi_feed_document(sources), 'feeds')
    assert temp_db.get_podcast_by_slug('ws-multi-1') is None


def test_config_import_still_refuses_a_url_that_fails_validation(temp_db, monkeypatch):
    def reject(*_args):
        raise SSRFError('Blocked private IP')

    document = _config_document('https://alice:pass word@10.0.0.1/f x.xml')
    preview = config_transfer.build_preview(temp_db, document, 'feeds')
    monkeypatch.setattr(config_transfer, '_validate_for_tier', reject)
    with pytest.raises(ConfigTransferError, match='security validation: Blocked private IP'):
        config_transfer.apply_config(temp_db, document, 'feeds', None, preview['previewToken'])
    assert temp_db.get_podcast_by_slug('ws-config') is None


def test_config_import_refuses_a_non_http_url_after_stripping(temp_db):
    with pytest.raises(ConfigTransferError, match='needs an HTTP'):
        config_transfer.build_preview(temp_db, _config_document(' ftp://feeds.example.com/f x.xml '), 'feeds')


class _HtmlResponse:
    status_code = 200
    headers = {'Content-Type': 'text/html'}

    def raise_for_status(self):
        pass

    def close(self):
        pass


@pytest.mark.parametrize('conditional', [False, True])
def test_feed_content_type_rejection_logs_no_credential(monkeypatch, caplog, conditional):
    monkeypatch.setattr(rss_parser, 'safe_get', lambda *a, **k: _HtmlResponse())
    parser = rss_parser.RSSParser()
    url = 'https://alice:SECRET@feeds.example.com/f.xml?key=SECRET'
    with caplog.at_level(logging.WARNING):
        parser.fetch_feed_conditional(url) if conditional else parser.fetch_feed(url)
    assert 'rejected on content-type' in caplog.text
    assert 'SECRET' not in caplog.text and 'url=https://feeds.example.com ' in caplog.text


def test_url_space_or_control_set_is_whitespace_or_cc():
    import sys
    import unicodedata
    from utils.url import URL_SPACE_OR_CONTROL_RE
    for code in range(sys.maxunicode + 1):
        ch = chr(code)
        assert bool(URL_SPACE_OR_CONTROL_RE.match(ch)) == (
            ch.isspace() or unicodedata.category(ch) == 'Cc'), hex(code)
