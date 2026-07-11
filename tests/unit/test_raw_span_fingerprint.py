"""generate_raw_span_fingerprint: extract-then-fpcalc -raw, mocked subprocess."""
import json
import subprocess
from unittest.mock import MagicMock, patch

from audio_fingerprinter import AudioFingerprinter


def _fp(monkeypatch):
    fp = AudioFingerprinter.__new__(AudioFingerprinter)
    fp._fpcalc_path = '/usr/bin/fpcalc'
    return fp


def test_returns_ints_and_duration(monkeypatch):
    fp = _fp(monkeypatch)
    fpcalc_out = MagicMock(returncode=0,
                           stdout=json.dumps({'fingerprint': [1, 2, 3, 4],
                                              'duration': 2.5}).encode())
    ffmpeg_out = MagicMock(returncode=0)
    with patch('audio_fingerprinter.tracked_run',
               side_effect=[ffmpeg_out, fpcalc_out]) as run:
        result = fp.generate_raw_span_fingerprint('/audio/ep.mp3', 100.0, 102.5)
    assert result == ([1, 2, 3, 4], 2.5)
    ffmpeg_cmd = run.call_args_list[0].args[0]
    assert '-ss' in ffmpeg_cmd and '100.0' in ffmpeg_cmd
    assert '-t' in ffmpeg_cmd and '2.5' in ffmpeg_cmd
    fpcalc_cmd = run.call_args_list[1].args[0]
    assert '-raw' in fpcalc_cmd


def test_no_fpcalc_returns_none():
    fp = AudioFingerprinter.__new__(AudioFingerprinter)
    fp._fpcalc_path = None
    assert fp.generate_raw_span_fingerprint('/audio/ep.mp3', 0.0, 1.0) is None


def test_ffmpeg_failure_returns_none(monkeypatch):
    fp = _fp(monkeypatch)
    with patch('audio_fingerprinter.tracked_run',
               side_effect=subprocess.CalledProcessError(1, ['ffmpeg'])):
        assert fp.generate_raw_span_fingerprint('/audio/ep.mp3', 0.0, 1.0) is None


def test_fpcalc_failure_returns_none(monkeypatch):
    fp = _fp(monkeypatch)
    ffmpeg_out = MagicMock(returncode=0)
    fpcalc_out = MagicMock(returncode=1, stderr=b'boom', stdout=b'')
    with patch('audio_fingerprinter.tracked_run',
               side_effect=[ffmpeg_out, fpcalc_out]):
        assert fp.generate_raw_span_fingerprint('/audio/ep.mp3', 0.0, 1.0) is None
