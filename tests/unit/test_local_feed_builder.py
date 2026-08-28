"""Local feed RSS builder (Task 5): renders a served feed for a local
(imported-archive) podcast entirely from DB rows -- no upstream source.
"""
import json

import feedparser

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('local_feed_builder_test_', reset_storage=True)

import main_app.feeds as mf  # noqa: E402
from local_feed_builder import (  # noqa: E402
    _fetch_local_feed_episodes,
    build_local_feed_xml,
    rebuild_local_feed,
)

KEY = 'c' * 64
CHANNEL_GUID = '11111111-2222-3333-4444-555555555555'


def _seed(slug):
    mf.db.create_podcast(slug, f'local://{slug}', 'Archive Show', feed_type='local')
    mf.db.update_podcast(
        slug,
        description='An archive of old episodes.',
        author='Jane Host',
        explicit=1,
        categories=json.dumps(['Technology']),
        p20_channel_json=json.dumps({
            'guid': CHANNEL_GUID,
            'locked': 'yes',
            'medium': 'podcast',
        }),
    )

    # Older, unprocessed episode: no vtt/chapters, unversioned enclosure.
    mf.db.upsert_episode(
        slug, 's01e01', original_url='local://s01e01', title='Ep One',
        description='First episode', status='discovered',
        season_number=1, episode_number=1,
        published_at='2026-01-02T00:00:00Z',
    )

    # Newer, processed episode: has vtt/chapters, versioned enclosure.
    # upsert_episode's INSERT column list omits processed_version (it is
    # only ever set on an UPDATE), so the row is inserted first and then
    # updated, mirroring how the processing pipeline actually populates it.
    mf.db.upsert_episode(
        slug, 's01e02', original_url='local://s01e02', title='Ep Two',
        description='Second episode', status='processed',
        season_number=1, episode_number=2,
        new_duration=600, original_duration=650,
        published_at='2026-01-05T00:00:00Z',
    )
    mf.db.upsert_episode(slug, 's01e02', processed_version=3)
    mf.storage.save_transcript_vtt(slug, 's01e02', 'WEBVTT\n\n00:00.000 --> 00:01.000\nHi\n')
    mf.storage.save_chapters_json(slug, 's01e02', {'chapters': []})

    return mf.db.get_podcast_by_slug(slug)


def _render(slug):
    podcast = _seed(slug)
    episodes = _fetch_local_feed_episodes(mf.db, podcast['id'])
    return build_local_feed_xml(podcast, episodes, storage=mf.storage, db=mf.db)


def _reset_feed_auth():
    mf.db.set_setting('feed_auth_enabled', 'false')
    mf.db.set_setting('feed_auth_key', '')


def teardown_function(_fn):
    _reset_feed_auth()


def test_channel_metadata_parses_back():
    xml = _render('chan-parse')

    parsed = feedparser.parse(xml)
    assert parsed.bozo == 0
    assert parsed.feed.title == 'Archive Show'
    assert parsed.feed.description == 'An archive of old episodes.'
    assert parsed.feed.author == 'Jane Host'


def test_entries_newest_first_with_correct_enclosure_versioning():
    xml = _render('entry-order')

    parsed = feedparser.parse(xml)
    assert [e.title for e in parsed.entries] == ['Ep Two', 'Ep One']

    unprocessed_href = parsed.entries[1].enclosures[0]['href']
    assert unprocessed_href.endswith('/episodes/entry-order/s01e01.mp3')

    processed_href = parsed.entries[0].enclosures[0]['href']
    assert processed_href.endswith('-v3.mp3')


def test_feed_key_appears_on_enclosure_transcript_and_chapters():
    slug = 'with-key'
    podcast = _seed(slug)
    mf.db.set_setting('feed_auth_enabled', 'true')
    mf.db.set_setting('feed_auth_key', KEY)
    try:
        episodes = _fetch_local_feed_episodes(mf.db, podcast['id'])
        xml = build_local_feed_xml(podcast, episodes, storage=mf.storage, db=mf.db)
    finally:
        _reset_feed_auth()

    for entry in feedparser.parse(xml).entries:
        assert f'?key={KEY}' in entry.enclosures[0]['href']

    assert f'/episodes/{slug}/s01e02.vtt?key={KEY}' in xml
    assert f'/episodes/{slug}/s01e02/chapters.json?key={KEY}' in xml


def test_guids_are_episode_ids_not_permalinks():
    xml = _render('guid-check')

    assert '<guid isPermaLink="false">s01e01</guid>' in xml
    assert '<guid isPermaLink="false">s01e02</guid>' in xml


def test_channel_guid_matches_stored_p20_guid_and_ai_content_present():
    xml = _render('pc2-check')

    assert f'<podcast:guid>{CHANNEL_GUID}</podcast:guid>' in xml
    assert '<podcast:txt purpose="ai-content">true</podcast:txt>' in xml


def test_rebuild_local_feed_persists_and_reads_back():
    slug = 'roundtrip'
    podcast = _seed(slug)

    assert rebuild_local_feed(slug, podcast) is True

    saved = mf.storage.get_rss(slug)
    assert saved is not None
    parsed = feedparser.parse(saved)
    assert parsed.bozo == 0
    assert parsed.feed.title == 'Archive Show'
    assert len(parsed.entries) == 2
