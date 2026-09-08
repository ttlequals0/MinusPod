"""Ad chapters wired into the three chapter-producing paths.

Kept ad segments still in the served audio are published as their own
chapters so a chapter-aware player can skip them. This covers the pass-1
_generate_assets branches (generate, publisher-preserve) and the manual
regenerate-chapters endpoint.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('ad_chapters_pipeline_test_', reset_storage=True)

import chapters_generator
from ad_chapters import AdChapterConfig
from main_app import processing

AD_CFG = AdChapterConfig(
    enabled=True, categories={'sponsor': True}, include_held=False,
    title_format='[mp:{category}]', held_title_format='[mp:{category}?]',
    resume_title='Show', min_confidence=0.9)

KEPT_SPONSOR = [{'start': 900.0, 'end': 960.0, 'action_applied': 'keep',
                 'category': 'sponsor', 'confidence': 0.95, 'was_cut': False}]

AD_ENTRY = {'startTime': 900, 'title': '[mp:sponsor]', 'kind': 'ad',
            'category': 'sponsor'}
RESUME_ENTRY = {'startTime': 960, 'title': 'Show', 'kind': 'resume'}


def _db(chapters_mode=None, chapters_enabled=None, upstream_chapters_url=None):
    db = MagicMock()

    def get_setting(key):
        if key == 'chapters_enabled':
            return chapters_enabled
        if key == 'vtt_transcripts_enabled':
            return 'false'
        return None

    db.get_setting.side_effect = get_setting
    db.get_podcast_by_slug.return_value = {'chapters_mode': chapters_mode}
    db.get_episode.return_value = {'upstream_chapters_url': upstream_chapters_url}
    return db


def _run(monkeypatch, db, publisher_chapters, generator_chapters=None,
         markers=None, ad_config=AD_CFG, fetch_return=None,
         original_duration=None):
    """Drive the real _generate_assets with every IO seam mocked."""
    storage_mock = MagicMock()
    embed_mock = MagicMock()
    transcript_gen_class = MagicMock()
    transcript_gen_class.return_value.compute_final_segments.return_value = []
    transcript_gen_class.return_value.generate_text.return_value = None

    generator_class = MagicMock()
    generator_class.return_value.generate_chapters.return_value = (
        generator_chapters if generator_chapters is not None
        else {'version': '1.2.0', 'chapters': [{'startTime': 1, 'title': 'Intro'}]}
    )

    monkeypatch.setattr(processing, 'db', db)
    monkeypatch.setattr(processing, 'storage', storage_mock)
    monkeypatch.setattr(processing, 'probe_chapters',
                        MagicMock(return_value=publisher_chapters))
    monkeypatch.setattr(processing, 'embed_chapters', embed_mock)
    monkeypatch.setattr(processing, 'fetch_upstream_chapters',
                        MagicMock(return_value=fetch_return))
    monkeypatch.setattr(processing, 'get_replacement_duration', lambda: 2.0)
    monkeypatch.setattr(processing, 'resolve_ad_chapter_config',
                        lambda db, row, slug=None: ad_config)
    monkeypatch.setattr('transcript_generator.TranscriptGenerator', transcript_gen_class)
    monkeypatch.setattr(chapters_generator, 'ChaptersGenerator', generator_class)

    processing._generate_assets(
        'example-podcast', 'a1b2c3d4e5f6', segments=[], all_cuts=[],
        episode_description='desc', podcast_name='Pod', episode_title='Title',
        regenerate_chapters=True, audio_path='/tmp/fake-processed.mp3',
        audio_duration=3600.0, markers=markers,
        original_duration=original_duration,
    )
    return storage_mock, embed_mock, generator_class


def _saved_chapters(storage_mock):
    return storage_mock.save_chapters_and_applied_cuts.call_args.args[2]['chapters']


# ---------- generate path ----------

def test_generate_path_appends_ad_chapters_and_embeds(monkeypatch):
    storage_mock, embed_mock, generator_class = _run(
        monkeypatch, _db(chapters_mode='generate'), publisher_chapters=[],
        markers=KEPT_SPONSOR)

    generator_class.return_value.generate_chapters.assert_called_once()
    merged = _saved_chapters(storage_mock)
    assert merged == [{'startTime': 1, 'title': 'Intro'}, AD_ENTRY, RESUME_ENTRY]
    embed_mock.assert_called_once_with(
        '/tmp/fake-processed.mp3', merged, duration=3600.0)


def test_generate_path_with_empty_generation_still_saves_ad_only_list(monkeypatch):
    storage_mock, embed_mock, generator_class = _run(
        monkeypatch, _db(chapters_mode='generate'), publisher_chapters=[],
        generator_chapters={'version': '1.2.0', 'chapters': []},
        markers=KEPT_SPONSOR)

    merged = _saved_chapters(storage_mock)
    assert merged == [AD_ENTRY, RESUME_ENTRY]
    embed_mock.assert_called_once_with(
        '/tmp/fake-processed.mp3', merged, duration=3600.0)


def test_generate_path_without_ads_saves_topics_only(monkeypatch):
    storage_mock, embed_mock, generator_class = _run(
        monkeypatch, _db(chapters_mode='generate'), publisher_chapters=[],
        markers=[])

    assert _saved_chapters(storage_mock) == [{'startTime': 1, 'title': 'Intro'}]
    embed_mock.assert_called_once()


def test_generate_path_disabled_config_saves_topics_only(monkeypatch):
    storage_mock, embed_mock, generator_class = _run(
        monkeypatch, _db(chapters_mode='generate'), publisher_chapters=[],
        markers=KEPT_SPONSOR, ad_config=AdChapterConfig.disabled())

    assert _saved_chapters(storage_mock) == [{'startTime': 1, 'title': 'Intro'}]


# ---------- publisher-preserve path ----------

PUBLISHER = [{'start': 0.0, 'end': 300.0, 'title': 'Intro'},
             {'start': 300.0, 'end': 1500.0, 'title': 'Body'},
             {'start': 1500.0, 'end': 3600.0, 'title': 'Outro'}]


def test_publisher_preserve_path_merges_and_embeds_when_ads_added(monkeypatch):
    storage_mock, embed_mock, generator_class = _run(
        monkeypatch, _db(chapters_mode='auto'), publisher_chapters=PUBLISHER,
        markers=KEPT_SPONSOR)

    generator_class.return_value.generate_chapters.assert_not_called()
    merged = _saved_chapters(storage_mock)
    assert merged == [{'startTime': 1, 'title': 'Intro'},
                      {'startTime': 300, 'title': 'Body'},
                      AD_ENTRY, RESUME_ENTRY,
                      {'startTime': 1500, 'title': 'Outro'}]
    # The cut step embedded the publisher frames; the ad entries it does not
    # know about make a re-embed necessary here.
    embed_mock.assert_called_once_with(
        '/tmp/fake-processed.mp3', merged, duration=3600.0)


def test_publisher_preserve_path_unchanged_when_no_ads(monkeypatch):
    storage_mock, embed_mock, generator_class = _run(
        monkeypatch, _db(chapters_mode='auto'), publisher_chapters=PUBLISHER,
        markers=[])

    assert _saved_chapters(storage_mock) == [{'startTime': 1, 'title': 'Intro'},
                                             {'startTime': 300, 'title': 'Body'},
                                             {'startTime': 1500, 'title': 'Outro'}]
    embed_mock.assert_not_called()


def test_chapters_mode_off_writes_nothing_even_with_ads(monkeypatch):
    storage_mock, embed_mock, generator_class = _run(
        monkeypatch, _db(chapters_mode='off'), publisher_chapters=PUBLISHER,
        markers=KEPT_SPONSOR)

    storage_mock.save_chapters_and_applied_cuts.assert_not_called()
    embed_mock.assert_not_called()


# ---------- upstream podcast:chapters JSON path ----------

UPSTREAM = [{'startTime': 1, 'title': 'Cold Open'},
            {'startTime': 300, 'title': 'Body'},
            {'startTime': 1500, 'title': 'Outro'}]


def test_upstream_json_path_merges_and_embeds(monkeypatch):
    storage_mock, embed_mock, generator_class = _run(
        monkeypatch,
        _db(chapters_mode='auto',
            upstream_chapters_url='https://pub.example.com/ch.json'),
        publisher_chapters=[], markers=KEPT_SPONSOR, fetch_return=UPSTREAM,
        original_duration=3600.0)

    generator_class.return_value.generate_chapters.assert_not_called()
    merged = _saved_chapters(storage_mock)
    assert merged == [{'startTime': 1, 'title': 'Cold Open'},
                      {'startTime': 300, 'title': 'Body'},
                      AD_ENTRY, RESUME_ENTRY,
                      {'startTime': 1500, 'title': 'Outro'}]
    embed_mock.assert_called_once_with(
        '/tmp/fake-processed.mp3', merged, duration=3600.0)


# ---------- regenerate-chapters endpoint ----------

SLUG = 'ad-chapters-regen-slug'
EPISODE_ID = 'a1b2c3d4e5f6'
VTT = 'WEBVTT\n\n00:00:00.000 --> 00:20:00.000\nHello there\n'


@pytest.fixture
def seeded(app_client):
    from api import get_database, get_storage
    from storage import Storage
    # Rebind Storage to the current Database; an earlier module may have reset it.
    Storage._instance = None
    db = get_database()
    db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Ad Chapters')
    db.upsert_episode(slug=SLUG, episode_id=EPISODE_ID,
                      original_url='https://example.com/ep.mp3',
                      title='Ep', description='Notes', status='processed')
    db.save_episode_details(SLUG, EPISODE_ID, ad_markers=KEPT_SPONSOR)
    get_storage().save_transcript_vtt(SLUG, EPISODE_ID, VTT)
    yield db
    db.delete_podcast(SLUG)


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')
    cookie = client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}


def test_regenerate_endpoint_merges_ad_chapters(app_client, seeded):
    headers = _authed(app_client)
    with patch('api.episodes.ChaptersGenerator') as generator, \
         patch('api.episodes.embed_chapters', return_value=False), \
         patch('api.episodes.resolve_ad_chapter_config',
               lambda db, row, slug=None: AD_CFG), \
         patch('main_app.processing._refresh_rss_for_slug'):
        generator.return_value.generate_chapters.return_value = {
            'version': '1.2.0', 'chapters': [{'startTime': 1, 'title': 'Intro'}]}
        resp = app_client.post(
            f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}/regenerate-chapters',
            headers=headers)

    assert resp.status_code == 200, resp.data
    chapters = resp.get_json()['chapters']
    assert chapters == [{'startTime': 1, 'title': 'Intro'}, AD_ENTRY, RESUME_ENTRY]
    assert resp.get_json()['chapterCount'] == 3
    stored = json.loads(seeded.get_episode(SLUG, EPISODE_ID)['chapters_json'])
    assert stored['chapters'] == chapters


def test_regenerate_endpoint_without_ad_config_is_unchanged(app_client, seeded):
    headers = _authed(app_client)
    with patch('api.episodes.ChaptersGenerator') as generator, \
         patch('api.episodes.embed_chapters', return_value=False), \
         patch('api.episodes.resolve_ad_chapter_config',
               lambda db, row, slug=None: AdChapterConfig.disabled()), \
         patch('main_app.processing._refresh_rss_for_slug'):
        generator.return_value.generate_chapters.return_value = {
            'version': '1.2.0', 'chapters': [{'startTime': 1, 'title': 'Intro'}]}
        resp = app_client.post(
            f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}/regenerate-chapters',
            headers=headers)

    assert resp.status_code == 200, resp.data
    assert resp.get_json()['chapters'] == [{'startTime': 1, 'title': 'Intro'}]
