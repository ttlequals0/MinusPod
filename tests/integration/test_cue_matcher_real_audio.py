"""Integration test: real-audio cue fixtures against the template matcher (#350).

Uses two user-contributed show stingers (WSJ and Pivot, both 16 kHz mono s16
FLAC, committed under tests/fixtures/cues/). Each cue is loaded via
flac_to_wav + compute_mfcc, planted at two known positions in a seeded-noise
WAV, and the matcher is expected to find both within +-0.15 s.

Also verifies:
  - cross-noise guard: a WSJ template does not falsely fire on a Pivot haystack
    (measured peak score 0.47, well under the 0.60 guard).
  - formant_atten_db=12.0 still finds both plants (exercises PCM re-derivation
    path at cue_template_matcher.py:91-105; measured peak 0.91 with attenuation).

Requires ffmpeg on PATH. Skipped if unavailable.
"""
import io
import shutil
import wave
from pathlib import Path

import numpy as np
import pytest

from audio_analysis.cue_features import (
    SAMPLE_RATE_HZ,
    compute_mfcc,
    flac_to_wav,
    pcm_to_int16_bytes,
    serialize_mfcc,
)
from audio_analysis.cue_template_matcher import AudioCueTemplateMatcher


# Positive-match score floor derived from measured peaks (WSJ: 0.957, Pivot: 0.892).
# Threshold is measured_min - 0.05 rounded conservatively.
_SCORE_FLOOR = 0.85
# Cross-noise ceiling: WSJ template vs Pivot haystack measured 0.47.
# Threshold is measured + 0.13 to give comfortable headroom.
_CROSS_NOISE_CEIL = 0.60
# Localization tolerance in seconds.
_LOC_TOL_S = 0.15

_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "cues"
_WSJ_FLAC = _FIXTURE_DIR / "wsj_content_transition.flac"
_PIVOT_FLAC = _FIXTURE_DIR / "pivot_content_transition.flac"

_PLANT_TIMES = [6.0, 15.0]
_HAYSTACK_SECONDS = 25.0


def _load_cue(flac_path: Path):
    """Return (pcm float32, mfcc ndarray, duration_s) for a fixture FLAC file."""
    raw_flac = flac_path.read_bytes()
    wav_bytes = flac_to_wav(raw_flac, max_seconds=10.0)
    with wave.open(io.BytesIO(wav_bytes)) as wf:
        n_frames = wf.getnframes()
        raw_pcm = wf.readframes(n_frames)
        duration_s = n_frames / wf.getframerate()
    pcm = np.frombuffer(raw_pcm, dtype="<i2").astype(np.float32) / 32768.0
    mfcc = compute_mfcc(pcm)
    return pcm, mfcc, duration_s


def _write_wav(path: str, samples: np.ndarray,
               sample_rate: int = SAMPLE_RATE_HZ) -> None:
    pcm = np.clip(samples, -1.0, 1.0)
    pcm_int16 = (pcm * 32767).astype("<i2")
    with wave.open(path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_int16.tobytes())


def _plant_cue(cue_pcm: np.ndarray, haystack: np.ndarray,
               plant_times: list, sr: int = SAMPLE_RATE_HZ) -> None:
    """Overwrite haystack in-place with cue at each plant time (wet mix)."""
    n_total = len(haystack)
    for t_s in plant_times:
        start = int(t_s * sr)
        end = start + len(cue_pcm)
        if end <= n_total:
            haystack[start:end] = cue_pcm + 0.01 * haystack[start:end]


@pytest.fixture(scope="module")
def _ffmpeg_check():
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available; integration test skipped")


@pytest.fixture(scope="module")
def wsj_cue(_ffmpeg_check):
    """(pcm, mfcc, duration_s) for the WSJ stinger fixture."""
    return _load_cue(_WSJ_FLAC)


@pytest.fixture(scope="module")
def pivot_cue(_ffmpeg_check):
    """(pcm, mfcc, duration_s) for the Pivot stinger fixture."""
    return _load_cue(_PIVOT_FLAC)


@pytest.fixture
def wsj_haystack(tmp_path, wsj_cue):
    """25 s seeded-noise WAV with WSJ cue planted at 6.0 s and 15.0 s."""
    wsj_pcm, _, _ = wsj_cue
    rng = np.random.default_rng(42)
    noise = (0.02 * rng.standard_normal(
        int(_HAYSTACK_SECONDS * SAMPLE_RATE_HZ)).astype(np.float32))
    _plant_cue(wsj_pcm, noise, _PLANT_TIMES)
    path = str(tmp_path / "wsj_haystack.wav")
    _write_wav(path, noise)
    return path


@pytest.fixture
def pivot_haystack(tmp_path, pivot_cue):
    """25 s seeded-noise WAV with Pivot cue planted at 6.0 s and 15.0 s."""
    pivot_pcm, _, _ = pivot_cue
    rng = np.random.default_rng(42)
    noise = (0.02 * rng.standard_normal(
        int(_HAYSTACK_SECONDS * SAMPLE_RATE_HZ)).astype(np.float32))
    _plant_cue(pivot_pcm, noise, _PLANT_TIMES)
    path = str(tmp_path / "pivot_haystack.wav")
    _write_wav(path, noise)
    return path


@pytest.fixture
def pivot_noise_haystack(tmp_path, pivot_cue):
    """25 s seeded-noise WAV with Pivot cue planted (used for cross-noise guard)."""
    pivot_pcm, _, _ = pivot_cue
    rng = np.random.default_rng(99)
    noise = (0.02 * rng.standard_normal(
        int(_HAYSTACK_SECONDS * SAMPLE_RATE_HZ)).astype(np.float32))
    _plant_cue(pivot_pcm, noise, _PLANT_TIMES)
    path = str(tmp_path / "pivot_cross_haystack.wav")
    _write_wav(path, noise)
    return path


def _template_row(tid, label, mfcc, duration_s, pcm=None):
    row = {
        "id": tid,
        "label": label,
        "mfcc_blob": serialize_mfcc(mfcc),
        "duration_s": duration_s,
        "n_coeffs": mfcc.shape[1],
    }
    if pcm is not None:
        row["pcm_blob"] = pcm_to_int16_bytes(pcm)
    return row


def test_wsj_cue_found_at_plant_times(wsj_cue, wsj_haystack):
    """WSJ template detects both planted occurrences within +-0.15 s at score >= 0.85.

    Measured peak score on the planted haystack: 0.957.
    """
    wsj_pcm, wsj_mfcc, wsj_dur = wsj_cue
    row = _template_row(1, "wsj", wsj_mfcc, wsj_dur)
    matcher = AudioCueTemplateMatcher(
        templates=[row], score_threshold=_SCORE_FLOOR)
    signals, debug = matcher.detect_with_debug(wsj_haystack)

    # Positive-score assertion: the planted match must clear the floor.
    peak = debug["templates"][0]["peak_score"]
    # Measured: 0.957. Floor set at 0.85 (measured - 0.10 margin).
    assert peak >= _SCORE_FLOOR, (
        f"WSJ peak {peak:.3f} below floor {_SCORE_FLOOR} "
        f"(measured baseline: 0.957)")

    starts = sorted(s.start for s in signals)
    for t_s in _PLANT_TIMES:
        assert any(abs(s - t_s) <= _LOC_TOL_S for s in starts), (
            f"no detection within {_LOC_TOL_S}s of WSJ plant at {t_s}s; "
            f"got {starts}")


def test_pivot_cue_found_at_plant_times(pivot_cue, pivot_haystack):
    """Pivot template detects both planted occurrences within +-0.15 s at score >= 0.85.

    Measured peak score on the planted haystack: 0.892.
    """
    pivot_pcm, pivot_mfcc, pivot_dur = pivot_cue
    row = _template_row(2, "pivot", pivot_mfcc, pivot_dur)
    matcher = AudioCueTemplateMatcher(
        templates=[row], score_threshold=_SCORE_FLOOR)
    signals, debug = matcher.detect_with_debug(pivot_haystack)

    peak = debug["templates"][0]["peak_score"]
    # Measured: 0.892. Floor set at 0.85 (measured - 0.04 margin).
    assert peak >= _SCORE_FLOOR, (
        f"Pivot peak {peak:.3f} below floor {_SCORE_FLOOR} "
        f"(measured baseline: 0.892)")

    starts = sorted(s.start for s in signals)
    for t_s in _PLANT_TIMES:
        assert any(abs(s - t_s) <= _LOC_TOL_S for s in starts), (
            f"no detection within {_LOC_TOL_S}s of Pivot plant at {t_s}s; "
            f"got {starts}")


def test_cross_noise_wsj_template_vs_pivot_haystack(wsj_cue, pivot_noise_haystack):
    """WSJ template must not falsely fire on a Pivot-seeded haystack.

    Cross-noise guard: peak < 0.60. Measured: 0.47 (gap of 0.13 to ceiling).
    This confirms the two jingles are spectrally distinct enough for safe
    per-feed template matching.
    """
    _, wsj_mfcc, wsj_dur = wsj_cue
    row = _template_row(1, "wsj", wsj_mfcc, wsj_dur)
    # Use a very low threshold so detect_with_debug returns the actual peak
    # regardless of whether it clears the production threshold.
    matcher = AudioCueTemplateMatcher(templates=[row], score_threshold=0.01)
    _, debug = matcher.detect_with_debug(pivot_noise_haystack)

    peak = debug["templates"][0]["peak_score"]
    # Measured: 0.471. Ceiling is measured + 0.13, rounded to 0.60.
    assert peak < _CROSS_NOISE_CEIL, (
        f"WSJ-vs-Pivot cross peak {peak:.3f} exceeds ceiling {_CROSS_NOISE_CEIL} "
        f"(measured baseline: 0.471); jingles may not be spectrally distinct")


def test_formant_atten_db_still_finds_both_plants(wsj_cue, pivot_cue,
                                                   wsj_haystack, pivot_haystack):
    """formant_atten_db=12.0 triggers PCM re-derivation (matcher.py:91-105) and
    still detects both planted occurrences for both cues.

    Measured peak scores with attenuation: WSJ 0.913, Pivot 0.917.
    """
    for cue_label, cue_fixtures, hay_path, measured_peak in [
        ("wsj", wsj_cue, wsj_haystack, 0.913),
        ("pivot", pivot_cue, pivot_haystack, 0.917),
    ]:
        pcm, mfcc, dur = cue_fixtures
        # pcm_blob must be present so the matcher takes the re-derivation path.
        row = _template_row(1, cue_label, mfcc, dur, pcm=pcm)
        matcher = AudioCueTemplateMatcher(
            templates=[row],
            score_threshold=_SCORE_FLOOR,
            formant_atten_db=12.0,
        )
        signals, debug = matcher.detect_with_debug(hay_path)

        peak = debug["templates"][0]["peak_score"]
        assert peak >= _SCORE_FLOOR, (
            f"{cue_label} formant_atten peak {peak:.3f} below floor {_SCORE_FLOOR} "
            f"(measured baseline: {measured_peak})")

        starts = sorted(s.start for s in signals)
        for t_s in _PLANT_TIMES:
            assert any(abs(s - t_s) <= _LOC_TOL_S for s in starts), (
                f"no detection within {_LOC_TOL_S}s of {cue_label} plant at "
                f"{t_s}s with formant_atten_db=12; got {starts}")
