"""Whole-file fingerprint positions convert to seconds at Chromaprint's own point rate.

fpcalc emits one point per 1365-sample hop at 11025 Hz but withholds about 21 points of every file to its filter
windows. Pairing the points with the audio duration fpcalc reports makes points / duration run slow, so a position
an hour into an episode landed about 2.5 s late.
"""
import json
import random
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np

from audio_fingerprinter import (
    FINGERPRINT_NOMINAL_FPS, AudioFingerprinter, enumerate_window_occurrences, match_fingerprints,
)

RATE = 11025 / 1365          # Chromaprint's default hop: 4096 - (4096 - 4096 // 3) samples
AUDIO_S = 3600.0
WITHHELD = 21                # Points fpcalc never emits: the chroma and classifier filter windows
KW = dict(strictness=0.65, gap_tolerance_s=3.5)


def _fpcalc_like(spot_at, spot_s=20.0, seed=3):
    """An hour of random points shaped like fpcalc output, with one spot planted at each time in ``spot_at``."""
    rng = random.Random(seed)
    ep = [rng.getrandbits(32) - (1 << 31) for _ in range(int(AUDIO_S * RATE) - WITHHELD)]
    spot = [rng.getrandbits(32) - (1 << 31) for _ in range(int(spot_s * RATE))]
    for at in spot_at:
        i = int(round(at * RATE))
        ep[i:i + len(spot)] = spot
    return ep, spot


def _fingerprinter(ep):
    db = MagicMock()
    db.get_setting_bool.return_value = True
    db.get_setting_float.side_effect = lambda key, default=None: default
    fp = AudioFingerprinter(db=db)
    fp._fpcalc_path = '/usr/bin/fpcalc'
    stdout = json.dumps({'duration': AUDIO_S, 'fingerprint': ep}).encode()
    run = patch('audio_fingerprinter.tracked_run',
                return_value=SimpleNamespace(returncode=0, stdout=stdout, stderr=b''))
    return fp, run


def test_nominal_rate_is_chromaprints_integer_hop():
    assert FINGERPRINT_NOMINAL_FPS == RATE


def test_full_fingerprint_spans_its_points_at_the_nominal_rate():
    ep, _ = _fpcalc_like([])
    fp, run = _fingerprinter(ep)
    with run:
        ints, span = fp._generate_full_fingerprint('/missing.mp3')
    assert span == len(ep) / RATE


def test_repeat_late_in_the_episode_lands_on_its_copy():
    ep, _ = _fpcalc_like([20.0, 3500.0])
    fp, run = _fingerprinter(ep)
    with run:
        (_, match), = fp.find_in_episode_repeats('/missing.mp3', [(20.0, 40.0)], [(20.0, 40.0)], AUDIO_S)
    assert abs(match.start - 3500.0) < 0.25 and abs(match.end - 3520.0) < 0.25


def test_stored_pattern_late_in_the_episode_lands_on_its_copy():
    ep, spot = _fpcalc_like([3300.0])
    fp, run = _fingerprinter(ep)
    with run:
        ints, span = fp._generate_full_fingerprint('/missing.mp3')
    (match,) = match_fingerprints(ints, span, [(1, spot, 20.0, None, None)], min_coverage=0.7, **KW)
    assert abs(match.start - 3300.0) < 0.25 and abs(match.end - 3320.0) < 0.25


def test_window_occurrence_late_in_the_episode_lands_on_its_copy():
    ep, spot = _fpcalc_like([3300.0])
    fp, run = _fingerprinter(ep)
    with run:
        ints, span = fp._generate_full_fingerprint('/missing.mp3')
    window = np.asarray(spot[:int(4 * RATE)], dtype=np.int64).astype(np.uint32)
    (start, _end), = enumerate_window_occurrences(window, ints, span, 0.9)
    assert abs(start - 3300.0) < 0.25
