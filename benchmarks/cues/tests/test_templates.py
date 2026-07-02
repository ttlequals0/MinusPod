"""Pure-logic tests for templates manifest parsing and rejection.

No network, no audio decode, no file I/O beyond fixtures.
"""
import io
import json
import struct
import wave
import zipfile
from pathlib import Path

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Minimal WAV builder (16 kHz mono s16) for unit tests that do not invoke ffmpeg
# ---------------------------------------------------------------------------

def _make_wav_bytes(n_frames: int = 8000, sample_rate: int = 16000) -> bytes:
    """Return raw WAV bytes: mono 16-bit at sample_rate."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        samples = np.zeros(n_frames, dtype="<i2")
        wf.writeframes(samples.tobytes())
    return buf.getvalue()


def _make_manifest(schema_version: int = 2, **overrides) -> dict:
    base = {
        "schemaVersion": schema_version,
        "appVersion": "2.0.0",
        "label": "test cue",
        "cueType": "content_transition",
        "durationS": 0.5,
        "sampleRate": 16000,
        "nCoeffs": 13,
        "sourceOffsetS": 0.0,
        "audioFile": "cue.flac",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tests: _validate_manifest
# ---------------------------------------------------------------------------

from cuebench.templates import _validate_manifest


class TestValidateManifest:
    def test_valid_schema_v1(self):
        _validate_manifest(_make_manifest(schema_version=1), Path("x"))

    def test_valid_schema_v2(self):
        _validate_manifest(_make_manifest(schema_version=2), Path("x"))

    def test_invalid_schema_version_string(self):
        with pytest.raises(ValueError, match="schemaVersion"):
            _validate_manifest(_make_manifest(schema_version="3"), Path("x"))

    def test_invalid_schema_version_zero(self):
        with pytest.raises(ValueError, match="schemaVersion"):
            _validate_manifest(_make_manifest(schema_version=0), Path("x"))

    def test_missing_schema_version(self):
        manifest = _make_manifest()
        del manifest["schemaVersion"]
        with pytest.raises(ValueError, match="schemaVersion"):
            _validate_manifest(manifest, Path("x"))

    def test_wrong_sample_rate_rejected(self):
        manifest = _make_manifest(sampleRate=44100)
        with pytest.raises(ValueError, match="sampleRate"):
            _validate_manifest(manifest, Path("x"))

    def test_correct_sample_rate_accepted(self):
        manifest = _make_manifest(sampleRate=16000)
        _validate_manifest(manifest, Path("x"))  # must not raise

    def test_missing_sample_rate_accepted(self):
        manifest = _make_manifest()
        del manifest["sampleRate"]
        _validate_manifest(manifest, Path("x"))  # optional field -- must not raise

    def test_wrong_n_coeffs_rejected(self):
        manifest = _make_manifest(nCoeffs=20)
        with pytest.raises(ValueError, match="nCoeffs"):
            _validate_manifest(manifest, Path("x"))

    def test_correct_n_coeffs_accepted(self):
        manifest = _make_manifest(nCoeffs=13)
        _validate_manifest(manifest, Path("x"))  # must not raise

    def test_missing_n_coeffs_accepted(self):
        manifest = _make_manifest()
        del manifest["nCoeffs"]
        _validate_manifest(manifest, Path("x"))  # optional field -- must not raise


# ---------------------------------------------------------------------------
# Tests: _decode_wav validation
# ---------------------------------------------------------------------------

from cuebench.templates import _decode_wav


class TestDecodeWav:
    def test_valid_wav(self):
        wav = _make_wav_bytes(n_frames=8000)
        pcm_blob, duration_s = _decode_wav(wav, Path("x"))
        assert len(pcm_blob) == 8000 * 2  # 16-bit
        assert abs(duration_s - 0.5) < 0.001

    def test_stereo_rejected(self):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00" * 3200)
        with pytest.raises(ValueError, match="mono"):
            _decode_wav(buf.getvalue(), Path("x"))

    def test_8bit_rejected(self):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(1)
            wf.setframerate(16000)
            wf.writeframes(b"\x80" * 1600)
        with pytest.raises(ValueError, match="16-bit"):
            _decode_wav(buf.getvalue(), Path("x"))

    def test_wrong_sample_rate_rejected(self):
        wav = _make_wav_bytes(n_frames=8000, sample_rate=44100)
        with pytest.raises(ValueError, match="sample rate"):
            _decode_wav(wav, Path("x"))


# ---------------------------------------------------------------------------
# Tests: _read_zip / _read_dir structural checks (no audio decode)
# ---------------------------------------------------------------------------

from cuebench.templates import _read_zip, _read_dir


class TestReadZip:
    def test_valid_zip(self, tmp_path):
        zpath = tmp_path / "cue.zip"
        with zipfile.ZipFile(zpath, "w") as z:
            z.writestr("cue.flac", b"FAKE_FLAC")
            z.writestr("template.json", json.dumps(_make_manifest()))
        flac, manifest = _read_zip(zpath)
        assert flac == b"FAKE_FLAC"
        assert manifest["label"] == "test cue"

    def test_missing_flac_raises(self, tmp_path):
        zpath = tmp_path / "bad.zip"
        with zipfile.ZipFile(zpath, "w") as z:
            z.writestr("template.json", json.dumps(_make_manifest()))
        with pytest.raises(ValueError, match="cue.flac"):
            _read_zip(zpath)

    def test_missing_manifest_raises(self, tmp_path):
        zpath = tmp_path / "bad.zip"
        with zipfile.ZipFile(zpath, "w") as z:
            z.writestr("cue.flac", b"data")
        with pytest.raises(ValueError, match="template.json"):
            _read_zip(zpath)


class TestReadDir:
    def test_valid_dir(self, tmp_path):
        (tmp_path / "cue.flac").write_bytes(b"FAKE_FLAC")
        (tmp_path / "template.json").write_text(json.dumps(_make_manifest()))
        flac, manifest = _read_dir(tmp_path)
        assert flac == b"FAKE_FLAC"
        assert manifest["cueType"] == "content_transition"

    def test_missing_flac_raises(self, tmp_path):
        (tmp_path / "template.json").write_text(json.dumps(_make_manifest()))
        with pytest.raises(ValueError, match="cue.flac"):
            _read_dir(tmp_path)

    def test_missing_manifest_raises(self, tmp_path):
        (tmp_path / "cue.flac").write_bytes(b"data")
        with pytest.raises(ValueError, match="template.json"):
            _read_dir(tmp_path)
