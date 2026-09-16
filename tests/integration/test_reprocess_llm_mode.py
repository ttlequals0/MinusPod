"""Integration tests for the LLM-only reprocess mode (issue #349) and for the
authoritative jobState the reprocess/bulk endpoints return alongside status.

The endpoint reruns ad detection and re-cut using the saved transcript and
skips re-transcription. It must refuse to run when no transcript exists, since
there is nothing to reuse. Most tests here exercise the request-validation
paths (400 responses), which return before any background processing starts;
the jobState tests below mock start_background_processing to reach the
queued/processing/409 branches without spinning a real pipeline.
"""
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
    # get_status_service().queue_episode(); it lives in StatusService's
    # in-memory queue, not the DB, so deleting the podcast row does not
    # clear it and it would otherwise leak into other tests' queue reads.
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
    # Confirms (does not rebuild) the atomic admission the reprocess endpoint
    # relies on: two concurrent acquires for the same episode cannot both
    # win, so the endpoint's own duplicate-submission handling never needs
    # to guard a second active run itself.
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
