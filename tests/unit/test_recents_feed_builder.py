"""Served /recents RSS (#721): items from every source feed, each pointing at
its source feed's URLs, cut off by publish date."""
import feedparser
import pytest

from tests.app_bootstrap import bootstrap

bootstrap('recents_builder_test_')

import main_app.feeds as mf  # noqa: E402
from database.podcasts import RECENTS_SLUG, recents_cutoff  # noqa: E402
from recents_feed import build_recents_feed_xml, rebuild_recents_feed  # noqa: E402


def _seed():
    mf.db.create_podcast('alpha', 'https://example.com/alpha.xml', 'Alpha Show')
    mf.db.create_podcast('beta', 'local://beta', 'Beta Archive', feed_type='local')
    mf.db.upsert_episode('alpha', 'aaaaaaaaaaa2', original_url='https://example.com/a2.mp3',
                         title='Alpha two', description='<p>a2</p>', status='processed',
                         published_at='2026-09-10T00:00:00Z', processed_file='/a2.mp3',
                         new_duration=100)
    mf.db.upsert_episode('alpha', 'aaaaaaaaaaa2', processed_version=2)
    mf.db.upsert_episode('alpha', 'aaaaaaaaaaa1', original_url='https://example.com/a1.mp3',
                         title='Alpha one', status='processed',
                         published_at='2026-01-01T00:00:00Z', processed_file='/a1.mp3')
    mf.db.upsert_episode('beta', 's01e01', original_url='local://s01e01', title='Beta one',
                         status='processed', published_at='2026-09-11T00:00:00Z',
                         processed_file='/b1.mp3')
    mf.db.create_podcast(RECENTS_SLUG, 'recents://', 'My Recents', feed_type='recents')
    mf.db.update_podcast(RECENTS_SLUG, description='Everything new')
    mf.db.get_connection().execute(
        "UPDATE podcasts SET created_at = '2026-09-05T00:00:00Z' WHERE slug = ?", (RECENTS_SLUG,))
    mf.db.get_connection().commit()
    return mf.db.get_podcast_by_slug(RECENTS_SLUG)


@pytest.fixture
def recents():
    yield _seed()
    for slug in ('alpha', 'beta', RECENTS_SLUG):
        mf.db.delete_podcast(slug)
    mf.db.set_setting('chapters_in_notes', 'false', is_default=True)


def _rows(podcast):
    return mf.db.get_recent_processed_episodes(recents_cutoff(podcast))


def test_items_come_from_every_source_and_point_at_source_urls(recents):
    xml = build_recents_feed_xml(recents, _rows(recents), storage=mf.storage, db=mf.db)
    feed = feedparser.parse(xml)
    assert feed.feed.title == 'My Recents'
    assert feed.feed.description == 'Everything new'
    assert [e.title for e in feed.entries] == ['Beta Archive: Beta one', 'Alpha Show: Alpha two']
    assert 'Alpha one' not in xml
    enclosures = [e.enclosures[0].href for e in feed.entries]
    assert enclosures[0].endswith('/episodes/beta/s01e01.mp3')
    assert enclosures[1].endswith('/episodes/alpha/aaaaaaaaaaa2-v2.mp3')
    assert '/episodes/recents/' not in xml


def test_transcript_and_chapter_tags_come_from_the_row(recents):
    mf.storage.save_transcript_vtt('alpha', 'aaaaaaaaaaa2', 'WEBVTT\n')
    mf.storage.save_chapters_json('alpha', 'aaaaaaaaaaa2', {'version': '1.2.0', 'chapters': [
        {'startTime': 0, 'title': 'Intro'}]})
    xml = build_recents_feed_xml(recents, _rows(recents), storage=mf.storage, db=mf.db)
    assert '/episodes/alpha/aaaaaaaaaaa2.vtt' in xml
    assert '/episodes/alpha/aaaaaaaaaaa2/chapters.json' in xml
    assert '/episodes/beta/s01e01.vtt' not in xml


def test_chapter_block_follows_the_global_setting(recents):
    mf.storage.save_chapters_json('alpha', 'aaaaaaaaaaa2', {'version': '1.2.0', 'chapters': [
        {'startTime': 0, 'title': 'Intro'}]})
    assert 'Chapters</p>' not in build_recents_feed_xml(recents, _rows(recents), storage=mf.storage, db=mf.db)
    mf.db.set_setting('chapters_in_notes', 'true', is_default=False)
    xml = build_recents_feed_xml(recents, _rows(recents), storage=mf.storage, db=mf.db)
    assert '<p>a2</p><p>Chapters</p><p>00:00 Intro</p>' in xml


def test_rebuild_persists_the_cached_rss_and_refresh_routes_to_it(recents):
    assert rebuild_recents_feed(recents) is True
    assert 'Beta one' in mf.storage.get_rss(RECENTS_SLUG)
    mf.storage.save_rss(RECENTS_SLUG, 'stale')
    assert mf.refresh_rss_feed(RECENTS_SLUG, 'recents://', force=True) is True
    assert 'Beta one' in mf.storage.get_rss(RECENTS_SLUG)
    mf.storage.save_rss(RECENTS_SLUG, 'stale')
    assert mf.rebuild_served_rss(RECENTS_SLUG) is True
    assert 'stale' not in mf.storage.get_rss(RECENTS_SLUG)


def test_source_episode_completion_rebuilds_the_recents_feed(recents):
    mf.storage.save_rss(RECENTS_SLUG, 'stale')
    mf.invalidate_feed_cache()
    from main_app import processing
    processing._refresh_rss_for_slug('alpha', 'aaaaaaaaaaa2')
    assert 'stale' not in (mf.storage.get_rss(RECENTS_SLUG) or 'stale')
