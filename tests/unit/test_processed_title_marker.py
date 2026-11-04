"""processed_title_marker: prefix on <item><title> of processed episodes."""
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('processed_marker_test_', reset_storage=True)
from config import (  # noqa: E402
    PROCESSED_TITLE_MARKER_MAX_LENGTH, resolve_processed_title_marker,
    validate_processed_title_marker,
)
from main_app import app, db  # noqa: E402
from rss_parser import RSSParser  # noqa: E402

SLUG = 'marker-feed'
UP = '''<?xml version="1.0"?><rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel><title>Show</title><link>https://x.example</link><description>d</description>
<item><title>Alpha</title><guid>g1</guid><pubDate>Mon, 02 Nov 2026 10:00:00 GMT</pubDate>
<enclosure url="https://cdn.example.com/a.mp3" type="audio/mpeg" length="1"/></item>
<item><title>Beta</title><guid>g2</guid><pubDate>Sun, 01 Nov 2026 10:00:00 GMT</pubDate>
<enclosure url="https://cdn.example.com/b.mp3" type="audio/mpeg" length="1"/></item>
</channel></rss>'''


def _titles(rss):
    root = ET.fromstring(rss)
    return [i.findtext('title') for i in root.iter('item')], root.find('channel').findtext('title')


def _render(marker, processed_titles=(), **kw):
    parser = RSSParser()
    feed = parser.parse_feed(UP, source=SLUG)
    ids = {}
    for entry in feed.entries:
        eid = parser.generate_episode_id(entry.enclosures[0]['href'], entry.get('id'))
        ids[entry.title] = eid
    extra = [{'episode_id': ids[t], 'processed_size_bytes': 5, 'new_duration': 10}
             for t in processed_titles]
    return parser.modify_feed(UP, SLUG, storage=MagicMock(has_artwork=lambda s: False),
                              extra_episodes=extra, processed_title_marker=marker, **kw)


def test_off_by_default_changes_nothing():
    assert _titles(_render('', ['Alpha']))[0] == ['Alpha', 'Beta']
    assert _titles(_render(None, ['Alpha']))[0] == ['Alpha', 'Beta']


def test_marker_on_processed_only():
    titles, channel = _titles(_render('[a] ', ['Alpha']))
    assert titles == ['[a] Alpha', 'Beta']
    assert channel == 'Show'


def test_marker_not_doubled():
    parser = RSSParser()
    assert parser.apply_title_marker('[a] Alpha', '[a] ') == '[a] Alpha'
    assert parser.apply_title_marker('Alpha', '[a] ') == '[a] Alpha'
    assert parser.apply_title_marker('Alpha', '') == 'Alpha'


def test_channel_title_override_still_applies_and_is_not_prefixed():
    titles, channel = _titles(_render('[a] ', ['Alpha'], title_override='Ad-free Show'))
    assert channel == 'Ad-free Show'
    assert titles == ['[a] Alpha', 'Beta']


def test_db_appended_processed_item_gets_marker():
    parser = RSSParser()
    lines = []
    parser._append_db_episode_item(lines, SLUG, {'episode_id': 'e1', 'title': 'Old'},
                                   MagicMock(), processed_title_marker='[a] ')
    assert '<title>[a] Old</title>' in '\n'.join(lines)


def test_marker_is_xml_escaped():
    titles, _ = _titles(_render('<a> ', ['Alpha']))
    assert titles[0] == '<a> Alpha'


def test_validator():
    assert validate_processed_title_marker('[a] ') == ('[a] ', None)
    assert validate_processed_title_marker('') == ('', None)
    assert validate_processed_title_marker('x' * PROCESSED_TITLE_MARKER_MAX_LENGTH)[1] is None
    assert validate_processed_title_marker('x' * (PROCESSED_TITLE_MARKER_MAX_LENGTH + 1))[1]
    assert PROCESSED_TITLE_MARKER_MAX_LENGTH == 16
    assert validate_processed_title_marker('a\nb')[1]
    assert validate_processed_title_marker(5)[1]


def test_resolver_per_feed_wins_including_explicit_off():
    db.set_setting('processed_title_marker', '[g] ', is_default=False)
    try:
        assert resolve_processed_title_marker(db, {}) == '[g] '
        assert resolve_processed_title_marker(db, {'processed_title_marker': '[a] '}) == '[a] '
        assert resolve_processed_title_marker(db, {'processed_title_marker': ''}) == ''
        assert resolve_processed_title_marker(db, {'processed_title_marker': None}) == '[g] '
    finally:
        db.set_setting('processed_title_marker', '', is_default=False)
    assert resolve_processed_title_marker(db, None) == ''


@pytest.fixture
def feed():
    db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Marker Test')
    db.set_setting('processed_title_marker', '', is_default=False)
    yield
    db.delete_podcast(SLUG)
    db.set_setting('processed_title_marker', '', is_default=False)


def test_served_rss_flips_on_status_change(feed, monkeypatch):
    """Rebuilding the served RSS (what completion's forced refresh does)
    picks the marker up the moment the status becomes processed."""
    import main_app.feeds as feeds
    saved = {}
    monkeypatch.setattr(feeds.storage, 'save_rss', lambda slug, rss: saved.update(rss=rss))
    db.set_setting('processed_title_marker', '[a] ', is_default=False)
    podcast = db.get_podcast_by_slug(SLUG)
    parser = RSSParser()
    parsed = parser.parse_feed(UP, source=SLUG)
    feeds._build_and_save_served_rss(SLUG, UP, parsed, podcast)
    assert _titles(saved['rss'])[0] == ['Alpha', 'Beta']

    entry = parsed.entries[0]
    eid = parser.generate_episode_id(entry.enclosures[0]['href'], entry.get('id'))
    db.upsert_episode(SLUG, eid, original_url=entry.enclosures[0]['href'],
                      title='Alpha', status='processed')
    with db.get_connection() as conn:
        conn.execute("UPDATE episodes SET processed_file='x.mp3' WHERE episode_id=?", (eid,))
    feeds._build_and_save_served_rss(SLUG, UP, parser.parse_feed(UP, source=SLUG), podcast)
    assert _titles(saved['rss'])[0] == ['[a] Alpha', 'Beta']

    # Per-feed explicit empty override turns it off for this feed only.
    db.update_podcast(SLUG, processed_title_marker='')
    feeds._build_and_save_served_rss(SLUG, UP, parser.parse_feed(UP, source=SLUG),
                                     db.get_podcast_by_slug(SLUG))
    assert _titles(saved['rss'])[0] == ['Alpha', 'Beta']


def test_settings_api_validates_and_clears_etags(feed):
    with app.test_client() as c:
        assert c.put('/api/v1/settings/ad-detection',
                     json={'processedTitleMarker': 'x' * 17}).status_code == 400
        assert c.put('/api/v1/settings/ad-detection',
                     json={'processedTitleMarker': '[a] '}).status_code == 200
        assert db.get_setting('processed_title_marker') == '[a] '
        bad = c.patch(f'/api/v1/feeds/{SLUG}', json={'processedTitleMarker': 'x' * 17})
        assert bad.status_code == 400
        ok = c.patch(f'/api/v1/feeds/{SLUG}', json={'processedTitleMarker': '[b] '})
        assert ok.status_code == 200
        assert ok.get_json()['processedTitleMarker'] == '[b] '
        cleared = c.patch(f'/api/v1/feeds/{SLUG}', json={'processedTitleMarker': None})
        assert cleared.get_json()['processedTitleMarker'] is None
