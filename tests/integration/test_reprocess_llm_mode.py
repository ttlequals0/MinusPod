"""Integration tests for the LLM-only reprocess mode (issue #349) and the authoritative jobState the reprocess/bulk endpoints return alongside status.

The endpoint reruns ad detection and re-cut from the saved transcript, skipping
re-transcription, and must refuse when no transcript exists. Most tests exercise
request-validation paths (400s) that return before background processing; the
jobState tests mock start_background_processing to reach the queued/processing/409
branches without a real pipeline.
"""
import json
import os
import sys
from unittest.mock import patch

import pytest

pytest.importorskip("ctranslate2", reason="Integration tests require Docker environment")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))


@pytest.fixture
def _auth(monkeypatch):
    # Bypass the @api.before_request auth gate by clearing ADMIN_PASSWORD.
    monkeypatch.delenv('ADMIN_PASSWORD', raising=False)
    yield


@pytest.fixture
def seeded_episode(app_client):
    from api import get_database

    db = get_database()
    slug = 'llm-reprocess-feed'
    # Must be 12-char hex: the blueprint url_value_preprocessor now 400s
    # malformed episode_id path params before the route body runs.
    episode_id = 'aa11bb22cc33'

    db.create_podcast(slug, 'https://example.com/feed.xml', 'LLM Reprocess Feed')
    db.upsert_episode(slug, episode_id,
                      original_url='https://example.com/ep.mp3',
                      title='Test Episode',
                      status='processed')

    yield {'slug': slug, 'episode_id': episode_id, 'db': db}

    try:
        db.delete_podcast(slug)
    except Exception:
        pass
    # The queued-jobState tests add a display-queue entry via
    # get_status_service().queue_episode(); it lives in StatusService's in-memory
    # queue, so deleting the podcast row does not clear it and it would leak into
    # other tests' queue reads.
    try:
        from api import get_status_service
        get_status_service().remove_feed_from_queue(slug)
    except Exception:
        pass


def test_llm_mode_requires_transcript(app_client, seeded_episode, _auth):
    slug = seeded_episode['slug']
    ep_id = seeded_episode['episode_id']

    # No transcript saved -> LLM-only reprocess must refuse with 400.
    r = app_client.post(f'/api/v1/episodes/{slug}/{ep_id}/reprocess', json={'mode': 'llm'})

    assert r.status_code == 400
    assert 'transcript' in (r.get_json() or {}).get('error', '').lower()


def test_invalid_mode_rejected(app_client, seeded_episode, _auth):
    slug = seeded_episode['slug']
    ep_id = seeded_episode['episode_id']

    r = app_client.post(f'/api/v1/episodes/{slug}/{ep_id}/reprocess', json={'mode': 'bogus'})

    assert r.status_code == 400


def test_bulk_reprocess_llm_action_accepted(app_client, seeded_episode, _auth):
    # The unified bulk endpoint must accept the reprocess_llm action. The
    # processed episode here has no transcript, so it is skipped rather than
    # queued, but the action itself must validate (not 400 on the enum).
    slug = seeded_episode['slug']
    ep_id = seeded_episode['episode_id']

    r = app_client.post(
        f'/api/v1/feeds/{slug}/episodes/bulk',
        json={'episodeIds': [ep_id], 'action': 'reprocess_llm'},
    )

    assert r.status_code == 200
    body = r.get_json() or {}
    assert body.get('skipped') == 1
    assert body.get('queued') == 0


def test_bulk_reprocess_returns_queued_job_state(app_client, seeded_episode, _auth):
    # 'reprocess' (unlike 'reprocess_llm') needs no transcript, so the
    # episode is actually enqueued and the bulk response reports jobState.
    slug = seeded_episode['slug']
    ep_id = seeded_episode['episode_id']

    r = app_client.post(
        f'/api/v1/feeds/{slug}/episodes/bulk',
        json={'episodeIds': [ep_id], 'action': 'reprocess'},
    )

    assert r.status_code == 200
    body = r.get_json() or {}
    assert body.get('queued') == 1
    assert body.get('jobState') == 'queued'


def test_bulk_delete_omits_job_state(app_client, seeded_episode, _auth):
    # jobState only describes queue/run state; delete has neither.
    slug = seeded_episode['slug']
    ep_id = seeded_episode['episode_id']

    r = app_client.post(
        f'/api/v1/feeds/{slug}/episodes/bulk',
        json={'episodeIds': [ep_id], 'action': 'delete'},
    )

    assert r.status_code == 200
    assert 'jobState' not in (r.get_json() or {})


def test_bulk_process_skips_title_patterns_for_discovered_and_pending(
        app_client, seeded_episode, _auth):
    slug = seeded_episode['slug']
    db = seeded_episode['db']
    db.update_podcast(slug, title_skip_patterns=json.dumps(['Skipped*']))
    db.upsert_episode(slug, 'bb22cc33dd44', original_url='https://example.com/a.mp3',
                      title='Skipped discovered', status='discovered')
    db.upsert_episode(slug, 'cc33dd44ee55', original_url='https://example.com/b.mp3',
                      title='Skipped pending', status='pending')

    response = app_client.post(
        f'/api/v1/feeds/{slug}/episodes/bulk',
        json={'episodeIds': ['bb22cc33dd44', 'cc33dd44ee55'], 'action': 'process'},
    )

    assert response.status_code == 200
    body = response.get_json() or {}
    assert body['queued'] == 0
    assert body['skipped'] == 2
    assert {item['episodeId'] for item in body['skippedEpisodes']} == {
        'bb22cc33dd44', 'cc33dd44ee55'}


def test_bulk_reprocess_allows_manual_title_skipped_selection(
        app_client, seeded_episode, _auth):
    slug = seeded_episode['slug']
    db = seeded_episode['db']
    db.update_podcast(slug, title_skip_patterns=json.dumps(['Test*']))
    episode_id = seeded_episode['episode_id']

    response = app_client.post(
        f'/api/v1/feeds/{slug}/episodes/bulk',
        json={'episodeIds': [episode_id], 'action': 'reprocess'},
    )

    assert response.status_code == 200
    assert (response.get_json() or {}).get('queued') == 1


def test_bulk_delete_allows_manual_title_skipped_selection(
        app_client, seeded_episode, _auth):
    slug = seeded_episode['slug']
    db = seeded_episode['db']
    db.update_podcast(slug, title_skip_patterns=json.dumps(['Test*']))

    response = app_client.post(
        f'/api/v1/feeds/{slug}/episodes/bulk',
        json={'episodeIds': [seeded_episode['episode_id']], 'action': 'delete'},
    )

    assert response.status_code == 200
    assert (response.get_json() or {}).get('skipped') == 0


@patch('main_app.processing.start_background_processing', return_value=(True, 'started'))
def test_reprocess_returns_processing_job_state_when_started(_start, app_client, seeded_episode, _auth):
    slug = seeded_episode['slug']
    ep_id = seeded_episode['episode_id']

    r = app_client.post(f'/api/v1/episodes/{slug}/{ep_id}/reprocess', json={'mode': 'reprocess'})

    assert r.status_code == 202
    body = r.get_json() or {}
    assert body.get('status') == 'processing'
    assert body.get('jobState') == 'processing'


@patch('main_app.processing.start_background_processing', return_value=(False, 'queue_busy:other:ep'))
def test_reprocess_returns_queued_job_state_with_no_duplicate_queue_row(_start, app_client, seeded_episode, _auth):
    slug = seeded_episode['slug']
    ep_id = seeded_episode['episode_id']
    db = seeded_episode['db']

    r1 = app_client.post(f'/api/v1/episodes/{slug}/{ep_id}/reprocess', json={'mode': 'reprocess'})
    assert r1.status_code == 202
    body1 = r1.get_json() or {}
    assert body1.get('status') == 'queued'
    assert body1.get('jobState') == 'queued'

    # Second immediate submission: same reported job state, no error to
    # special-case, and the UNIQUE(podcast_id, episode_id) constraint means
    # this can only ever update the existing row, never insert a second one.
    r2 = app_client.post(f'/api/v1/episodes/{slug}/{ep_id}/reprocess', json={'mode': 'reprocess'})
    assert r2.status_code == 202
    body2 = r2.get_json() or {}
    assert body2.get('jobState') == 'queued'

    podcast = db.get_podcast_by_slug(slug)
    row_count = db.get_connection().execute(
        'SELECT COUNT(*) AS n FROM auto_process_queue WHERE podcast_id = ? AND episode_id = ?',
        (podcast['id'], ep_id),
    ).fetchone()['n']
    assert row_count == 1


def test_reprocess_409_when_already_processing_carries_job_state(app_client, seeded_episode, _auth):
    slug = seeded_episode['slug']
    ep_id = seeded_episode['episode_id']
    db = seeded_episode['db']
    db.upsert_episode(slug, ep_id, status='processing')

    r = app_client.post(f'/api/v1/episodes/{slug}/{ep_id}/reprocess', json={'mode': 'reprocess'})

    assert r.status_code == 409
    body = r.get_json() or {}
    assert body.get('jobState') == 'processing'


def test_second_active_run_blocked_by_partial_unique_index(seeded_episode):
    # Confirms (does not rebuild) the atomic admission the reprocess endpoint relies
    # on: two concurrent acquires for one episode cannot both win, so the endpoint
    # never needs to guard a second active run itself.
    from processing_queue import ProcessingQueue

    slug = seeded_episode['slug']
    ep_id = seeded_episode['episode_id']
    queue = ProcessingQueue()

    first = queue.acquire(slug, ep_id)
    try:
        assert first is not None
        assert queue.acquire(slug, ep_id) is None
    finally:
        queue.release(first, terminal_state='interrupted')
