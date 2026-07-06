"""Integration tests for POST .../optimize-window endpoint (D2a).

Covers: validation, claim/poll semantics, rescan, aged-out audio 409, error
surface, and the worker payload shape. Mirrors test_cue_cross_episode_scan_api.py
but keyed by template_id instead of (podcast_id, episode_set_hash).
"""
import os
import sys
import tempfile
import wave
from unittest.mock import patch

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='wopt-api-test-'))

from api import get_database, get_storage

_MFCC_BLOB = np.zeros((5, 13), dtype='<f4').tobytes()
_PCM_BLOB = np.zeros(3200, dtype='<i2').tobytes()


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
def wopt_seeded(app_client):
    """Feed with one processed episode that has retained original audio, plus one
    cue template whose source_episode_id points at that episode."""
    db = get_database()
    storage = get_storage()
    slug = 'wopt-feed'
    ep_id = 'aabbcc000001'
    try:
        db.delete_podcast(slug)
    except Exception:
        pass
    db.create_podcast(slug, 'https://example.com/wopt.xml', title='WOpt Show')
    db.upsert_episode(slug, ep_id, title='Ep 1', status='processed')
    db.upsert_episode(slug, ep_id, status='processed', original_file='original.mp3')
    path = storage.get_original_path(slug, ep_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_wav(path)

    pid = db.get_podcast_by_slug(slug)['id']
    tid = db.create_cue_template(
        podcast_id=pid,
        cue_type='ad_break_boundary',
        source_episode_id=ep_id,
        source_offset_s=5.0,
        duration_s=0.5,
        sample_rate=16000,
        n_coeffs=13,
        mfcc_blob=_MFCC_BLOB,
        pcm_blob=_PCM_BLOB,
        pcm_sample_rate=16000,
    )
    yield {'slug': slug, 'ep_id': ep_id, 'db': db, 'storage': storage,
           'path': path, 'tid': tid, 'pid': pid}
    try:
        db.delete_podcast(slug)
    except Exception:
        pass


def _post(app_client, slug, tid, body, headers):
    return app_client.post(
        f'/api/v1/feeds/{slug}/cue-templates/{tid}/optimize-window',
        json=body,
        headers=headers,
    )


# --- validation ---

def test_unknown_feed_returns_404(app_client):
    hdr = _csrf(app_client)
    r = _post(app_client, 'no-such-feed', 1, {}, hdr)
    assert r.status_code == 404


def test_unknown_template_returns_404(app_client, wopt_seeded):
    hdr = _csrf(app_client)
    slug = wopt_seeded['slug']
    r = _post(app_client, slug, 99999, {}, hdr)
    assert r.status_code == 404


def test_template_belonging_to_different_feed_returns_404(app_client, wopt_seeded):
    hdr = _csrf(app_client)
    db = get_database()
    other_slug = 'wopt-other-feed'
    try:
        db.delete_podcast(other_slug)
    except Exception:
        pass
    db.create_podcast(other_slug, 'https://example.com/other.xml', title='Other')
    tid = wopt_seeded['tid']
    # The template belongs to wopt_seeded's feed, not other_slug.
    r = _post(app_client, other_slug, tid, {}, hdr)
    assert r.status_code == 404
    try:
        db.delete_podcast(other_slug)
    except Exception:
        pass


def test_aged_out_source_audio_returns_409(app_client, wopt_seeded):
    hdr = _csrf(app_client)
    slug = wopt_seeded['slug']
    tid = wopt_seeded['tid']
    path = wopt_seeded['path']
    # Remove the original audio file to simulate aged-out retention.
    if path.exists():
        path.unlink()
    r = _post(app_client, slug, tid, {}, hdr)
    assert r.status_code == 409
    body = r.get_json()
    assert 'aged out' in body.get('error', '').lower() or 'original audio' in body.get('error', '').lower()
    # Restore so fixture teardown does not error.
    _write_wav(path)


# --- claim / poll semantics ---

def test_happy_path_returns_scanning_or_ready(app_client, wopt_seeded):
    hdr = _csrf(app_client)
    slug = wopt_seeded['slug']
    tid = wopt_seeded['tid']

    # Patch the worker internals so we do not require ffmpeg or a real matcher.
    with patch('api.cue_templates._run_cue_window_optimize_scan'):
        r = _post(app_client, slug, tid, {}, hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body['status'] == 'scanning'
    assert body['templateId'] == tid


def test_poll_returns_cached_result_when_ready(app_client, wopt_seeded):
    hdr = _csrf(app_client)
    slug = wopt_seeded['slug']
    tid = wopt_seeded['tid']

    db = get_database()
    # Seed a ready result directly so the test does not depend on the worker.
    db.claim_cue_window_optimize_scan(tid, 900)
    fake_result = {
        'proposedStartS': 4.75,
        'proposedEndS': 5.25,
        'meanPeakScore': 0.88,
        'baselineMeanPeakScore': 0.70,
        'perEpisode': [{'episodeId': wopt_seeded['ep_id'], 'peakScore': 0.88}],
        'baselineWindow': {'startS': 5.0, 'endS': 5.5},
        'templateId': tid,
    }
    db.save_cue_window_optimize_scan_result(tid, fake_result)

    r = _post(app_client, slug, tid, {}, hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body['status'] == 'ready'
    assert body['templateId'] == tid
    assert body['proposedStartS'] == 4.75
    assert body['meanPeakScore'] == 0.88
    assert 'perEpisode' in body
    assert 'baselineWindow' in body


def test_rescan_forces_fresh_run(app_client, wopt_seeded):
    hdr = _csrf(app_client)
    slug = wopt_seeded['slug']
    tid = wopt_seeded['tid']

    db = get_database()
    db.claim_cue_window_optimize_scan(tid, 900)
    db.save_cue_window_optimize_scan_result(tid, {
        'proposedStartS': 5.0, 'proposedEndS': 5.5,
        'meanPeakScore': 0.7, 'baselineMeanPeakScore': 0.7,
        'perEpisode': [], 'baselineWindow': {'startS': 5.0, 'endS': 5.5},
        'templateId': tid,
    })

    with patch('api.cue_templates._run_cue_window_optimize_scan'):
        r = _post(app_client, slug, tid, {'rescan': True}, hdr)
    assert r.status_code == 200
    body = r.get_json()
    # The patched worker never completes, so a forced rescan reports scanning.
    assert body['status'] == 'scanning'


def test_second_call_without_rescan_returns_existing(app_client, wopt_seeded):
    hdr = _csrf(app_client)
    slug = wopt_seeded['slug']
    tid = wopt_seeded['tid']

    db = get_database()
    db.claim_cue_window_optimize_scan(tid, 900)
    db.save_cue_window_optimize_scan_result(tid, {
        'proposedStartS': 3.1, 'proposedEndS': 3.6,
        'meanPeakScore': 0.95, 'baselineMeanPeakScore': 0.80,
        'perEpisode': [], 'baselineWindow': {'startS': 3.0, 'endS': 3.5},
        'templateId': tid,
    })

    r = _post(app_client, slug, tid, {}, hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body['status'] == 'ready'
    assert body['proposedStartS'] == 3.1


def test_scanning_in_progress_returns_scanning(app_client, wopt_seeded):
    hdr = _csrf(app_client)
    slug = wopt_seeded['slug']
    tid = wopt_seeded['tid']

    db = get_database()
    # Claim without completing -> scanning state
    db.claim_cue_window_optimize_scan(tid, 900)

    r = _post(app_client, slug, tid, {}, hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body['status'] == 'scanning'
    assert body['templateId'] == tid


def test_error_state_is_surfaced(app_client, wopt_seeded):
    hdr = _csrf(app_client)
    slug = wopt_seeded['slug']
    tid = wopt_seeded['tid']

    db = get_database()
    db.claim_cue_window_optimize_scan(tid, 900)
    db.save_cue_window_optimize_scan_error(tid, 'decode blew up')

    r = _post(app_client, slug, tid, {}, hdr)
    assert r.status_code == 200
    body = r.get_json()
    assert body['status'] == 'error'
    assert 'error' in body
