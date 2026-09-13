"""Integration tests for the authoritative episode jobState field.

jobState is derived from live queue state (auto_process_queue), not the
stored lifecycle status, so a 'pending' episode without a queue row reports
'idle' rather than implying work is scheduled.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='job-state-test-'))


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True


@pytest.fixture
def seeded(app_client):
    from api import get_database
    db = get_database()
    slug = 'job-state-feed'
    db.create_podcast(slug, 'https://example.com/feed.xml', 'Job State Test')
    podcast = db.get_podcast_by_slug(slug)

    def seed_episode(ep_id, status='pending', queued=False):
        db.upsert_episode(slug, ep_id,
                          original_url=f'https://example.com/{ep_id}.mp3',
                          title=ep_id, status=status)
        if queued:
            db.queue_episode_for_processing(
                slug, ep_id, original_url=f'https://example.com/{ep_id}.mp3',
                title=ep_id)
        return slug, ep_id

    yield {'slug': slug, 'db': db, 'podcast': podcast, 'seed': seed_episode}
    db.delete_podcast(slug)


def test_pending_with_queue_row_reports_queued(app_client, seeded):
    slug, ep = seeded['seed']('aaa000000001', status='pending', queued=True)
    _authed(app_client)
    resp = app_client.get(f'/api/v1/feeds/{slug}/episodes/{ep}')
    assert resp.get_json()['jobState'] == 'queued'


def test_pending_without_queue_row_reports_idle(app_client, seeded):
    slug, ep = seeded['seed']('bbb000000001', status='pending', queued=False)
    _authed(app_client)
    resp = app_client.get(f'/api/v1/feeds/{slug}/episodes/{ep}')
    assert resp.get_json()['jobState'] == 'idle'


def test_processing_reports_processing(app_client, seeded):
    slug, ep = seeded['seed']('ccc000000001', status='processing', queued=False)
    _authed(app_client)
    resp = app_client.get(f'/api/v1/feeds/{slug}/episodes/{ep}')
    assert resp.get_json()['jobState'] == 'processing'


def test_processing_reports_processing_even_with_stale_queue_row(app_client, seeded):
    """A claimed queue row flips to status='processing' elsewhere; the
    lingering queue row (if any) must not downgrade jobState back to
    queued."""
    slug, ep = seeded['seed']('ddd000000001', status='processing', queued=True)
    _authed(app_client)
    resp = app_client.get(f'/api/v1/feeds/{slug}/episodes/{ep}')
    assert resp.get_json()['jobState'] == 'processing'


def test_list_endpoint_reports_job_state_per_episode(app_client, seeded):
    slug = seeded['slug']
    seeded['seed']('eee000000001', status='pending', queued=True)
    seeded['seed']('fff000000001', status='pending', queued=False)

    _authed(app_client)
    resp = app_client.get(f'/api/v1/feeds/{slug}/episodes')
    episodes = {e['episodeId']: e['jobState'] for e in resp.get_json()['episodes']}
    assert episodes['eee000000001'] == 'queued'
    assert episodes['fff000000001'] == 'idle'
