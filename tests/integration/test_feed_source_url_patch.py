"""Integration tests for editable source RSS URL (#484).

PATCH /feeds/{slug} accepts sourceUrl: the server fetches and parses the new
URL before persisting (typos must not silently break the feed), clears the
conditional-GET validators, and triggers an immediate forced refresh.
"""
import logging
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='feed-srcurl-test-'))

VALID_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>New Source Show</title>
    <link>https://new.example.com</link>
    <description>D</description>
    <item>
      <title>Ep One</title>
      <enclosure url="https://new.example.com/ep.mp3" type="audio/mpeg"/>
      <guid>ep1</guid>
    </item>
  </channel>
</rss>"""

EMPTY_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Empty Show</title>
    <link>https://new.example.com</link>
    <description>D</description>
  </channel>
</rss>"""

HTML_PAGE = "<html><head><title></title></head><body>not a feed</body></html>"

# validate_url resolves hostnames, so the new URL must use a real-resolving
# host (example.com), same as the rest of the integration suite.
NEW_URL = 'https://example.com/new-feed.xml'
OLD_URL = 'https://example.com/feed.xml'


@pytest.fixture
def _auth(monkeypatch):
    monkeypatch.delenv('ADMIN_PASSWORD', raising=False)
    yield


@pytest.fixture
def seeded_feed(app_client):
    from api import get_database
    from werkzeug.security import generate_password_hash
    db = get_database()
    db.set_setting('app_password', generate_password_hash('SrcUrlTest123!', method='scrypt'))
    slug = 'source-url-feed'
    db.create_podcast(slug, OLD_URL, title='Source URL Test')
    yield {'slug': slug, 'db': db}
    try:
        db.delete_podcast(slug)
    finally:
        db.set_setting('app_password', '')


@pytest.fixture
def refresh_recorder(monkeypatch):
    """Suppress and record the post-save forced refresh."""
    calls = []

    def _record(slug, feed_url, force=False):
        calls.append({'slug': slug, 'feed_url': feed_url, 'force': force})

    monkeypatch.setattr('main_app.feeds.refresh_rss_feed', _record)
    return calls


def _mock_fetch(monkeypatch, content):
    def _fetch(self, url, timeout=30):
        return content
    monkeypatch.setattr('rss_parser.RSSParser.fetch_feed', _fetch)


def _csrf(app_client):
    with app_client.session_transaction() as sess:
        sess['authenticated'] = True
    app_client.get('/api/v1/auth/status')
    cookie = app_client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}


def test_patch_source_url_valid_persists_and_returns(app_client, seeded_feed, _auth,
                                                     refresh_recorder, monkeypatch):
    _mock_fetch(monkeypatch, VALID_RSS)
    slug = seeded_feed['slug']
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'sourceUrl': NEW_URL}, headers=_csrf(app_client))
    assert r.status_code == 200
    assert r.get_json()['sourceUrl'] == NEW_URL
    podcast = seeded_feed['db'].get_podcast_by_slug(slug)
    assert podcast['source_url'] == NEW_URL


def test_patch_source_url_triggers_forced_refresh_with_new_url(app_client, seeded_feed,
                                                               _auth, refresh_recorder,
                                                               monkeypatch):
    _mock_fetch(monkeypatch, VALID_RSS)
    slug = seeded_feed['slug']
    db = seeded_feed['db']
    db.update_podcast_etag(slug, 'W/"old-etag"', 'Mon, 01 Jan 2024 00:00:00 GMT')
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'sourceUrl': NEW_URL}, headers=_csrf(app_client))
    assert r.status_code == 200
    assert refresh_recorder == [{'slug': slug, 'feed_url': NEW_URL, 'force': True}]
    podcast = db.get_podcast_by_slug(slug)
    assert podcast.get('etag') is None
    assert podcast.get('last_modified_header') is None


def test_patch_source_url_unfetchable_rejected(app_client, seeded_feed, _auth,
                                               refresh_recorder, monkeypatch):
    _mock_fetch(monkeypatch, None)
    slug = seeded_feed['slug']
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'sourceUrl': NEW_URL}, headers=_csrf(app_client))
    assert r.status_code == 400
    assert seeded_feed['db'].get_podcast_by_slug(slug)['source_url'] == OLD_URL
    assert refresh_recorder == []


def test_patch_source_url_non_feed_content_rejected(app_client, seeded_feed, _auth,
                                                    refresh_recorder, monkeypatch):
    _mock_fetch(monkeypatch, HTML_PAGE)
    slug = seeded_feed['slug']
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'sourceUrl': NEW_URL}, headers=_csrf(app_client))
    assert r.status_code == 400
    assert seeded_feed['db'].get_podcast_by_slug(slug)['source_url'] == OLD_URL


@pytest.mark.parametrize('bad_value', ['', '   ', None, 123, ['x']])
def test_patch_source_url_invalid_values_rejected(app_client, seeded_feed, _auth,
                                                  refresh_recorder, bad_value):
    slug = seeded_feed['slug']
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'sourceUrl': bad_value}, headers=_csrf(app_client))
    assert r.status_code == 400
    assert seeded_feed['db'].get_podcast_by_slug(slug)['source_url'] == OLD_URL


def test_patch_source_url_ssrf_blocked(app_client, seeded_feed, _auth,
                                       refresh_recorder, monkeypatch):
    fetch_calls = []

    def _fetch(self, url, timeout=30):
        fetch_calls.append(url)
        return VALID_RSS

    monkeypatch.setattr('rss_parser.RSSParser.fetch_feed', _fetch)
    slug = seeded_feed['slug']
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'sourceUrl': 'http://127.0.0.1/feed.xml'},
                         headers=_csrf(app_client))
    assert r.status_code == 400
    assert 'Invalid feed URL' in r.get_json()['error']
    assert fetch_calls == []


def test_patch_invalid_source_url_blocks_other_fields(app_client, seeded_feed, _auth,
                                                      refresh_recorder, monkeypatch):
    """sourceUrl is validated before update_podcast runs; a bad URL must not
    let sibling fields in the same PATCH persist."""
    _mock_fetch(monkeypatch, None)
    slug = seeded_feed['slug']
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'sourceUrl': NEW_URL, 'daiPlatform': 'megaphone'},
                         headers=_csrf(app_client))
    assert r.status_code == 400
    podcast = seeded_feed['db'].get_podcast_by_slug(slug)
    assert podcast.get('dai_platform') != 'megaphone'


def test_patch_source_url_refresh_failure_still_200(app_client, seeded_feed, _auth,
                                                    monkeypatch):
    _mock_fetch(monkeypatch, VALID_RSS)

    def _boom(slug, feed_url, force=False):
        raise RuntimeError('refresh exploded')

    monkeypatch.setattr('main_app.feeds.refresh_rss_feed', _boom)
    slug = seeded_feed['slug']
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'sourceUrl': NEW_URL}, headers=_csrf(app_client))
    assert r.status_code == 200
    assert seeded_feed['db'].get_podcast_by_slug(slug)['source_url'] == NEW_URL


def test_get_feed_roundtrip_shows_new_source_url(app_client, seeded_feed, _auth,
                                                 refresh_recorder, monkeypatch):
    _mock_fetch(monkeypatch, VALID_RSS)
    slug = seeded_feed['slug']
    app_client.patch(f'/api/v1/feeds/{slug}',
                     json={'sourceUrl': NEW_URL}, headers=_csrf(app_client))
    body = app_client.get(f'/api/v1/feeds/{slug}').get_json()
    assert body['sourceUrl'] == NEW_URL


def test_zero_episode_feed_accepted(app_client, seeded_feed, _auth,
                                    refresh_recorder, monkeypatch):
    """A feed with a channel title but no items is legitimate (new show)."""
    _mock_fetch(monkeypatch, EMPTY_RSS)
    slug = seeded_feed['slug']
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'sourceUrl': NEW_URL}, headers=_csrf(app_client))
    assert r.status_code == 200


def test_refresh_logs_url_at_info_without_query(app_client, seeded_feed, _auth,
                                                monkeypatch, caplog):
    """The refresh log line must be INFO-visible and must not leak query
    strings (private-feed tokens live there)."""
    from main_app.feeds import refresh_rss_feed
    monkeypatch.setattr('main_app.feeds.rss_parser.fetch_feed_conditional',
                        lambda url, etag=None, last_modified=None: (None, None, None))
    with caplog.at_level(logging.INFO, logger='podcast.refresh'):
        refresh_rss_feed(seeded_feed['slug'],
                         'https://example.com/feed.xml?key=SECRET', force=True)
    lines = [rec.message for rec in caplog.records
             if 'Starting RSS refresh from:' in rec.message]
    assert lines, 'expected an INFO-level refresh log line'
    assert 'https://example.com/feed.xml' in lines[0]
    assert 'SECRET' not in lines[0]
