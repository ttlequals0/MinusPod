"""Unit tests for the cue window optimizer worker (D2a).

Grid generation, tie-break math, baseline comparison, and payload shape.
The matcher and decode are mocked so no ffmpeg or real audio is needed.
"""
import json
import os
import sys
import tempfile
from unittest.mock import patch

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

# Minimum window duration the worker should skip (from config).
from config import AUDIO_CUE_CAPTURE_MIN_SECONDS
from api.cue_templates import (
    _build_optimize_grid, _pick_best_candidate, _run_cue_window_optimize_scan,
)
from database import Database


def _make_template(source_offset_s=5.0, duration_s=1.0, source_episode_id='aabb00000001'):
    """Minimal template dict for worker tests."""
    mfcc_blob = np.zeros((10, 13), dtype='<f4').tobytes()
    pcm_blob = np.zeros(16000, dtype='<i2').tobytes()
    return {
        'id': 1,
        'podcast_id': 1,
        'label': 'test-cue',
        'cue_type': 'ad_break_boundary',
        'source_episode_id': source_episode_id,
        'source_offset_s': source_offset_s,
        'duration_s': duration_s,
        'sample_rate': 16000,
        'n_coeffs': 13,
        'mfcc_blob': mfcc_blob,
        'pcm_blob': pcm_blob,
        'pcm_sample_rate': 16000,
        'scope': 'podcast',
        'network_id': None,
        'enabled': 1,
        'score_threshold': None,
    }


def _make_pcm(duration_s=10.0, sr=16000):
    """Return a float32 PCM array of `duration_s` seconds."""
    n = int(sr * duration_s)
    return np.zeros(n, dtype=np.float32)


# ---------------------------------------------------------------------------
# Grid generation tests
# ---------------------------------------------------------------------------

def test_grid_candidates_bounded():
    """The candidate grid should have at most ~121 entries (11x11)."""
    template = _make_template(source_offset_s=5.0, duration_s=1.0)
    candidates = _build_optimize_grid(template)
    # 11x11 = 121 minus any skipped-negative-start or below-min-duration entries.
    assert len(candidates) <= 121
    assert len(candidates) > 0


def test_grid_includes_baseline():
    """The (0.0, 0.0) delta candidate must be present."""
    template = _make_template(source_offset_s=5.0, duration_s=1.0)
    candidates = _build_optimize_grid(template)
    deltas = [(round(c['start_delta'], 6), round(c['end_delta'], 6)) for c in candidates]
    assert (0.0, 0.0) in deltas


def test_grid_skips_negative_start():
    """Candidates that would result in start_s < 0 are skipped."""
    template = _make_template(source_offset_s=0.1, duration_s=1.0)
    candidates = _build_optimize_grid(template)
    for c in candidates:
        assert c['start_s'] >= 0.0


def test_grid_skips_below_min_duration():
    """Candidates that shrink the window below AUDIO_CUE_CAPTURE_MIN_SECONDS are skipped."""
    # Short duration so tail-in cuts can push it below the floor.
    template = _make_template(source_offset_s=5.0, duration_s=0.3)
    candidates = _build_optimize_grid(template)
    for c in candidates:
        dur = c['end_s'] - c['start_s']
        assert dur >= AUDIO_CUE_CAPTURE_MIN_SECONDS - 1e-9


# ---------------------------------------------------------------------------
# Mean score and tie-break math
# ---------------------------------------------------------------------------

def test_best_candidate_has_highest_mean_score():
    """pick_best_candidate selects the candidate with the highest mean_peak_score."""
    candidates = [
        {'start_delta': 0.0, 'end_delta': 0.0, 'mean_peak_score': 0.70},
        {'start_delta': -0.1, 'end_delta': 0.1, 'mean_peak_score': 0.85},
        {'start_delta': 0.2, 'end_delta': 0.0, 'mean_peak_score': 0.80},
    ]
    best = _pick_best_candidate(candidates)
    assert best['mean_peak_score'] == 0.85


def test_tie_break_prefers_least_delta():
    """When two candidates share the highest mean, prefer the one with smallest |delta|."""
    candidates = [
        {'start_delta': 0.3, 'end_delta': 0.2, 'mean_peak_score': 0.90},
        {'start_delta': 0.1, 'end_delta': 0.1, 'mean_peak_score': 0.90},
        {'start_delta': 0.0, 'end_delta': 0.0, 'mean_peak_score': 0.88},
    ]
    best = _pick_best_candidate(candidates)
    # |0.1| + |0.1| = 0.2 < |0.3| + |0.2| = 0.5
    assert best['start_delta'] == 0.1
    assert best['end_delta'] == 0.1


def test_baseline_wins_when_no_improvement():
    """If no candidate beats baseline, proposed == baseline (delta 0,0 is returned)."""
    candidates = [
        {'start_delta': 0.0, 'end_delta': 0.0, 'mean_peak_score': 0.70},
        {'start_delta': -0.1, 'end_delta': 0.2, 'mean_peak_score': 0.65},
    ]
    best = _pick_best_candidate(candidates)
    assert best['start_delta'] == 0.0
    assert best['end_delta'] == 0.0


# ---------------------------------------------------------------------------
# Payload shape
# ---------------------------------------------------------------------------

def test_worker_produces_correct_payload_shape():
    """_run_cue_window_optimize_scan saves a payload with all required fields."""
    tmpdir = tempfile.mkdtemp(prefix='wopt-worker-test-')
    db = Database(data_dir=tmpdir)

    pid = db.create_podcast('wopt-worker-feed', 'http://x/w.xml', 'W')
    ep_id = 'aabbcc000020'
    db.upsert_episode('wopt-worker-feed', ep_id, title='Ep', status='processed')
    db.upsert_episode('wopt-worker-feed', ep_id, status='processed', original_file='orig.mp3')
    template = _make_template(source_episode_id=ep_id)
    tid = db.create_cue_template(
        podcast_id=pid,
        cue_type=template['cue_type'],
        source_episode_id=ep_id,
        source_offset_s=template['source_offset_s'],
        duration_s=template['duration_s'],
        sample_rate=template['sample_rate'],
        n_coeffs=template['n_coeffs'],
        mfcc_blob=template['mfcc_blob'],
        pcm_blob=template['pcm_blob'],
        pcm_sample_rate=template['pcm_sample_rate'],
    )
    db.claim_cue_window_optimize_scan(tid, 900)

    fake_audio_path = os.path.join(tmpdir, 'orig.mp3')
    with open(fake_audio_path, 'wb') as f:
        f.write(b'\x00' * 100)

    # Mock out decode and matcher to avoid ffmpeg and real audio processing.
    fake_pcm = _make_pcm(10.0)
    fake_mfcc = np.zeros((10, 13), dtype=np.float32)

    with patch('api.cue_templates.get_database', return_value=db), \
         patch('api.cue_templates.decode_pcm_window', return_value=fake_pcm), \
         patch('api.cue_templates.compute_mfcc', return_value=fake_mfcc), \
         patch('api.cue_templates.peak_zncc', return_value=(0.75, 0)):
        _run_cue_window_optimize_scan(tid, fake_audio_path, [])

    row = db.get_cue_window_optimize_scan(tid)
    assert row is not None
    assert row['status'] == 'ready', row.get('error')
    payload = json.loads(row['result_json'])
    for key in ('proposedStartS', 'proposedEndS', 'meanPeakScore',
                'baselineMeanPeakScore', 'perEpisode', 'baselineWindow', 'templateId'):
        assert key in payload, f"missing key {key!r} in payload"
    assert isinstance(payload['perEpisode'], list)
    assert 'startS' in payload['baselineWindow']
    assert 'endS' in payload['baselineWindow']
