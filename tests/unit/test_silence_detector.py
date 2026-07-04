"""Unit tests for SilenceDetector (task B2).

ffmpeg is never invoked; tracked_run is patched throughout. Tests cover:
- Paired silence_start / silence_end lines parsed correctly.
- Unterminated trailing silence_start closed at file duration.
- Interleaved non-silencedetect lines tolerated.
- Timeout -> [].
- Nonzero exit -> [].
- noise_db and min_silence_s appear in the -af argument.
- Spans sorted by start.
- File-not-found -> [].
"""
import os
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from audio_analysis.silence_detector import SilenceDetector

_AUDIO_PATH = '/fake/episode.mp3'
_DURATION = 600.0  # 10 minutes


def _stderr(lines):
    """Join lines into bytes as ffmpeg would emit on stderr."""
    return '\n'.join(lines).encode('utf-8')


def _make_result(stderr_lines, returncode=0):
    return SimpleNamespace(
        returncode=returncode,
        stderr=_stderr(stderr_lines),
        stdout=b'',
    )


def _patch_run(mock_result):
    """Return three patches: tracked_run, get_audio_duration, os.path.exists."""
    run_patch = patch(
        'audio_analysis.silence_detector.tracked_run',
        return_value=mock_result,
    )
    dur_patch = patch(
        'audio_analysis.silence_detector.get_audio_duration',
        return_value=_DURATION,
    )
    exists_patch = patch('os.path.exists', return_value=True)
    return run_patch, dur_patch, exists_patch


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def test_paired_lines_parsed():
    stderr = [
        '[silencedetect @ 0x...] silence_start: 10.5',
        '[silencedetect @ 0x...] silence_end: 13.2 | silence_duration: 2.7',
    ]
    result = _make_result(stderr)
    run_p, dur_p, ex_p = _patch_run(result)
    with run_p, dur_p, ex_p:
        spans = SilenceDetector(noise_db=-50.0, min_silence_s=0.3).detect(_AUDIO_PATH)
    assert len(spans) == 1
    s = spans[0]
    assert s['start'] == 10.5
    assert s['end'] == 13.2
    assert abs(s['duration'] - 2.7) < 0.01


def test_multiple_pairs_sorted_by_start():
    stderr = [
        '[silencedetect @ 0x...] silence_start: 30.0',
        '[silencedetect @ 0x...] silence_end: 31.5 | silence_duration: 1.5',
        '[silencedetect @ 0x...] silence_start: 5.0',
        '[silencedetect @ 0x...] silence_end: 6.2 | silence_duration: 1.2',
    ]
    result = _make_result(stderr)
    run_p, dur_p, ex_p = _patch_run(result)
    with run_p, dur_p, ex_p:
        spans = SilenceDetector(noise_db=-50.0, min_silence_s=0.3).detect(_AUDIO_PATH)
    assert len(spans) == 2
    assert spans[0]['start'] < spans[1]['start']
    assert spans[0]['start'] == 5.0
    assert spans[1]['start'] == 30.0


def test_unterminated_trailing_silence_closed_at_duration():
    stderr = [
        '[silencedetect @ 0x...] silence_start: 595.0',
        # No silence_end follows -- file ends mid-silence.
    ]
    result = _make_result(stderr)
    run_p, dur_p, ex_p = _patch_run(result)
    with run_p, dur_p, ex_p:
        spans = SilenceDetector(noise_db=-50.0, min_silence_s=0.3).detect(_AUDIO_PATH)
    assert len(spans) == 1
    s = spans[0]
    assert s['start'] == 595.0
    assert s['end'] == _DURATION
    assert abs(s['duration'] - (_DURATION - 595.0)) < 0.01


def test_interleaved_non_silencedetect_lines_tolerated():
    stderr = [
        'ffmpeg version 6.0',
        'Input #0, mp3, from /fake/episode.mp3:',
        '[silencedetect @ 0x...] silence_start: 20.0',
        '  Duration: 00:10:00.00',
        '[silencedetect @ 0x...] silence_end: 22.5 | silence_duration: 2.5',
        'video:0kB audio:45000kB',
    ]
    result = _make_result(stderr)
    run_p, dur_p, ex_p = _patch_run(result)
    with run_p, dur_p, ex_p:
        spans = SilenceDetector(noise_db=-50.0, min_silence_s=0.3).detect(_AUDIO_PATH)
    assert len(spans) == 1
    assert spans[0]['start'] == 20.0


def test_no_silence_lines_returns_empty():
    stderr = ['ffmpeg version 6.0', 'Output: nothing interesting']
    result = _make_result(stderr)
    run_p, dur_p, ex_p = _patch_run(result)
    with run_p, dur_p, ex_p:
        spans = SilenceDetector(noise_db=-50.0, min_silence_s=0.3).detect(_AUDIO_PATH)
    assert spans == []


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------

def test_timeout_returns_empty():
    dur_patch = patch(
        'audio_analysis.silence_detector.get_audio_duration',
        return_value=_DURATION,
    )
    run_patch = patch(
        'audio_analysis.silence_detector.tracked_run',
        side_effect=subprocess.TimeoutExpired(cmd='ffmpeg', timeout=300),
    )
    with run_patch, dur_patch:
        spans = SilenceDetector(noise_db=-50.0, min_silence_s=0.3).detect(_AUDIO_PATH)
    assert spans == []


def test_nonzero_exit_returns_empty():
    result = _make_result(
        ['[silencedetect @ 0x...] silence_start: 10.0'],
        returncode=1,
    )
    run_p, dur_p, ex_p = _patch_run(result)
    with run_p, dur_p, ex_p:
        spans = SilenceDetector(noise_db=-50.0, min_silence_s=0.3).detect(_AUDIO_PATH)
    assert spans == []


def test_tracked_run_exception_returns_empty():
    dur_patch = patch(
        'audio_analysis.silence_detector.get_audio_duration',
        return_value=_DURATION,
    )
    run_patch = patch(
        'audio_analysis.silence_detector.tracked_run',
        side_effect=OSError('no ffmpeg'),
    )
    with run_patch, dur_patch:
        spans = SilenceDetector(noise_db=-50.0, min_silence_s=0.3).detect(_AUDIO_PATH)
    assert spans == []


def test_file_not_found_returns_empty():
    # No patches needed -- the detector bails before calling ffmpeg.
    spans = SilenceDetector(noise_db=-50.0, min_silence_s=0.3).detect('/nonexistent/path.mp3')
    assert spans == []


def test_duration_unavailable_returns_empty():
    dur_patch = patch(
        'audio_analysis.silence_detector.get_audio_duration',
        return_value=None,
    )
    with patch('os.path.exists', return_value=True), dur_patch:
        spans = SilenceDetector(noise_db=-50.0, min_silence_s=0.3).detect(_AUDIO_PATH)
    assert spans == []


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------

def test_noise_db_and_min_silence_in_af_argument():
    """The -af flag must encode both noise_db and min_silence_s."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured['cmd'] = cmd
        return _make_result([])

    dur_patch = patch(
        'audio_analysis.silence_detector.get_audio_duration',
        return_value=_DURATION,
    )
    run_patch = patch('audio_analysis.silence_detector.tracked_run', side_effect=fake_run)
    with run_patch, dur_patch:
        with patch('os.path.exists', return_value=True):
            SilenceDetector(noise_db=-42.0, min_silence_s=0.5).detect(_AUDIO_PATH)

    cmd = captured['cmd']
    af_idx = cmd.index('-af')
    af_value = cmd[af_idx + 1]
    assert 'silencedetect' in af_value
    assert '-42.0dB' in af_value or 'noise=-42.0dB' in af_value
    assert '0.5' in af_value


def test_timeout_scales_with_duration():
    """Timeout for a 3600s file should be >= 300 and <= 1200."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured['kwargs'] = kwargs
        return _make_result([])

    dur_patch = patch(
        'audio_analysis.silence_detector.get_audio_duration',
        return_value=3600.0,
    )
    run_patch = patch('audio_analysis.silence_detector.tracked_run', side_effect=fake_run)
    with run_patch, dur_patch:
        with patch('os.path.exists', return_value=True):
            SilenceDetector(noise_db=-50.0, min_silence_s=0.3).detect(_AUDIO_PATH)

    timeout = captured['kwargs']['timeout']
    assert 300 <= timeout <= 1200
