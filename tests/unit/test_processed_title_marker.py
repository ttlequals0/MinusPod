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


def test_marker_added_once_per_render_and_not_stored():
    first = _titles(_render('[a] ', ['Alpha']))[0]
    second = _titles(_render('[a] ', ['Alpha']))[0]
    assert first == second == ['[a] Alpha', 'Beta']


def test_upstream_title_starting_with_marker_text_is_still_marked():
    parser = RSSParser()
    assert parser.apply_title_marker('[a] Alpha', '[a] ') == '[a] [a] Alpha'
    assert parser.apply_title_marker('Alpha', '') == 'Alpha'
    assert parser.apply_title_marker('Alpha', None) == 'Alpha'


def test_channel_title_override_still_applies_and_is_not_prefixed():
    titles, channel = _titles(_render('[a] ', ['Alpha'], title_override='Ad-free Show'))
    assert channel == 'Ad-free Show'
    assert titles == ['[a] Alpha', 'Beta']


def test_db_appended_processed_item_gets_marker():
    parser = RSSParser()
    lines = []
    parser._append_db_episode_item(lines, SLUG, {'episode_id': 'abc123def456', 'title': 'Old'},
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
    assert validate_processed_title_marker('   ')[1]
    assert validate_processed_title_marker(' ')[1]
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


def test_original_mode_changes_enclosure_url_of_processed_items_only():
    """Apps that auto-downloaded the original must see a new URL once the
    item is processed, or they never re-fetch the ad-free file."""
    def enclosures(rss):
        return [i.find('enclosure').get('url') for i in ET.fromstring(rss).iter('item')]

    parser = RSSParser()
    feed = parser.parse_feed(UP, source=SLUG)
    eid = parser.generate_episode_id(feed.entries[0].enclosures[0]['href'], feed.entries[0].get('id'))

    def render(mode, version):
        extra = [{'episode_id': eid, 'processed_size_bytes': 5, 'new_duration': 10,
                  'processed_version': version}]
        return enclosures(parser.modify_feed(
            UP, SLUG, storage=MagicMock(has_artwork=lambda s: False),
            extra_episodes=extra, unprocessed_playback=mode))

    default = render('process', 0)
    assert default[0].endswith(f'/{eid}.mp3')          # today's URL, untouched
    unprocessed_form = default[0]
    v0 = render('original', 0)
    v1 = render('original', 1)
    assert v0[0].endswith(f'/{eid}-v1.mp3') and v0[0] != unprocessed_form
    assert v1[0].endswith(f'/{eid}-v2.mp3') and v1[0] != v0[0]   # reprocess changes it again
    assert v0[1] == default[1]                                    # unprocessed item unchanged


def test_original_mode_versioned_url_is_served(feed):
    from unittest.mock import patch
    import tempfile, os
    db.upsert_episode(SLUG, 'abc123def456', original_url='https://cdn.example.com/a.mp3',
                      title='A', status='processed')
    with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
        f.write(b'ID3')
        path = f.name
    try:
        with patch('main_app.routes.get_feed_map',
                   return_value={SLUG: {'in': 'x', 'out': SLUG}}), \
             patch('main_app.routes._published_processed_path', return_value=path) as pp, \
             app.test_client() as c:
            assert c.get(f'/episodes/{SLUG}/abc123def456-v1.mp3').status_code == 200
            assert pp.call_args[0][3] == 1
    finally:
        os.unlink(path)


def test_recents_feed_applies_per_feed_marker(feed):
    import recents_feed
    db.set_setting('processed_title_marker', '[g] ', is_default=False)
    ep = {'episode_id': 'r1', 'title': 'T', 'source_slug': SLUG, 'source_title': 'Show',
          'source_processed_title_marker': None, 'source_chapters_in_notes': None,
          'chapters_json': None, 'has_transcript_vtt': 0}
    captured = []
    def fake_item(lines, slug, ep, base, storage, key, **kw):
        captured.append(kw['title_prefix'])
    from unittest.mock import patch
    podcast = {'title': 'Recents', 'description': ''}
    for src_marker, expected in ((None, '[g] Show: '), ('[b] ', '[b] Show: '), ('', 'Show: ')):
        captured.clear()
        ep['source_processed_title_marker'] = src_marker
        with patch.object(recents_feed, '_append_local_episode_item', fake_item):
            recents_feed.build_recents_feed_xml(podcast, [ep], storage=MagicMock(has_artwork=lambda s: False), db=db)
        assert captured == [expected]
