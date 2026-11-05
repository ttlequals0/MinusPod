"""The marker means "this episode's published cut came out of a run with ad
detection on". It is decided per episode from the run record, never from the
feed's current mode (which changes over time)."""
import xml.etree.ElementTree as ET
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('marker_run_mode_test_', reset_storage=True)
import main_app.feeds as feeds  # noqa: E402
from main_app import db  # noqa: E402
from rss_parser import RSSParser  # noqa: E402

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


def _run(title, stats, status='completed'):
    pod = db.get_podcast_by_slug(SLUG)
    db.record_processing_history(pod['id'], SLUG, 'Run Mode', IDS[title], title,
                                 status, processing_stats=stats)


def _titles():
    saved = {}
    with patch.object(feeds.storage, 'save_rss', lambda s, r: saved.update(rss=r)):
        feeds._build_and_save_served_rss(
            SLUG, UP, parser.parse_feed(UP, source=SLUG), db.get_podcast_by_slug(SLUG))
    root = ET.fromstring(saved['rss'])
    return [i.findtext('title') for i in root.iter('item')]


def test_feed_mode_is_irrelevant_the_published_run_decides():
    _run('Alpha', {'mode': 'auto'})                                # real ad removal
    _run('Beta', {'mode': 'auto', 'detection_skipped': True})     # served unmodified
    db.update_podcast(SLUG, skip_ad_detection=1)                   # feed temporarily skipping
    assert _titles() == ['[a] Alpha', 'Beta']
    db.update_podcast(SLUG, skip_ad_detection=0, passthrough_enabled=0)   # back to standard
    assert _titles() == ['[a] Alpha', 'Beta']                      # Beta stays unmarked


def test_passthrough_run_is_unmarked_even_on_a_standard_feed():
    _run('Alpha', {'mode': 'passthrough'})
    _run('Beta', {'mode': 'auto'})
    assert _titles() == ['Alpha', '[a] Beta']


def test_latest_completed_run_wins_and_failed_or_chapters_runs_do_not_count():
    _run('Alpha', {'mode': 'auto', 'detection_skipped': True})
    _run('Alpha', {'mode': 'llm'})                                 # later redetect: detection on
    _run('Alpha', {'mode': 'auto', 'detection_skipped': True}, status='failed')
    _run('Alpha', {'mode': 'chapters', 'detection_skipped': True})
    assert _titles()[0] == '[a] Alpha'


def test_youtube_sponsorblock_passthrough_is_unmarked():
    _run('Alpha', {'mode': 'auto', 'sponsorblock': {'mode': 'passthrough'}})
    assert _titles()[0] == 'Alpha'


def test_no_run_record_falls_back_to_the_episode_override_flag():
    assert _titles() == ['[a] Alpha', '[a] Beta']                  # legacy row, no evidence: marked
    with db.get_connection() as conn:
        conn.execute("UPDATE episodes SET passthrough_enabled=1 WHERE episode_id=?", (IDS['Beta'],))
    assert _titles() == ['[a] Alpha', 'Beta']


def test_unmodified_set_lookup():
    _run('Alpha', {'mode': 'passthrough'})
    pod = db.get_podcast_by_slug(SLUG)
    assert db.get_unmodified_episode_ids([pod['id']], list(IDS.values())) == {(pod['id'], IDS['Alpha'])}


def test_recents_uses_the_same_per_episode_rule():
    import recents_feed
    _run('Alpha', {'mode': 'auto', 'detection_skipped': True})
    _run('Beta', {'mode': 'auto'})
    pod = db.get_podcast_by_slug(SLUG)
    db.update_podcast(SLUG, skip_ad_detection=1)
    eps = [{'episode_id': IDS[t], 'podcast_id': pod['id'], 'title': t, 'source_slug': SLUG,
            'source_title': 'Show', 'source_feed_type': 'subscribed', 'source_processed_title_marker': None,
            'source_chapters_in_notes': None, 'chapters_json': None, 'has_transcript_vtt': 0}
           for t in ('Alpha', 'Beta')]
    captured = []
    with patch.object(recents_feed, '_append_local_episode_item',
                      lambda lines, slug, ep, b, st, k, **kw: captured.append(kw['title_prefix'])):
        recents_feed.build_recents_feed_xml(
            {'title': 'Recents', 'description': ''}, eps,
            storage=MagicMock(has_artwork=lambda s: False), db=db)
    assert captured == ['Show: ', '[a] Show: ']
