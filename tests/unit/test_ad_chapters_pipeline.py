"""Ad chapters wired into the three chapter-producing paths.

Kept ad segments still in the served audio are published as their own
chapters so a chapter-aware player can skip them. This covers the pass-1
_generate_assets branches (generate, publisher-preserve) and the manual
regenerate-chapters endpoint.
"""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap
from tests.unit.thread_fakes import SyncThread

_test_data_dir = bootstrap('ad_chapters_pipeline_test_', reset_storage=True)

import chapters_generator
from ad_chapters import AdChapterConfig, public_chapters
from llm_client import ProviderRateLimitedError
from main_app import processing
from rate_limit_hold import hold_message
from utils.time import utc_now_iso

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
         original_duration=None, generator_error=None):
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
    if generator_error is not None:
        generator_class.return_value.generate_chapters.side_effect = generator_error

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


def _assert_embedded(embed_mock, merged):
    """The embed always gets the spec-clean projection, never the stored list."""
    embed_mock.assert_called_once_with(
        '/tmp/fake-processed.mp3', public_chapters(merged), duration=3600.0)


# ---------- generate path ----------

def test_generate_path_appends_ad_chapters_and_embeds(monkeypatch):
    storage_mock, embed_mock, generator_class = _run(
        monkeypatch, _db(chapters_mode='generate'), publisher_chapters=[],
        markers=KEPT_SPONSOR)

    generator_class.return_value.generate_chapters.assert_called_once()
    merged = _saved_chapters(storage_mock)
    assert merged == [{'startTime': 1, 'title': 'Intro'}, AD_ENTRY, RESUME_ENTRY]
    _assert_embedded(embed_mock, merged)
    assert all(set(ch) == {'startTime', 'title'}
               for ch in embed_mock.call_args.args[1])


def test_generate_path_with_empty_generation_still_saves_ad_only_list(monkeypatch):
    storage_mock, embed_mock, generator_class = _run(
        monkeypatch, _db(chapters_mode='generate'), publisher_chapters=[],
        generator_chapters={'version': '1.2.0', 'chapters': []},
        markers=KEPT_SPONSOR)

    merged = _saved_chapters(storage_mock)
    assert merged == [AD_ENTRY, RESUME_ENTRY]
    _assert_embedded(embed_mock, merged)


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
    _assert_embedded(embed_mock, merged)


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
    _assert_embedded(embed_mock, merged)


# ---------- regenerate-chapters endpoint ----------

SLUG = 'ad-chapters-regen-slug'
EPISODE_ID = 'a1b2c3d4e5f6'
VTT = 'WEBVTT\n\n00:00:00.000 --> 00:20:00.000\nHello there\n'


@pytest.fixture
def seeded(app_client):
    from api import get_database, get_storage, limiter
    from storage import Storage
    # regenerate-chapters is rate limited; clear the counters per test.
    limiter.reset()
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


def _post_regenerate(app_client, generated, ad_config=None):
    headers = _authed(app_client)
    with patch('api.episodes.threading', SimpleNamespace(Thread=SyncThread)), \
         patch('api.episodes.ChaptersGenerator') as generator, \
         patch('api.episodes.embed_chapters', return_value=False), \
         patch('api.episodes.resolve_ad_chapter_config',
               lambda db, row, slug=None: ad_config or AD_CFG), \
         patch('main_app.processing._refresh_rss_for_slug'):
        generator.return_value.generate_chapters.return_value = generated
        return app_client.post(
            f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}/regenerate-chapters',
            headers=headers)


def test_regenerate_endpoint_merges_ad_chapters(app_client, seeded):
    # Authoritative (empty) cut list: without one the endpoint cannot place
    # ad chapters and skips the merge.
    seeded.save_applied_cuts(SLUG, EPISODE_ID, [])
    resp = _post_regenerate(app_client, {
        'version': '1.2.0', 'chapters': [{'startTime': 1, 'title': 'Intro'}]})

    assert resp.status_code == 202, resp.data
    row = seeded.get_episode(SLUG, EPISODE_ID)
    stored = json.loads(row['chapters_json'])
    assert stored['chapters'] == [{'startTime': 1, 'title': 'Intro'},
                                  AD_ENTRY, RESUME_ENTRY]
    # The served projection drops the internal keys.
    assert public_chapters(stored['chapters']) == [
        {'startTime': 1, 'title': 'Intro'},
        {'startTime': 900, 'title': '[mp:sponsor]'},
        {'startTime': 960, 'title': 'Show'}]
    assert row['chapters_regen_started_at'] is None
    assert row['chapters_regen_error'] is None


def test_regenerate_endpoint_skips_ad_chapters_without_applied_cuts(app_client, seeded):
    """None cuts are unknown, not empty: ad spans would land at original offsets."""
    assert seeded.get_applied_cuts(SLUG, EPISODE_ID) is None
    resp = _post_regenerate(app_client, {
        'version': '1.2.0', 'chapters': [{'startTime': 1, 'title': 'Intro'}]})

    assert resp.status_code == 202, resp.data
    stored = json.loads(seeded.get_episode(SLUG, EPISODE_ID)['chapters_json'])
    assert stored['chapters'] == [{'startTime': 1, 'title': 'Intro'}]


def test_served_chapters_json_carries_no_internal_keys(app_client, monkeypatch):
    """The Podcasting 2.0 document is the public projection of the stored list."""
    import main_app
    stored = {'version': '1.2.0', 'chapters': [
        {'startTime': 1, 'title': 'Intro'},
        {'startTime': 600, 'title': 'Displaced topic', 'hidden': True},
        AD_ENTRY, RESUME_ENTRY]}
    monkeypatch.setattr(main_app.storage, 'get_chapters_json', lambda s, e: stored)

    resp = app_client.get(f'/episodes/{SLUG}/{EPISODE_ID}/chapters.json')
    assert resp.status_code == 200, resp.data
    body = json.loads(resp.data)
    assert body['version'] == '1.2.0'
    assert body['chapters'] == [{'startTime': 1, 'title': 'Intro'},
                                {'startTime': 900, 'title': '[mp:sponsor]'},
                                {'startTime': 960, 'title': 'Show'}]


def test_regenerate_endpoint_without_ad_config_is_unchanged(app_client, seeded):
    resp = _post_regenerate(app_client, {
        'version': '1.2.0', 'chapters': [{'startTime': 1, 'title': 'Intro'}]},
        ad_config=AdChapterConfig.disabled())

    assert resp.status_code == 202, resp.data
    stored = json.loads(seeded.get_episode(SLUG, EPISODE_ID)['chapters_json'])
    assert stored['chapters'] == [{'startTime': 1, 'title': 'Intro'}]


def test_regenerate_endpoint_reports_the_run_on_the_episode(app_client, seeded):
    """The stamp marks a run in flight and a failure is kept for the detail page."""
    headers = _authed(app_client)
    # Patch the module reference, not threading.Thread itself: a global patch
    # also breaks the rate limiter's expiry Timer on any concurrent request.
    thread = MagicMock()
    with patch('api.episodes.threading', SimpleNamespace(Thread=thread)):
        resp = app_client.post(
            f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}/regenerate-chapters',
            headers=headers)
    assert resp.status_code == 202, resp.data
    thread.return_value.start.assert_called_once()
    # The job owns the run through the stamp the claim wrote.
    stamp = seeded.get_episode(SLUG, EPISODE_ID)['chapters_regen_started_at']
    assert thread.call_args.kwargs['args'] == (SLUG, EPISODE_ID, stamp)
    detail = app_client.get(f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}').get_json()
    assert detail['chaptersRegenerating'] is True
    assert detail['chaptersRegenError'] is None

    # A second start while one is in flight is refused.
    resp = app_client.post(
        f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}/regenerate-chapters',
        headers=headers)
    assert resp.status_code == 409, resp.data

    resp = _post_regenerate(app_client, None)  # generator returns nothing
    assert resp.status_code == 409, resp.data

    seeded.finish_chapters_regen(SLUG, EPISODE_ID, stamp)
    resp = _post_regenerate(app_client, None)
    assert resp.status_code == 202, resp.data
    detail = app_client.get(f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}').get_json()
    assert detail['chaptersRegenerating'] is False
    assert detail['chaptersRegenError'] == 'Failed to generate chapters'


def test_regenerate_endpoint_refuses_while_the_queue_is_held(app_client, seeded):
    """A rate-limit hold (#696) pauses new LLM runs, and a topic pass is one."""
    from rate_limit_hold import clear_hold, record_hold_until

    until = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
    record_hold_until(seeded, until)
    try:
        resp = app_client.post(
            f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}/regenerate-chapters',
            headers=_authed(app_client))
    finally:
        clear_hold(seeded)

    assert resp.status_code == 409, resp.data
    assert until in resp.get_json()['error']
    assert seeded.get_episode(SLUG, EPISODE_ID)['chapters_regen_started_at'] is None


def test_regen_job_records_the_hold_on_a_provider_rate_limit(app_client, seeded):
    """A 429 mid-regeneration pauses the queue and is reported on the episode."""
    from rate_limit_hold import clear_hold, get_hold_until

    error = ProviderRateLimitedError('resets in 900s', retry_after_seconds=900.0)
    seeded.set_setting('rate_limit_hold_enabled', 'true')
    try:
        with patch('api.episodes.threading', SimpleNamespace(Thread=SyncThread)), \
             patch('api.episodes.ChaptersGenerator') as generator, \
             patch('rate_limit_hold.fire_queue_held_event') as fire, \
             patch('rate_limit_hold.get_llm_usage_url', lambda _db: ''):
            generator.return_value.generate_chapters.side_effect = error
            resp = app_client.post(
                f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}/regenerate-chapters',
                headers=_authed(app_client))
        held_until = get_hold_until(seeded)
    finally:
        clear_hold(seeded)
        seeded.set_setting('rate_limit_hold_enabled', 'false')

    assert resp.status_code == 202, resp.data
    assert held_until and held_until > utc_now_iso()
    row = seeded.get_episode(SLUG, EPISODE_ID)
    assert row['chapters_regen_started_at'] is None
    assert row['chapters_regen_error'] == hold_message(held_until, error)
    # A fresh pause alerts once, the same rule the failure handler follows.
    assert fire.call_count == 1
    assert fire.call_args.kwargs['hold_until'] == held_until
    assert fire.call_args.kwargs['episode_id'] == EPISODE_ID
    assert fire.call_args.kwargs['podcast_name'] == 'Ad Chapters'


def test_regen_job_under_an_active_hold_does_not_alert_again(app_client, seeded):
    """The endpoint refuses a start under an active hold, so this can only
    happen when the pause lands mid-run; extending it is not a new pause."""
    from rate_limit_hold import clear_hold, get_hold_until, record_hold_until

    active_until = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime(
        '%Y-%m-%dT%H:%M:%SZ')
    stamp = seeded.claim_chapters_regen(SLUG, EPISODE_ID)
    error = ProviderRateLimitedError('resets in 900s', retry_after_seconds=900.0)
    seeded.set_setting('rate_limit_hold_enabled', 'true')
    try:
        with patch('api.episodes.ChaptersGenerator') as generator, \
             patch('rate_limit_hold.fire_queue_held_event') as fire, \
             patch('rate_limit_hold.get_llm_usage_url', lambda _db: ''):
            generator.return_value.generate_chapters.side_effect = error
            record_hold_until(seeded, active_until)
            from api.episodes import _regenerate_chapters_job
            _regenerate_chapters_job(SLUG, EPISODE_ID, stamp)
        held_until = get_hold_until(seeded)
    finally:
        clear_hold(seeded)
        seeded.set_setting('rate_limit_hold_enabled', 'false')

    fire.assert_not_called()
    assert held_until == active_until
    row = seeded.get_episode(SLUG, EPISODE_ID)
    assert row['chapters_regen_error'] == hold_message(active_until, error)


def test_stale_regen_stamp_is_taken_over(app_client, seeded):
    """A worker that died mid-run leaves a stamp; an old one does not block."""
    conn = seeded.get_connection()
    conn.execute(
        "UPDATE episodes SET chapters_regen_started_at = '2020-01-01T00:00:00Z' "
        "WHERE episode_id = ?", (EPISODE_ID,))
    conn.commit()
    detail = app_client.get(f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}').get_json()
    assert detail['chaptersRegenerating'] is False

    resp = _post_regenerate(app_client, {
        'version': '1.2.0', 'chapters': [{'startTime': 1, 'title': 'Intro'}]},
        ad_config=AdChapterConfig.disabled())
    assert resp.status_code == 202, resp.data
    assert seeded.get_episode(SLUG, EPISODE_ID)['chapters_regen_started_at'] is None


def test_regenerate_endpoint_refuses_an_episode_being_processed(app_client, seeded):
    """A run in the pipeline rewrites the transcript the regeneration would read."""
    conn = seeded.get_connection()
    conn.execute("UPDATE episodes SET status = 'processing' WHERE episode_id = ?",
                 (EPISODE_ID,))
    conn.commit()
    resp = app_client.post(
        f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}/regenerate-chapters',
        headers=_authed(app_client))
    assert resp.status_code == 409, resp.data
    assert 'processing' in resp.get_json()['error']
    assert seeded.get_episode(SLUG, EPISODE_ID)['chapters_regen_started_at'] is None


def test_restart_clears_stamps_left_by_killed_runs(app_client, seeded):
    """No thread survives a restart, so every stamp left behind is dead."""
    assert seeded.claim_chapters_regen(SLUG, EPISODE_ID)
    detail = app_client.get(f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}').get_json()
    assert detail['chaptersRegenerating'] is True

    assert seeded.clear_chapters_regen_stamps() == 1
    detail = app_client.get(f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}').get_json()
    assert detail['chaptersRegenerating'] is False


def test_a_taken_over_run_leaves_the_new_owner_alone(seeded):
    """The old owner finishing late must not clear the run that replaced it."""
    stale = '2020-01-01T00:00:00Z'
    conn = seeded.get_connection()
    conn.execute("UPDATE episodes SET chapters_regen_started_at = ? WHERE episode_id = ?",
                 (stale, EPISODE_ID))
    conn.commit()

    stamp = seeded.claim_chapters_regen(SLUG, EPISODE_ID)
    assert stamp and stamp != stale

    seeded.finish_chapters_regen(SLUG, EPISODE_ID, stale, error='late failure')
    row = seeded.get_episode(SLUG, EPISODE_ID)
    assert row['chapters_regen_started_at'] == stamp
    assert row['chapters_regen_error'] is None

    seeded.finish_chapters_regen(SLUG, EPISODE_ID, stamp)
    assert seeded.get_episode(SLUG, EPISODE_ID)['chapters_regen_started_at'] is None


def test_regenerate_aborts_when_the_episode_moves_underneath_it(app_client, seeded):
    """Chapters cut from a transcript a reprocess has replaced are not saved."""
    before = seeded.get_episode(SLUG, EPISODE_ID)['chapters_json']
    real_get_episode = type(seeded).get_episode
    calls = []

    def fake_get_episode(self, slug, episode_id):
        row = real_get_episode(self, slug, episode_id)
        calls.append(row)
        return {**row, 'status': 'processing'} if len(calls) >= 2 else row

    with patch.object(type(seeded), 'get_episode', fake_get_episode):
        resp = _post_regenerate(app_client, {
            'version': '1.2.0', 'chapters': [{'startTime': 1, 'title': 'Intro'}]},
            ad_config=AdChapterConfig.disabled())

    assert resp.status_code == 202, resp.data
    row = seeded.get_episode(SLUG, EPISODE_ID)
    assert row['chapters_json'] == before
    assert 'reprocessed during chapter regeneration' in row['chapters_regen_error']


def test_regenerate_merges_the_markers_as_of_the_save(app_client, seeded):
    """A correction landing during the LLM pass must not be reverted by the merge."""
    seeded.save_applied_cuts(SLUG, EPISODE_ID, [])
    headers = _authed(app_client)

    def reject_the_marker(*_args, **_kwargs):
        seeded.save_episode_details(SLUG, EPISODE_ID, ad_markers=[])
        return {'version': '1.2.0', 'chapters': [{'startTime': 1, 'title': 'Intro'}]}

    with patch('api.episodes.threading', SimpleNamespace(Thread=SyncThread)), \
         patch('api.episodes.ChaptersGenerator') as generator, \
         patch('api.episodes.embed_chapters', return_value=False), \
         patch('api.episodes.resolve_ad_chapter_config',
               lambda db, row, slug=None: AD_CFG), \
         patch('main_app.processing._refresh_rss_for_slug'):
        generator.return_value.generate_chapters.side_effect = reject_the_marker
        resp = app_client.post(
            f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}/regenerate-chapters',
            headers=headers)

    assert resp.status_code == 202, resp.data
    stored = json.loads(seeded.get_episode(SLUG, EPISODE_ID)['chapters_json'])
    assert stored['chapters'] == [{'startTime': 1, 'title': 'Intro'}]


def test_regenerate_does_not_save_once_its_stamp_was_taken_over(app_client, seeded):
    """A run past the claim window lost the episode to a newer one."""
    before = seeded.get_episode(SLUG, EPISODE_ID)['chapters_json']
    newer = '2099-01-01T00:00:00Z'

    def take_the_stamp_over(*_args, **_kwargs):
        conn = seeded.get_connection()
        conn.execute("UPDATE episodes SET chapters_regen_started_at = ? "
                     "WHERE episode_id = ?", (newer, EPISODE_ID))
        conn.commit()
        return {'version': '1.2.0', 'chapters': [{'startTime': 1, 'title': 'Intro'}]}

    with patch('api.episodes.threading', SimpleNamespace(Thread=SyncThread)), \
         patch('api.episodes.ChaptersGenerator') as generator, \
         patch('main_app.processing._refresh_rss_for_slug'):
        generator.return_value.generate_chapters.side_effect = take_the_stamp_over
        resp = app_client.post(
            f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}/regenerate-chapters',
            headers=_authed(app_client))

    assert resp.status_code == 202, resp.data
    row = seeded.get_episode(SLUG, EPISODE_ID)
    assert row['chapters_json'] == before
    # The newer run still owns the episode; the loser wrote nothing.
    assert row['chapters_regen_started_at'] == newer
    assert row['chapters_regen_error'] is None


def test_regenerate_endpoint_reports_a_thread_that_will_not_start(app_client, seeded):
    """A refused thread leaves no stamp behind for the 15 minute takeover."""
    refuses = SimpleNamespace(Thread=MagicMock(side_effect=RuntimeError('no threads')))
    with patch('api.episodes.threading', refuses):
        resp = app_client.post(
            f'/api/v1/feeds/{SLUG}/episodes/{EPISODE_ID}/regenerate-chapters',
            headers=_authed(app_client))

    assert resp.status_code == 500, resp.data
    row = seeded.get_episode(SLUG, EPISODE_ID)
    assert row['chapters_regen_started_at'] is None
    assert row['chapters_regen_error'] == 'Could not start regeneration thread'


def test_regen_job_records_the_error_when_the_hold_write_fails(app_client, seeded):
    """The run failed either way, so the episode must not report a clean finish."""
    error = ProviderRateLimitedError('resets in 900s', retry_after_seconds=900.0)
    stamp = seeded.claim_chapters_regen(SLUG, EPISODE_ID)

    with patch('api.episodes.ChaptersGenerator') as generator, \
         patch('api.episodes.hold_queue_for_provider_limit',
               side_effect=RuntimeError('settings write failed')):
        generator.return_value.generate_chapters.side_effect = error
        from api.episodes import _regenerate_chapters_job
        _regenerate_chapters_job(SLUG, EPISODE_ID, stamp)

    row = seeded.get_episode(SLUG, EPISODE_ID)
    assert row['chapters_regen_started_at'] is None
    assert 'resets in 900s' in row['chapters_regen_error']


def test_chapter_step_publishes_ad_chapters_when_the_hold_write_fails(monkeypatch):
    """The audio is already cut; a failed hold write must not cost it its chapters."""
    monkeypatch.setattr(processing, 'hold_queue_for_provider_limit',
                        MagicMock(side_effect=RuntimeError('settings write failed')))
    storage_mock, embed_mock, _ = _run(
        monkeypatch, _db(chapters_mode='generate'), publisher_chapters=[],
        markers=KEPT_SPONSOR,
        generator_error=ProviderRateLimitedError('resets in 900s',
                                                 retry_after_seconds=900.0))

    merged = _saved_chapters(storage_mock)
    assert merged == [AD_ENTRY, RESUME_ENTRY]
    _assert_embedded(embed_mock, merged)
