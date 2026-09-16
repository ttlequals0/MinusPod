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


def _start_run(db, podcast_id, episode_id, run_id):
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO processing_runs (run_id, podcast_id, episode_id, owner_pid, state) "
        "VALUES (?, ?, ?, ?, 'running')",
        (run_id, podcast_id, episode_id, os.getpid()),
    )
    conn.commit()


def test_claimed_run_reports_processing_before_the_status_flips(app_client, seeded):
    """A worker owns the run but has not written status='processing' yet."""
    slug, ep = seeded['seed']('a11000000001', status='pending', queued=False)
    _start_run(seeded['db'], seeded['podcast']['id'], ep, 'run-job-state-1')
    try:
        _authed(app_client)
        resp = app_client.get(f'/api/v1/feeds/{slug}/episodes/{ep}')
        assert resp.get_json()['jobState'] == 'processing'

        listed = app_client.get(f'/api/v1/feeds/{slug}/episodes').get_json()['episodes']
        states = {e['episodeId']: e['jobState'] for e in listed}
        assert states[ep] == 'processing'
    finally:
        conn = seeded['db'].get_connection()
        conn.execute("DELETE FROM processing_runs WHERE run_id = 'run-job-state-1'")
        conn.commit()


def test_claimed_queue_row_reports_processing_not_queued(app_client, seeded):
    slug, ep = seeded['seed']('a22000000001', status='pending', queued=True)
    db = seeded['db']
    conn = db.get_connection()
    conn.execute(
        "UPDATE auto_process_queue SET status = 'processing' WHERE episode_id = ?", (ep,))
    conn.commit()

    _authed(app_client)
    resp = app_client.get(f'/api/v1/feeds/{slug}/episodes/{ep}')
    assert resp.get_json()['jobState'] == 'processing'


def test_has_been_processed_survives_requeue(app_client, seeded):
    """A completed episode queued again reverts to 'pending'; the Process vs
    Reprocess label must not flip with it."""
    db, podcast = seeded['db'], seeded['podcast']
    slug, ep = seeded['seed']('a33000000001', status='processed', queued=False)
    db.record_processing_history(
        podcast_id=podcast['id'], podcast_slug=slug, podcast_title='Job State',
        episode_id=ep, episode_title=ep, status='completed', ads_detected=1)

    _authed(app_client)
    assert app_client.get(
        f'/api/v1/feeds/{slug}/episodes/{ep}').get_json()['hasBeenProcessed'] is True

    db.upsert_episode(slug, ep, status='pending')
    detail = app_client.get(f'/api/v1/feeds/{slug}/episodes/{ep}').get_json()
    assert detail['status'] == 'pending'
    assert detail['hasBeenProcessed'] is True

    listed = app_client.get(f'/api/v1/feeds/{slug}/episodes').get_json()['episodes']
    flags = {e['episodeId']: e['hasBeenProcessed'] for e in listed}
    assert flags[ep] is True


def test_never_processed_episode_reports_has_been_processed_false(app_client, seeded):
    slug, ep = seeded['seed']('a44000000001', status='discovered', queued=False)
    _authed(app_client)
    assert app_client.get(
        f'/api/v1/feeds/{slug}/episodes/{ep}').get_json()['hasBeenProcessed'] is False
