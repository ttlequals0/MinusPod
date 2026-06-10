"""Integration tests for the LLM-only reprocess mode (issue #349).

The endpoint reruns ad detection and re-cut using the saved transcript and
skips re-transcription. It must refuse to run when no transcript exists, since
there is nothing to reuse. These tests only exercise the request-validation
paths (400 responses), which return before any background processing starts.
"""
import os
import sys

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
    episode_id = 'llmrep001'

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
