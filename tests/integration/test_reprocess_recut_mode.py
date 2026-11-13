"""Integration tests for the recut reprocess mode (issue #422).

Recut re-cuts the retained original from the current ad detections with no
transcription or LLM. The endpoint must refuse when its inputs are missing.
These tests exercise request-validation paths that return before any background
work starts.
"""
import os
import sys

import pytest

pytest.importorskip("ctranslate2", reason="Integration tests require Docker environment")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))


@pytest.fixture
def _auth(monkeypatch):
    monkeypatch.delenv('ADMIN_PASSWORD', raising=False)
    yield


@pytest.fixture
def seeded_episode(app_client):
    from api import get_database

    db = get_database()
    slug = 'recut-feed'
    episode_id = 'abcdef012422'  # 12-char hex (is_valid_episode_id)

    db.create_podcast(slug, 'https://example.com/feed.xml', 'Recut Feed')
    db.upsert_episode(slug, episode_id,
                      original_url='https://example.com/ep.mp3',
                      title='Test Episode',
                      status='processed')

    yield {'slug': slug, 'episode_id': episode_id, 'db': db}

    try:
        db.delete_podcast(slug)
    except Exception:
        pass


def test_recut_requires_retained_original(app_client, seeded_episode, _auth):
    slug = seeded_episode['slug']
    ep_id = seeded_episode['episode_id']

    # No retained original on disk -> recut refuses with 409. A 409 (not 400)
    # also proves the 'recut' mode itself is accepted by the enum.
    r = app_client.post(f'/api/v1/episodes/{slug}/{ep_id}/reprocess', json={'mode': 'recut'})

    assert r.status_code == 409
    assert 'original audio' in (r.get_json() or {}).get('error', '').lower()
