"""Tests for the long-capture nudge (Phase 8, issue #350).

Verifies that the create-201 response includes longCapture and
captureWarnSeconds, and that the threshold is read from the config constant
(not hardcoded). The sensitive test uses a capture just barely over 5.0s so
it fails fast if the constant is ignored or the comparison is inverted.
"""
import os
import shutil
import sys
import tempfile
import wave

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

os.environ.setdefault(
    'MINUSPOD_DATA_DIR',
    tempfile.mkdtemp(prefix='long-capture-test-'),
)


def _write_wav(path, duration_s, sr=16000):
    """Write a WAV file containing a brief chirp in a silence bed."""
    n = int(sr * duration_s)
    audio = 0.01 * np.random.default_rng(42).standard_normal(n).astype(np.float32)
    # Plant a 0.3s chirp near the start so fingerprinting has something to grab.
    chirp_len = int(0.3 * sr)
    t = np.arange(chirp_len) / sr
    chirp = (0.7 * np.sin(2 * np.pi * 3500 * t)).astype(np.float32)
    audio[:chirp_len] = chirp
    pcm = (np.clip(audio, -1, 1) * 32767).astype('<i2')
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def _csrf(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')
    cookie = client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}


@pytest.fixture
def long_audio_env(app_client):
    """Feed + 10s episode for long-capture tests."""
    from api import get_database, get_storage
    db = get_database()
    storage = get_storage()
    slug = 'long-cap-feed'
    ep_id = 'a1b2c3d4e5f6'
    try:
        db.delete_podcast(slug)
    except Exception:
        pass
    db.create_podcast(slug, 'https://example.com/lc.xml', title='Long Cap Show')
    db.upsert_episode(slug, ep_id, title='Ep LC', status='processed')
    db.upsert_episode(slug, ep_id, status='processed', original_file='original.mp3')
    path = storage.get_original_path(slug, ep_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_wav(path, 10.0)
    yield {'slug': slug, 'ep_id': ep_id, 'db': db, 'path': path}
    try:
        db.delete_podcast(slug)
    except Exception:
        pass
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Sensitive test: 6s ad_break_boundary must produce longCapture=True.
# Fails immediately if AUDIO_CUE_CAPTURE_WARN_AD_SECONDS is ignored.
# ---------------------------------------------------------------------------

def test_long_ad_capture_flagged(app_client, long_audio_env):
    """6s ad_break_boundary -> longCapture true, captureWarnSeconds == 5.0."""
    if shutil.which('ffmpeg') is None:
        pytest.skip('ffmpeg not available')
    hdr = _csrf(app_client)
    slug, ep = long_audio_env['slug'], long_audio_env['ep_id']
    r = app_client.post(
        f'/api/v1/feeds/{slug}/cue-templates',
        json={'episodeId': ep, 'startS': 0.0, 'endS': 6.0,
              'cueType': 'ad_break_boundary'},
        headers=hdr,
    )
    assert r.status_code == 201, r.get_data(as_text=True)
    tpl = r.get_json()['template']
    # Core assertion: flag must fire for a 6s ad-break capture.
    assert tpl.get('longCapture') is True, (
        'longCapture must be True for a 6s ad_break_boundary capture '
        '(AUDIO_CUE_CAPTURE_WARN_AD_SECONDS = 5.0)'
    )
    # captureWarnSeconds must be present and match the config constant.
    from config import AUDIO_CUE_CAPTURE_WARN_AD_SECONDS
    assert 'captureWarnSeconds' in tpl
    assert tpl['captureWarnSeconds'] == AUDIO_CUE_CAPTURE_WARN_AD_SECONDS


# ---------------------------------------------------------------------------
# Non-ad type (show_intro) must never set longCapture regardless of duration.
# ---------------------------------------------------------------------------

def test_long_show_intro_not_flagged(app_client, long_audio_env):
    """6s show_intro -> longCapture absent or False (non-ad types are exempt)."""
    if shutil.which('ffmpeg') is None:
        pytest.skip('ffmpeg not available')
    hdr = _csrf(app_client)
    slug, ep = long_audio_env['slug'], long_audio_env['ep_id']
    r = app_client.post(
        f'/api/v1/feeds/{slug}/cue-templates',
        json={'episodeId': ep, 'startS': 0.0, 'endS': 6.0,
              'cueType': 'show_intro'},
        headers=hdr,
    )
    assert r.status_code == 201, r.get_data(as_text=True)
    tpl = r.get_json()['template']
    assert not tpl.get('longCapture'), (
        'show_intro is a non-ad type and must not set longCapture'
    )


# ---------------------------------------------------------------------------
# Ad capture under the threshold must not be flagged.
# ---------------------------------------------------------------------------

def test_short_ad_capture_not_flagged(app_client, long_audio_env):
    """3s ad_break_boundary -> longCapture False (under the 5s threshold)."""
    if shutil.which('ffmpeg') is None:
        pytest.skip('ffmpeg not available')
    hdr = _csrf(app_client)
    slug, ep = long_audio_env['slug'], long_audio_env['ep_id']
    r = app_client.post(
        f'/api/v1/feeds/{slug}/cue-templates',
        json={'episodeId': ep, 'startS': 0.0, 'endS': 3.0,
              'cueType': 'ad_break_boundary'},
        headers=hdr,
    )
    assert r.status_code == 201, r.get_data(as_text=True)
    tpl = r.get_json()['template']
    assert tpl.get('longCapture') is False, (
        'longCapture must be False for a 3s capture (under the 5s threshold)'
    )
