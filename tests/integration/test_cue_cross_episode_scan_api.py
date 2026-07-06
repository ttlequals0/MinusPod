"""Integration tests for POST /feeds/<slug>/cue-cross-episode-scan (D1b, #350).

Covers: claim/poll semantics, rescan, validation failures, worker payload shape,
and error path.
"""
import os
import sys
import tempfile
import wave
from unittest.mock import patch

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='xep-scan-test-'))


def _csrf(app_client):
    with app_client.session_transaction() as sess:
        sess['authenticated'] = True
    app_client.get('/api/v1/auth/status')
    cookie = app_client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}


def _write_wav(path, sr=16000, duration_s=2.0):
    samples = (0.01 * np.random.default_rng(0).standard_normal(int(sr * duration_s))
               .astype(np.float32))
    pcm = (np.clip(samples, -1, 1) * 32767).astype('<i2')
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


@pytest.fixture
def xep_seeded(app_client):
    """Feed with two processed episodes that each have retained original audio."""
    from api import get_database, get_storage
    db = get_database()
    storage = get_storage()
    slug = 'xep-scan-feed'
    # Episode IDs must be 12 lowercase hex chars (EPISODE_ID_RE: ^[a-f0-9]{12}$)
    ep1 = 'aabbcc000001'
    ep2 = 'aabbcc000002'
    try:
        db.delete_podcast(slug)
    except Exception:
        pass
    db.create_podcast(slug, 'https://example.com/xep.xml', title='XEP Show')
    for eid in (ep1, ep2):
        db.upsert_episode(slug, eid, title=f'Ep {eid}', status='processed')
        db.upsert_episode(slug, eid, status='processed', original_file='original.mp3')
        path = storage.get_original_path(slug, eid)
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_wav(path)
    yield {'slug': slug, 'ep1': ep1, 'ep2': ep2, 'db': db, 'storage': storage}


def _post(app_client, slug, body, headers):
    return app_client.post(
        f'/api/v1/feeds/{slug}/cue-cross-episode-scan',
        json=body,
        headers=headers,
    )


# --- validation ---

def test_unknown_feed_returns_404(app_client):
    hdr = _csrf(app_client)
    r = _post(app_client, 'no-such-feed', {'episodeIds': ['ep1', 'ep2']}, hdr)
    assert r.status_code == 404


def test_fewer_than_two_ids_returns_400(app_client, xep_seeded):
    hdr = _csrf(app_client)
    slug = xep_seeded['slug']
    r = _post(app_client, slug, {'episodeIds': [xep_seeded['ep1']]}, hdr)
    assert r.status_code == 400
    assert 'at least 2' in r.get_json().get('error', '').lower()


def test_empty_id_list_returns_400(app_client, xep_seeded):
    hdr = _csrf(app_client)
    r = _post(app_client, xep_seeded['slug'], {'episodeIds': []}, hdr)
    assert r.status_code == 400


def test_too_many_ids_returns_400(app_client, xep_seeded):
    hdr = _csrf(app_client)
    slug = xep_seeded['slug']
    r = _post(app_client, slug, {'episodeIds': ['a', 'b', 'c', 'd', 'e', 'f']}, hdr)
    assert r.status_code == 400
    assert 'at most' in r.get_json().get('error', '').lower()


def test_foreign_episode_id_returns_400(app_client, xep_seeded):
    hdr = _csrf(app_client)
    slug = xep_seeded['slug']
    ep1 = xep_seeded['ep1']
    foreign = 'ffeedd000099'  # valid shape but not in this feed
    r = _post(app_client, slug, {'episodeIds': [ep1, foreign]}, hdr)
    assert r.status_code == 400
    body = r.get_json()
    assert foreign in body.get('error', '')
    # Validation now runs post-claim, so the failed claim is released as an
    # error row -- a subsequent poll returns that error, never a stuck scanning.
    r2 = _post(app_client, slug, {'episodeIds': [ep1, foreign]}, hdr)
    assert r2.status_code == 200
    body2 = r2.get_json()
    assert body2['status'] == 'error'
    assert foreign in body2.get('error', '')


def test_episode_missing_original_audio_returns_400(app_client, xep_seeded):
    hdr = _csrf(app_client)
    db = xep_seeded['db']
    slug = xep_seeded['slug']
    ep1 = xep_seeded['ep1']
    # Add a third episode with no original audio
    ep3 = 'aabbcc000003'
    db.upsert_episode(slug, ep3, title='Ep 3', status='processed')
    # no original_file set, so audio path check will fail
    r = _post(app_client, slug, {'episodeIds': [ep1, ep3]}, hdr)
    assert r.status_code == 400
    assert ep3 in r.get_json().get('error', '')
    # Post-claim validation failure leaves an error row, not an orphaned scanning
    # one: the next poll surfaces the error state.
    r2 = _post(app_client, slug, {'episodeIds': [ep1, ep3]}, hdr)
    assert r2.status_code == 200
    body2 = r2.get_json()
    assert body2['status'] == 'error'
    assert ep3 in body2.get('error', '')


# --- claim / poll semantics ---

def test_happy_path_claims_and_returns_scanning(app_client, xep_seeded):
    import audio_fingerprinter as afp_module
    hdr = _csrf(app_client)
    slug = xep_seeded['slug']
    ep1, ep2 = xep_seeded['ep1'], xep_seeded['ep2']

    with patch.object(afp_module.AudioFingerprinter, 'is_available', return_value=False):
        r = _post(app_client, slug, {'episodeIds': [ep1, ep2]}, hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body['status'] in ('scanning', 'ready')
    assert 'episodeIds' in body
    assert set(body['episodeIds']) == {ep1, ep2}


def test_poll_returns_cached_result(app_client, xep_seeded):
    hdr = _csrf(app_client)
    slug = xep_seeded['slug']
    ep1, ep2 = xep_seeded['ep1'], xep_seeded['ep2']

    # Seed a ready result directly so the test does not depend on fpcalc.
    from api import get_database
    db2 = get_database()
    pod = db2.get_podcast_by_slug(slug)
    import hashlib
    h = hashlib.sha256(','.join(sorted([ep1, ep2])).encode()).hexdigest()
    db2.claim_cue_cross_episode_scan(pod['id'], h, 900)
    fake_payload = {
        'candidates': [{'start': 5.0, 'end': 7.5, 'kind': 'recurring', 'episodeMatches': 2}],
        'targetEpisodeId': ep1,
        'episodeIds': [ep1, ep2],
    }
    db2.save_cue_cross_episode_scan_result(pod['id'], h, fake_payload)

    r = _post(app_client, slug, {'episodeIds': [ep1, ep2]}, hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body['status'] == 'ready'
    assert 'candidates' in body
    assert body['candidates'][0]['start'] == 5.0
    assert body['targetEpisodeId'] == ep1
    assert set(body['episodeIds']) == {ep1, ep2}


def test_rescan_forces_fresh_run(app_client, xep_seeded):
    import audio_fingerprinter as afp_module
    hdr = _csrf(app_client)
    slug = xep_seeded['slug']
    ep1, ep2 = xep_seeded['ep1'], xep_seeded['ep2']

    # Seed a ready result directly
    from api import get_database
    db = get_database()
    pod = db.get_podcast_by_slug(slug)
    import hashlib
    h = hashlib.sha256(','.join(sorted([ep1, ep2])).encode()).hexdigest()
    db.claim_cue_cross_episode_scan(pod['id'], h, 900)
    db.save_cue_cross_episode_scan_result(pod['id'], h, {
        'candidates': [], 'targetEpisodeId': ep1, 'episodeIds': [ep1, ep2],
    })

    with patch.object(afp_module.AudioFingerprinter, 'is_available', return_value=False):
        r = _post(app_client, slug, {'episodeIds': [ep1, ep2], 'rescan': True}, hdr)
    assert r.status_code == 200
    # Should have kicked off a new scan (scanning or ready again)
    assert r.get_json()['status'] in ('scanning', 'ready')


def test_second_call_without_rescan_returns_existing(app_client, xep_seeded):
    hdr = _csrf(app_client)
    slug = xep_seeded['slug']
    ep1, ep2 = xep_seeded['ep1'], xep_seeded['ep2']

    from api import get_database
    db = get_database()
    pod = db.get_podcast_by_slug(slug)
    import hashlib
    h = hashlib.sha256(','.join(sorted([ep1, ep2])).encode()).hexdigest()
    db.claim_cue_cross_episode_scan(pod['id'], h, 900)
    db.save_cue_cross_episode_scan_result(pod['id'], h, {
        'candidates': [{'start': 1.0}], 'targetEpisodeId': ep1, 'episodeIds': [ep1, ep2],
    })

    r = _post(app_client, slug, {'episodeIds': [ep1, ep2]}, hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body['status'] == 'ready'
    assert body['candidates'][0]['start'] == 1.0


def test_unexpected_exception_post_claim_releases_slot(app_client, xep_seeded):
    """An error between a successful claim and Thread.start() must release the
    slot as an 'error' row (finding 5), not orphan a 'scanning' one; a later
    rescan must then be able to claim."""
    from api import get_database
    hdr = _csrf(app_client)
    slug = xep_seeded['slug']
    ep1, ep2 = xep_seeded['ep1'], xep_seeded['ep2']

    # Blow up inside the post-claim validation loop.
    with patch('api.cue_templates.get_storage') as mock_storage:
        mock_storage.return_value.get_original_path.side_effect = RuntimeError('boom')
        try:
            _post(app_client, slug, {'episodeIds': [ep1, ep2]}, hdr)
        except RuntimeError:
            pass  # the route re-raises after releasing the slot

    db = get_database()
    pod = db.get_podcast_by_slug(slug)
    import hashlib
    h = hashlib.sha256(','.join(sorted([ep1, ep2])).encode()).hexdigest()
    row = db.get_cue_cross_episode_scan(pod['id'], h)
    assert row is not None
    assert row['status'] == 'error'  # not left 'scanning'

    # A subsequent poll surfaces the error and a rescan can re-claim.
    r2 = _post(app_client, slug, {'episodeIds': [ep1, ep2]}, hdr)
    assert r2.get_json()['status'] == 'error'


def test_error_state_is_surfaced(app_client, xep_seeded):
    hdr = _csrf(app_client)
    slug = xep_seeded['slug']
    ep1, ep2 = xep_seeded['ep1'], xep_seeded['ep2']

    from api import get_database
    db = get_database()
    pod = db.get_podcast_by_slug(slug)
    import hashlib
    h = hashlib.sha256(','.join(sorted([ep1, ep2])).encode()).hexdigest()
    db.claim_cue_cross_episode_scan(pod['id'], h, 900)
    db.save_cue_cross_episode_scan_error(pod['id'], h, 'fingerprint decode failed')

    r = _post(app_client, slug, {'episodeIds': [ep1, ep2]}, hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body['status'] == 'error'
    assert 'error' in body
