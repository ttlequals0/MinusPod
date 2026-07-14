"""Integration tests for the original.mp3 stale-column self-heal (#517).

Original-only retention sweeps before 2.52.0 deleted the retained original
from disk without clearing episodes.original_file, so Ad Review kept
rendering play buttons whose URL 404s. The route now clears the stale
column on that 404 so the button disappears on the next detections fetch.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='orig-heal-test-'))


@pytest.fixture
def stale_episode(app_client):
    """Episode whose original_file column is set but whose file is gone."""
    from api import get_database
    db = get_database()
    slug = 'selfheal-feed'
    ep_id = 'abcdef123456'
    db.create_podcast(slug, 'https://example.com/feed.xml', 'Self Heal Test')
    db.upsert_episode(
        slug, ep_id,
        original_url=f'https://example.com/{ep_id}.mp3',
        title='Stale original', status='processed',
    )
    # Second call takes the update path; the insert path ignores this field.
    db.upsert_episode(slug, ep_id, original_file=f'episodes/{ep_id}-original.mp3')
    assert db.get_episode(slug, ep_id)['original_file']
    yield {'slug': slug, 'ep_id': ep_id, 'db': db}
    db.delete_podcast(slug)


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True


def test_missing_file_404_clears_stale_column(app_client, stale_episode):
    _authed(app_client)
    slug, ep_id = stale_episode['slug'], stale_episode['ep_id']

    resp = app_client.get(f'/api/v1/feeds/{slug}/episodes/{ep_id}/original.mp3')
    assert resp.status_code == 404
    assert resp.get_json()['error'] == 'Original audio file missing'

    ep = stale_episode['db'].get_episode(slug, ep_id)
    assert ep['original_file'] is None

    # With the column cleared, the route reports "not retained" instead.
    resp = app_client.get(f'/api/v1/feeds/{slug}/episodes/{ep_id}/original.mp3')
    assert resp.status_code == 404
    assert resp.get_json()['error'] == 'Original audio not retained for this episode'


def test_peaks_route_also_heals(app_client, stale_episode):
    _authed(app_client)
    slug, ep_id = stale_episode['slug'], stale_episode['ep_id']

    resp = app_client.get(f'/api/v1/feeds/{slug}/episodes/{ep_id}/peaks')
    assert resp.status_code == 404

    ep = stale_episode['db'].get_episode(slug, ep_id)
    assert ep['original_file'] is None
