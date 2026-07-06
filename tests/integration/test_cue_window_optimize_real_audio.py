"""Real-audio optimizer test (D2a, #350).

Plants the same chirp in a source and a sibling episode, marks a deliberately
rough template window (0.2s of noise on each side, mirroring issue #350's
cue-21 vs cue-23), and asserts the optimizer proposes a tighter window with a
higher mean peak score. Runs the real decode / MFCC / ZNCC path end to end.

Requires ffmpeg on PATH; skipped if unavailable.
"""
import json
import shutil
import tempfile
import wave

import numpy as np
import pytest

from api.cue_templates import _run_cue_window_optimize_scan
from audio_analysis.cue_features import SAMPLE_RATE_HZ
from database import Database

CHIRP_DURATION_S = 0.6
SOURCE_CHIRP_AT_S = 5.0
SIBLING_CHIRP_AT_S = 12.0
# Rough capture: 0.2s of background noise on each side of the chirp.
ROUGH_START_S = 4.8
ROUGH_DURATION_S = 1.0


def _chirp(duration_s, sample_rate=SAMPLE_RATE_HZ):
    t = np.arange(int(sample_rate * duration_s)) / sample_rate
    freq = 3000 + (5000 - 3000) * (t / max(duration_s, 1e-9))
    phase = 2 * np.pi * np.cumsum(freq) / sample_rate
    env = np.sin(np.pi * t / duration_s) ** 2
    return (0.6 * env * np.sin(phase)).astype(np.float32)


def _write_episode_wav(path, chirp_at_s, seed, total_seconds=30.0):
    sr = SAMPLE_RATE_HZ
    background = (0.02 * np.random.default_rng(seed)
                  .standard_normal(int(sr * total_seconds)).astype(np.float32))
    chirp = _chirp(CHIRP_DURATION_S)
    start = int(chirp_at_s * sr)
    background[start:start + len(chirp)] = chirp
    pcm_int16 = (np.clip(background, -1.0, 1.0) * 32767).astype('<i2')
    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm_int16.tobytes())


def test_optimizer_tightens_rough_window(tmp_path, monkeypatch):
    if shutil.which('ffmpeg') is None:
        pytest.skip('ffmpeg not available; integration test skipped')

    source_path = tmp_path / 'source.wav'
    sibling_path = tmp_path / 'sibling.wav'
    _write_episode_wav(source_path, SOURCE_CHIRP_AT_S, seed=0)
    _write_episode_wav(sibling_path, SIBLING_CHIRP_AT_S, seed=1)

    db = Database(data_dir=tempfile.mkdtemp(prefix='wopt-real-test-'))
    pid = db.create_podcast('wopt-real-feed', 'http://x/real.xml', 'Real')
    tid = db.create_cue_template(
        podcast_id=pid,
        cue_type='ad_break_boundary',
        source_episode_id='aabbcc0000f0',
        source_offset_s=ROUGH_START_S,
        duration_s=ROUGH_DURATION_S,
        sample_rate=SAMPLE_RATE_HZ,
        n_coeffs=13,
        mfcc_blob=b'\x00' * (5 * 13 * 4),
        pcm_blob=b'\x00' * (3200 * 2),
        pcm_sample_rate=SAMPLE_RATE_HZ,
    )
    assert db.claim_cue_window_optimize_scan(tid, 900) == 'started'

    monkeypatch.setattr('api.cue_templates.get_database', lambda: db)
    _run_cue_window_optimize_scan(
        tid, str(source_path), [('aabbcc0000f1', str(sibling_path))])

    row = db.get_cue_window_optimize_scan(tid)
    assert row['status'] == 'ready', row.get('error')
    payload = json.loads(row['result_json'])

    # The optimizer must beat the rough baseline and land inside the chirp.
    # It may trim into the quiet sin^2 onset/tail (those frames are noise-
    # dominated, so dropping them raises ZNCC) but must never keep or grow
    # the surrounding background noise the rough window captured.
    chirp_end_s = SOURCE_CHIRP_AT_S + CHIRP_DURATION_S
    assert payload['meanPeakScore'] > payload['baselineMeanPeakScore']
    assert SOURCE_CHIRP_AT_S - 0.05 <= payload['proposedStartS'] <= SOURCE_CHIRP_AT_S + 0.3
    assert chirp_end_s - 0.3 <= payload['proposedEndS'] <= chirp_end_s + 0.05
    # Both episodes scored, and the sibling match is strong for the trim.
    assert len(payload['perEpisode']) == 2
    sibling_scores = [e['peakScore'] for e in payload['perEpisode']
                     if e['episodeId'] == 'aabbcc0000f1']
    assert sibling_scores and sibling_scores[0] > 0.9
