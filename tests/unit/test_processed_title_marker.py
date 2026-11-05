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
    from rss_parser import episode_title_for_feed
    assert episode_title_for_feed('[a] Alpha', '[a] ', {}) == '[a] [a] Alpha'


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


def _enclosures(rss):
    return [i.find('enclosure').get('url') for i in ET.fromstring(rss).iter('item')]


def test_original_mode_leaves_processed_urls_alone_and_marks_unprocessed():
    """Flipping a feed to original must not touch the back catalogue's
    enclosure URLs; only unprocessed items get the distinct -orig form."""
    parser = RSSParser()
    feed = parser.parse_feed(UP, source=SLUG)
    ids = [parser.generate_episode_id(e.enclosures[0]['href'], e.get('id')) for e in feed.entries]

    def render(mode, version=0):
        extra = [{'episode_id': ids[0], 'processed_size_bytes': 5, 'new_duration': 10,
                  'processed_version': version}]
        return _enclosures(parser.modify_feed(
            UP, SLUG, storage=MagicMock(has_artwork=lambda s: False),
            extra_episodes=extra, unprocessed_playback=mode))

    process = render('process')
    original = render('original')
    assert process[0].endswith(f'/{ids[0]}.mp3') and process[1].endswith(f'/{ids[1]}.mp3')
    assert original[0] == process[0]                       # processed: identical in every mode
    assert render('original', 2)[0] == render('process', 2)[0]
    assert original[1].endswith(f'/{ids[1]}-orig.mp3')     # unprocessed: distinct, stable form
    assert render('original')[1] == original[1]


def test_db_appended_processed_item_url_is_mode_independent():
    parser = RSSParser()
    urls = []
    for _ in range(2):
        lines = []
        parser._append_db_episode_item(lines, SLUG, {'episode_id': 'x1', 'title': 'Old',
                                                     'processed_version': 1},
                                       MagicMock())
        urls.append([l for l in lines if '<enclosure' in l][0])
    assert urls[0] == urls[1] and '/x1-v1.mp3' in urls[0]


def test_orig_url_form_is_served_like_the_plain_url(feed):
    from unittest.mock import patch
    import tempfile, os
    eid = 'abc123def456'
    db.set_setting('unprocessed_playback', 'original', is_default=False)
    fm = {SLUG: {'in': 'x', 'out': SLUG}}
    ep = {'url': 'https://cdn.example.com/a.mp3', 'title': 'A', 'description': '',
          'artwork_url': None, 'published': None}
    try:
        with patch('main_app.routes.get_feed_map', return_value=fm), \
             patch('main_app.routes._lookup_episode', return_value=(ep, 'Show')), \
             patch('main_app.processing.start_background_processing') as start, \
             app.test_client() as c:
            r = c.get(f'/episodes/{SLUG}/{eid}-orig.mp3')
            assert r.status_code == 302 and r.headers['Location'] == ep['url']
            start.assert_not_called()
            db.upsert_episode(SLUG, eid, original_url=ep['url'], title='A', status='processed')
            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
                f.write(b'ID3')
            with patch('main_app.routes._published_processed_path', return_value=f.name):
                assert c.get(f'/episodes/{SLUG}/{eid}-orig.mp3').status_code == 200
            os.unlink(f.name)
    finally:
        db.set_setting('unprocessed_playback', 'process', is_default=False)


def _recents(eps):
    import recents_feed
    from unittest.mock import patch
    captured = []
    with patch.object(recents_feed, '_append_local_episode_item',
                      lambda lines, slug, e, b, st, k, **kw: captured.append(kw['title_prefix'])):
        recents_feed.build_recents_feed_xml(
            {'title': 'Recents', 'description': ''}, eps,
            storage=MagicMock(has_artwork=lambda s: False), db=db)
    return captured


def _rec_ep(slug, i=1, **extra):
    return {'episode_id': f'r{i}', 'podcast_id': 0, 'title': 'T', 'source_slug': slug,
            'source_title': 'Show', 'source_chapters_in_notes': None,
            'chapters_json': None, 'has_transcript_vtt': 0, **extra}


def test_recents_feed_applies_per_feed_marker(feed):
    db.set_setting('processed_title_marker', '[g] ', is_default=False)
    for src_marker, expected in ((None, '[g] Show: '), ('[b] ', '[b] Show: '), ('', 'Show: ')):
        db.update_podcast(SLUG, processed_title_marker=src_marker)
        assert _recents([_rec_ep(SLUG)]) == [expected]


def _processed_alpha(parser):
    entry = parser.parse_feed(UP, source=SLUG).entries[0]
    eid = parser.generate_episode_id(entry.enclosures[0]['href'], entry.get('id'))
    db.upsert_episode(SLUG, eid, original_url=entry.enclosures[0]['href'],
                      title='Alpha', status='processed')
    with db.get_connection() as conn:
        conn.execute("UPDATE episodes SET processed_file='x.mp3' WHERE episode_id=?", (eid,))
    return eid


def _served_titles(c):
    return _titles(c.get(f'/{SLUG}').get_data(as_text=True))[0]


def test_global_change_hands_feeds_to_the_refresher_without_fetching(feed):
    """The PUT must not block on upstream fetches: it marks every feed due
    (etag cleared, attempt stamp reset), rebuilds Recents from the DB, and the
    scheduler's normal refresh then renders the new titles."""
    from unittest.mock import patch
    parser = RSSParser()
    _processed_alpha(parser)
    import main_app.feeds as feeds
    fm = {SLUG: {'in': 'https://example.com/feed.xml', 'out': SLUG}}
    db.update_podcast_etag(SLUG, 'etag-1', 'lm-1')
    with db.get_connection() as conn:
        conn.execute("UPDATE podcasts SET last_refresh_attempt_at='2999-01-01T00:00:00Z' WHERE slug=?", (SLUG,))
    with patch('main_app.feeds.get_feed_map', return_value=fm), \
         patch('main_app.routes.get_feed_map', return_value=fm), \
         patch('main_app.feeds.rss_parser.fetch_feed', return_value=UP) as fetch, \
         patch('main_app.feeds.rss_parser.fetch_feed_conditional',
               return_value=(UP, None, None)) as fetch_cond, \
         patch('main_app.feeds.rebuild_served_rss') as rebuild, \
         app.test_client() as c:
        assert c.put('/api/v1/settings/ad-detection',
                     json={'processedTitleMarker': '[a] '}).status_code == 200
        fetch.assert_not_called()                    # no upstream fetch in the request
        fetch_cond.assert_not_called()
        rebuild.assert_called_once_with('recents')   # Recents is pure DB, rebuilt now
        row = db.get_podcast_by_slug(SLUG)
        assert row.get('etag') is None and row.get('last_refresh_attempt_at') is None
        assert SLUG in db.get_due_feed_slugs(900, 50)
        feeds.refresh_due_feeds(50, 900)
        assert _served_titles(c) == ['[a] Alpha', 'Beta']


def test_effective_default_compare_means_unrelated_saves_do_not_rebuild(feed):
    from unittest.mock import patch
    with db.get_connection() as conn:
        conn.execute("DELETE FROM settings WHERE key IN ('unprocessed_playback','processed_title_marker')")
    with patch('main_app.feeds.on_served_rss_settings_changed') as schedule, \
         app.test_client() as c:
        # unset == 'process' / '' : saving the defaults is not a change
        c.put('/api/v1/settings/ad-detection', json={'unprocessedPlayback': 'process',
                                                     'processedTitleMarker': ''})
        schedule.assert_not_called()
        c.put('/api/v1/settings/ad-detection', json={'processedTitleMarker': '[b] '})
        schedule.assert_called_once()
        schedule.reset_mock()
        c.put('/api/v1/settings/ad-detection', json={'processedTitleMarker': '[b] '})
        schedule.assert_not_called()


def test_config_import_of_global_fields_schedules_the_same_rerender(temp_db, monkeypatch):
    from unittest.mock import patch
    import config_transfer
    doc = {'format': config_transfer.FORMAT, 'formatVersion': config_transfer.FORMAT_VERSION,
           'appVersion': '2.98.2', 'exportedAt': '2026-10-08T00:00:00+00:00',
           'containsSecrets': True, 'feeds': [],
           'settings': {'processed_title_marker': '[a] ', 'unprocessed_playback': 'original'}}
    monkeypatch.setattr(config_transfer, '_after_feed_commit', lambda *_a: [])
    preview = config_transfer.build_preview(temp_db, doc, 'global')
    with patch('main_app.feeds.on_served_rss_settings_changed') as schedule:
        config_transfer.apply_config(temp_db, doc, 'global', None, preview['previewToken'])
    schedule.assert_called_once()
    assert temp_db.get_setting('processed_title_marker') == '[a] '


def test_feed_patch_of_marker_or_playback_hands_off_without_blocking(feed):
    from unittest.mock import patch
    with patch('main_app.feeds.refresh_rss_feed') as sync_refresh, \
         patch('main_app.routes._kick_background_refresh') as kick, \
         patch('main_app.feeds.rebuild_served_rss') as rebuild, \
         app.test_client() as c:
        for body in ({'processedTitleMarker': '[b] '}, {'unprocessedPlayback': 'original'}):
            kick.reset_mock()
            rebuild.reset_mock()
            assert c.patch(f'/api/v1/feeds/{SLUG}', json=body).status_code == 200
            sync_refresh.assert_not_called()          # no upstream fetch in the request
            kick.assert_called_once_with(SLUG, 'https://example.com/feed.xml', force=True)
            rebuild.assert_called_once_with('recents')
        assert db.get_podcast_by_slug(SLUG).get('etag') is None


def test_recents_resolves_global_once_and_matches_each_source_feeds_rule(feed):
    from unittest.mock import patch
    db.set_setting('processed_title_marker', '[g] ', is_default=False)
    db.create_podcast('rec-local', 'local://rec-local', 'L', feed_type='local')
    db.create_podcast('rec-yt', 'https://youtube.com/@x', 'Y', feed_type='youtube')
    try:
        eps = [_rec_ep(SLUG, 1), _rec_ep(SLUG, 2), _rec_ep('rec-local', 3), _rec_ep('rec-yt', 4)]
        real_get = db.get_setting
        calls = []
        def spy(key, *a, **k):
            calls.append(key)
            return real_get(key, *a, **k)
        with patch.object(db, 'get_setting', spy):
            captured = _recents(eps)
        assert captured == ['[g] Show: ', '[g] Show: ', 'Show: ', 'Show: ']
        assert calls.count('processed_title_marker') == 1
    finally:
        db.delete_podcast('rec-local')
        db.delete_podcast('rec-yt')


def test_title_resolver_is_the_single_rule_for_feed_and_db_items():
    from rss_parser import episode_title_for_feed
    assert episode_title_for_feed('[a] Alpha', '[a] ', {}) == '[a] [a] Alpha'
    assert episode_title_for_feed('Alpha', '', {}) == 'Alpha'
    assert episode_title_for_feed('Alpha', None, {'ad_detection_ran': 1}) == 'Alpha'
    assert episode_title_for_feed('Alpha', '[a] ', {'ad_detection_ran': 0}) == 'Alpha'


@pytest.mark.parametrize('char,name', [
    ('\u0085', 'NEL (Cc)'), ('\u200b', 'zero-width space (Cf)'),
    ('\u2028', 'line separator (Zl)'), ('\u2029', 'paragraph separator (Zp)'),
    ('\u202e', 'right-to-left override (Cf)'), ('\ufeff', 'BOM (Cf)'),
    ('\x07', 'bell (Cc)'), ('\x7f', 'DEL (Cc)'),
])
def test_validator_rejects_unicode_control_format_and_separator_chars(char, name):
    assert validate_processed_title_marker(f'[a]{char}')[1], name
    assert validate_processed_title_marker(f'{char}[a] ')[1], name


def test_validator_keeps_ordinary_markers_and_rejects_unicode_blank():
    for ok in ('[a] ', '\u2705 ', '(cleaned) ', '\u00e9 '):
        assert validate_processed_title_marker(ok) == (ok, None), ok
    for blank in (' ', '\u00a0', '\u3000', '\u2003 '):
        assert validate_processed_title_marker(blank)[1], repr(blank)


def test_settings_and_feed_apis_reject_a_hidden_character_marker(feed):
    with app.test_client() as c:
        assert c.put('/api/v1/settings/ad-detection',
                     json={'processedTitleMarker': '[a]\u200b'}).status_code == 400
        assert c.patch(f'/api/v1/feeds/{SLUG}',
                       json={'processedTitleMarker': '[a]\u202e'}).status_code == 400
