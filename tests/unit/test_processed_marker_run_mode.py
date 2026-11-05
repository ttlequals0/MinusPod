"""The marker means "this episode's published cut came out of a run with ad
detection on". It is a per-episode fact stamped when a run publishes
(episodes.ad_detection_ran), never read from the feed's current mode."""
import json
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('marker_run_mode_test_', reset_storage=True)
import main_app.feeds as feeds  # noqa: E402
from ad_yield import detection_suppressed  # noqa: E402
from main_app import db  # noqa: E402
from main_app.pipeline import finalize  # noqa: E402
from rss_parser import RSSParser, episode_title_for_feed, processed_title_prefix  # noqa: E402

SLUG = 'marker-runmode'
UP = '''<?xml version="1.0"?><rss version="2.0"><channel><title>Show</title>
<link>https://x.example</link><description>d</description>
<item><title>Alpha</title><guid>g1</guid><pubDate>Mon, 02 Nov 2026 10:00:00 GMT</pubDate>
<enclosure url="https://cdn.example.com/a.mp3" type="audio/mpeg" length="1"/></item>
<item><title>Beta</title><guid>g2</guid><pubDate>Sun, 01 Nov 2026 10:00:00 GMT</pubDate>
<enclosure url="https://cdn.example.com/b.mp3" type="audio/mpeg" length="1"/></item>
</channel></rss>'''

parser = RSSParser()
IDS = {}


@pytest.fixture(autouse=True)
def feed():
    db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Run Mode')
    db.set_setting('processed_title_marker', '[a] ', is_default=False)
    for entry in parser.parse_feed(UP, source=SLUG).entries:
        eid = parser.generate_episode_id(entry.enclosures[0]['href'], entry.get('id'))
        IDS[entry.title] = eid
        db.upsert_episode(SLUG, eid, original_url=entry.enclosures[0]['href'],
                          title=entry.title, status='processed')
        with db.get_connection() as conn:
            conn.execute("UPDATE episodes SET processed_file='x.mp3' WHERE episode_id=?", (eid,))
    yield
    db.delete_podcast(SLUG)
    db.set_setting('processed_title_marker', '', is_default=False)


def _stamp(title, ran):
    db.upsert_episode(SLUG, IDS[title], ad_detection_ran=ran)


def _titles():
    saved = {}
    with patch.object(feeds.storage, 'save_rss', lambda s, r: saved.update(rss=r)):
        feeds._build_and_save_served_rss(
            SLUG, UP, parser.parse_feed(UP, source=SLUG), db.get_podcast_by_slug(SLUG))
    root = ET.fromstring(saved['rss'])
    return [i.findtext('title') for i in root.iter('item')]


def test_the_stamp_decides_not_the_feeds_current_mode():
    _stamp('Alpha', 1)
    _stamp('Beta', 0)
    db.update_podcast(SLUG, skip_ad_detection=1)
    assert _titles() == ['[a] Alpha', 'Beta']
    db.update_podcast(SLUG, skip_ad_detection=0, passthrough_enabled=0)
    assert _titles() == ['[a] Alpha', 'Beta']


def test_unknown_stamp_counts_as_ran():
    assert _titles() == ['[a] Alpha', '[a] Beta']


def test_one_resolver_rule():
    assert processed_title_prefix('[a] ', {'ad_detection_ran': 1}) == '[a] '
    assert processed_title_prefix('[a] ', {'ad_detection_ran': None}) == '[a] '
    assert processed_title_prefix('[a] ', {'ad_detection_ran': 0}) == ''
    assert processed_title_prefix('', {'ad_detection_ran': 1}) == ''
    assert episode_title_for_feed('[a] X', '[a] ', {}) == '[a] [a] X'   # render-time only


@pytest.mark.parametrize('stats,suppressed', [
    ({'mode': 'auto'}, False),
    ({'mode': 'passthrough'}, True),
    ({'mode': 'auto', 'detection_skipped': True}, True),
    ({'mode': 'auto', 'sponsorblock': {'mode': 'passthrough'}}, True),
    ({'mode': 'auto', 'sponsorblock': {'mode': 'llm'}}, False),
    (None, False),
])
def test_publish_rule(stats, suppressed):
    assert detection_suppressed(stats) is suppressed


def test_finalize_stamps_the_column_from_the_run_stats():
    for stats, expected in (({'mode': 'auto'}, 1),
                            ({'mode': 'passthrough'}, 0),
                            ({'mode': 'auto', 'detection_skipped': True}, 0)):
        finalize._persist_episode_state(
            SLUG, IDS['Alpha'], 0, 0, 0, 10.0, 10.0, 0,
            ad_detection_ran=not detection_suppressed(stats))
        assert db.get_episode(SLUG, IDS['Alpha'])['ad_detection_ran'] == expected


def test_backfill_derives_existing_rows_once_from_history():
    pod = db.get_podcast_by_slug(SLUG)

    def run(title, stats, status='completed'):
        db.record_processing_history(pod['id'], SLUG, 'Run Mode', IDS[title], title,
                                     status, processing_stats=stats)
    run('Alpha', {'mode': 'auto', 'detection_skipped': True})
    run('Alpha', {'mode': 'llm'})                                  # later redetect: ran
    run('Alpha', {'mode': 'auto', 'detection_skipped': True}, status='failed')
    run('Alpha', {'mode': 'chapters', 'detection_skipped': True})  # not an audio run
    run('Beta', {'mode': 'passthrough'})
    with db.get_connection() as conn:
        conn.execute("UPDATE episodes SET ad_detection_ran=NULL")
        conn.execute("DELETE FROM schema_migrations WHERE name='backfill_episodes_ad_detection_ran_v1'")
        db._backfill_ad_detection_ran(conn)
    assert db.get_episode(SLUG, IDS['Alpha'])['ad_detection_ran'] == 1
    assert db.get_episode(SLUG, IDS['Beta'])['ad_detection_ran'] == 0
    # gated: a second run changes nothing
    _stamp('Alpha', 0)
    with db.get_connection() as conn:
        conn.execute("UPDATE episodes SET ad_detection_ran=NULL WHERE episode_id=?", (IDS['Alpha'],))
        db._backfill_ad_detection_ran(conn)
    assert db.get_episode(SLUG, IDS['Alpha'])['ad_detection_ran'] is None


def test_backfill_without_history_falls_back_to_the_override_flag():
    with db.get_connection() as conn:
        conn.execute("UPDATE episodes SET ad_detection_ran=NULL, passthrough_enabled=1 "
                     "WHERE episode_id=?", (IDS['Beta'],))
        conn.execute("UPDATE episodes SET ad_detection_ran=NULL WHERE episode_id=?", (IDS['Alpha'],))
        conn.execute("DELETE FROM schema_migrations WHERE name='backfill_episodes_ad_detection_ran_v1'")
        db._backfill_ad_detection_ran(conn)
    assert db.get_episode(SLUG, IDS['Alpha'])['ad_detection_ran'] == 1
    assert db.get_episode(SLUG, IDS['Beta'])['ad_detection_ran'] == 0


def test_recents_uses_the_same_stamp_and_source_row():
    import recents_feed
    _stamp('Alpha', 0)
    _stamp('Beta', 1)
    db.update_podcast(SLUG, skip_ad_detection=1)
    eps = [{**db.get_episode(SLUG, IDS[t]), 'source_slug': SLUG, 'source_title': 'Show',
            'source_chapters_in_notes': None, 'chapters_json': None, 'has_transcript_vtt': 0}
           for t in ('Alpha', 'Beta')]
    captured = []
    with patch.object(recents_feed, '_append_local_episode_item',
                      lambda lines, slug, ep, b, st, k, **kw: captured.append(kw['title_prefix'])):
        recents_feed.build_recents_feed_xml(
            {'title': 'Recents', 'description': ''}, eps,
            storage=MagicMock(has_artwork=lambda s: False), db=db)
    assert captured == ['Show: ', '[a] Show: ']
