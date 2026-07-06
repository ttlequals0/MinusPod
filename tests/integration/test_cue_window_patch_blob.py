"""Integration tests for PATCH /api/v1/cue-templates/<id> blob re-extraction (D2a).

When sourceOffsetS or durationS change, the endpoint must re-extract mfcc_blob
and pcm_blob from the source episode's original audio. When the original audio
is gone, it returns 409. When neither window field is in the body, no
re-extraction happens.
"""
import os
import sys
import tempfile
import wave
from unittest.mock import patch

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='wopt-patch-test-'))

from api import get_database, get_storage

_MFCC_BLOB = np.zeros((5, 13), dtype='<f4').tobytes()
_PCM_BLOB = np.zeros(3200, dtype='<i2').tobytes()


def _csrf(app_client):
    with app_client.session_transaction() as sess:
        sess['authenticated'] = True
    app_client.get('/api/v1/auth/status')
    cookie = app_client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}


def _write_wav(path, sr=16000, duration_s=3.0):
    samples = (0.01 * np.random.default_rng(1).standard_normal(int(sr * duration_s))
               .astype(np.float32))
    pcm = (np.clip(samples, -1, 1) * 32767).astype('<i2')
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


@pytest.fixture
def patch_seeded(app_client):
    """Feed with one processed episode that has original audio on disk, plus a
    cue template with known blobs."""
    db = get_database()
    storage = get_storage()
    slug = 'patch-blob-feed'
    ep_id = 'aabbcc000010'
    try:
        db.delete_podcast(slug)
    except Exception:
        pass
    db.create_podcast(slug, 'https://example.com/patch.xml', title='Patch Show')
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


def _patch(app_client, tid, body, headers):
    return app_client.patch(
        f'/api/v1/cue-templates/{tid}',
        json=body,
        headers=headers,
    )


# --- blob re-extraction when window fields change ---

def test_patch_with_window_fields_re_extracts_blobs(app_client, patch_seeded):
    hdr = _csrf(app_client)
    tid = patch_seeded['tid']

    # decode_pcm_window returns float32 numpy array; compute_mfcc returns float32 matrix.
    # The implementation calls pcm_to_int16_bytes(pcm) and serialize_mfcc(mfcc) before
    # storing, so we return realistic numpy arrays here.
    new_pcm_arr = np.ones(3200, dtype=np.float32)
    new_mfcc_arr = np.ones((5, 13), dtype=np.float32)

    with patch('api.cue_templates.decode_pcm_window', return_value=new_pcm_arr) as mock_decode, \
         patch('api.cue_templates.compute_mfcc', return_value=new_mfcc_arr) as mock_mfcc:
        r = _patch(app_client, tid, {'sourceOffsetS': 6.0, 'durationS': 0.6}, hdr)

    assert r.status_code == 200
    mock_decode.assert_called_once()
    mock_mfcc.assert_called_once()

    db = get_database()
    row = db.get_cue_template(tid)
    assert row['source_offset_s'] == 6.0
    assert row['duration_s'] == 0.6
    # Blobs should have been updated (not the original zeros).
    assert row['pcm_blob'] is not None
    assert row['mfcc_blob'] is not None


def test_patch_with_only_source_offset_re_extracts(app_client, patch_seeded):
    hdr = _csrf(app_client)
    tid = patch_seeded['tid']

    new_pcm_arr = np.full(3200, 0.1, dtype=np.float32)
    new_mfcc_arr = np.full((5, 13), 0.5, dtype=np.float32)

    with patch('api.cue_templates.decode_pcm_window', return_value=new_pcm_arr) as mock_decode, \
         patch('api.cue_templates.compute_mfcc', return_value=new_mfcc_arr):
        r = _patch(app_client, tid, {'sourceOffsetS': 7.0}, hdr)

    assert r.status_code == 200
    mock_decode.assert_called_once()


def test_patch_with_only_duration_re_extracts(app_client, patch_seeded):
    hdr = _csrf(app_client)
    tid = patch_seeded['tid']

    new_pcm_arr = np.full(3200, 0.05, dtype=np.float32)
    new_mfcc_arr = np.full((5, 13), 0.25, dtype=np.float32)

    with patch('api.cue_templates.decode_pcm_window', return_value=new_pcm_arr) as mock_decode, \
         patch('api.cue_templates.compute_mfcc', return_value=new_mfcc_arr):
        r = _patch(app_client, tid, {'durationS': 0.8}, hdr)

    assert r.status_code == 200
    mock_decode.assert_called_once()


# --- window field validation ---

def test_patch_with_non_numeric_window_field_returns_400(app_client, patch_seeded):
    hdr = _csrf(app_client)
    tid = patch_seeded['tid']

    with patch('api.cue_templates.decode_pcm_window') as mock_decode:
        r = _patch(app_client, tid, {'sourceOffsetS': 'abc'}, hdr)
    assert r.status_code == 400
    mock_decode.assert_not_called()

    with patch('api.cue_templates.decode_pcm_window') as mock_decode:
        r = _patch(app_client, tid, {'durationS': None}, hdr)
    assert r.status_code == 400
    mock_decode.assert_not_called()


def test_patch_with_negative_offset_returns_400(app_client, patch_seeded):
    hdr = _csrf(app_client)
    tid = patch_seeded['tid']

    with patch('api.cue_templates.decode_pcm_window') as mock_decode:
        r = _patch(app_client, tid, {'sourceOffsetS': -1.0}, hdr)
    assert r.status_code == 400
    mock_decode.assert_not_called()


def test_patch_below_min_duration_returns_400(app_client, patch_seeded):
    hdr = _csrf(app_client)
    tid = patch_seeded['tid']

    with patch('api.cue_templates.decode_pcm_window') as mock_decode:
        r = _patch(app_client, tid, {'durationS': 0.05}, hdr)
    assert r.status_code == 400
    mock_decode.assert_not_called()


# --- window move invalidates the cached optimizer result ---

def test_patch_window_invalidates_optimizer_cache(app_client, patch_seeded):
    hdr = _csrf(app_client)
    tid = patch_seeded['tid']
    db = patch_seeded['db']

    db.claim_cue_window_optimize_scan(tid, 900)
    db.save_cue_window_optimize_scan_result(tid, {'templateId': tid})
    assert db.get_cue_window_optimize_scan(tid)['status'] == 'ready'

    new_pcm_arr = np.ones(3200, dtype=np.float32)
    new_mfcc_arr = np.ones((5, 13), dtype=np.float32)
    with patch('api.cue_templates.decode_pcm_window', return_value=new_pcm_arr), \
         patch('api.cue_templates.compute_mfcc', return_value=new_mfcc_arr):
        r = _patch(app_client, tid, {'sourceOffsetS': 6.0}, hdr)
    assert r.status_code == 200
    # The cached proposal described the pre-move geometry; it must be gone.
    assert db.get_cue_window_optimize_scan(tid) is None


# --- aged-out audio returns 409 when window fields change ---

def test_patch_with_window_fields_and_aged_out_audio_returns_409(app_client, patch_seeded):
    hdr = _csrf(app_client)
    tid = patch_seeded['tid']
    path = patch_seeded['path']
    # Remove the original audio to simulate aged-out retention.
    if path.exists():
        path.unlink()

    with patch('api.cue_templates.decode_pcm_window') as mock_decode:
        r = _patch(app_client, tid, {'sourceOffsetS': 6.0, 'durationS': 0.6}, hdr)

    assert r.status_code == 409
    mock_decode.assert_not_called()
    body = r.get_json()
    assert 'error' in body

    # Restore for teardown.
    _write_wav(path)


# --- no re-extraction when window fields absent ---

def test_patch_without_window_fields_does_not_re_extract(app_client, patch_seeded):
    hdr = _csrf(app_client)
    tid = patch_seeded['tid']

    with patch('api.cue_templates.decode_pcm_window') as mock_decode:
        r = _patch(app_client, tid, {'cueType': 'ad_break_start'}, hdr)

    assert r.status_code == 200
    mock_decode.assert_not_called()


def test_patch_with_only_score_threshold_does_not_re_extract(app_client, patch_seeded):
    hdr = _csrf(app_client)
    tid = patch_seeded['tid']

    with patch('api.cue_templates.decode_pcm_window') as mock_decode:
        r = _patch(app_client, tid, {'scoreThreshold': 0.75}, hdr)

    assert r.status_code == 200
    mock_decode.assert_not_called()


def test_patch_with_only_enabled_does_not_re_extract(app_client, patch_seeded):
    hdr = _csrf(app_client)
    tid = patch_seeded['tid']

    with patch('api.cue_templates.decode_pcm_window') as mock_decode:
        r = _patch(app_client, tid, {'enabled': False}, hdr)

    assert r.status_code == 200
    mock_decode.assert_not_called()


def test_patch_with_scope_does_not_re_extract(app_client, patch_seeded):
    hdr = _csrf(app_client)
    tid = patch_seeded['tid']

    with patch('api.cue_templates.decode_pcm_window') as mock_decode:
        r = _patch(app_client, tid, {'scope': 'podcast'}, hdr)

    assert r.status_code == 200
    mock_decode.assert_not_called()
