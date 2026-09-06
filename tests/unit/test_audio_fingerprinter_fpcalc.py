"""Issue #690: fpcalc exits non-zero after recoverable decode glitches while
still emitting a complete fingerprint. All three call sites must parse stdout
before honoring the exit code."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from audio_fingerprinter import AudioFingerprinter


def _result(returncode=0, stdout=b'', stderr=b''):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


_FULL_FP_STDOUT = b'{"duration": 3259.01, "fingerprint": [1, 2, 3, 4]}'
_COMPRESSED_FP_STDOUT = b'{"duration": 120.0, "fingerprint": "AQAA0Q"}'


@pytest.fixture
def fp():
    f = AudioFingerprinter.__new__(AudioFingerprinter)
    f.db = None
    f._fpcalc_path = '/usr/bin/fpcalc'
    return f


class TestGenerateFingerprint:
    def test_accepts_valid_stdout_on_nonzero_exit(self, fp):
        with patch('audio_fingerprinter.tracked_run',
                   return_value=_result(3, _COMPRESSED_FP_STDOUT, b'ERROR: decode glitch')):
            result = fp.generate_fingerprint('/tmp/x.mp3')
        assert result is not None
        assert result.fingerprint == 'AQAA0Q'
        assert result.duration == 120.0

    def test_none_on_garbage_stdout_with_zero_exit(self, fp):
        with patch('audio_fingerprinter.tracked_run',
                   return_value=_result(0, b'not json')):
            assert fp.generate_fingerprint('/tmp/x.mp3') is None

    def test_none_on_empty_stdout_with_nonzero_exit(self, fp):
        with patch('audio_fingerprinter.tracked_run',
                   return_value=_result(1, b'', b'fatal')):
            assert fp.generate_fingerprint('/tmp/x.mp3') is None


class TestGenerateFullFingerprint:
    def test_accepts_valid_stdout_on_nonzero_exit(self, fp):
        with patch('audio_fingerprinter.tracked_run',
                   return_value=_result(3, _FULL_FP_STDOUT, b'ERROR: decode glitch')):
            result = fp._generate_full_fingerprint('/tmp/x.mp3')
        assert result == ([1, 2, 3, 4], 3259.01)

    def test_none_on_empty_fingerprint_list(self, fp):
        stdout = b'{"duration": 10.0, "fingerprint": []}'
        with patch('audio_fingerprinter.tracked_run',
                   return_value=_result(0, stdout)):
            assert fp._generate_full_fingerprint('/tmp/x.mp3') is None

    def test_none_on_garbage_stdout_with_nonzero_exit(self, fp):
        with patch('audio_fingerprinter.tracked_run',
                   return_value=_result(1, b'garbage', b'fatal')):
            assert fp._generate_full_fingerprint('/tmp/x.mp3') is None


class TestGenerateRawSpanFingerprint:
    def test_accepts_valid_stdout_on_nonzero_exit(self, fp):
        with patch('audio_fingerprinter.tracked_run',
                   return_value=_result(3, _FULL_FP_STDOUT, b'ERROR: decode glitch')):
            result = fp.generate_raw_span_fingerprint('/tmp/x.mp3', 10.0, 20.0)
        assert result == ([1, 2, 3, 4], 3259.01)

    def test_none_on_empty_stdout(self, fp):
        with patch('audio_fingerprinter.tracked_run',
                   return_value=_result(1, b'', b'fatal')):
            assert fp.generate_raw_span_fingerprint('/tmp/x.mp3', 10.0, 20.0) is None
