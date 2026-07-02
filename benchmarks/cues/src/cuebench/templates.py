"""Load cue template exports (zip or dir) and produce matcher-ready row dicts."""
from __future__ import annotations

import io
import json
import logging
import wave
import zipfile
from pathlib import Path
from typing import List, Dict, Any

# Imported after sys.path bootstrap via __init__.py
from audio_analysis.cue_features import (
    SAMPLE_RATE_HZ,
    N_COEFFS,
    compute_mfcc,
    flac_to_wav,
    int16_bytes_to_pcm,
    serialize_mfcc,
)


# Offline permissive bound = settings max for intro/outro captures
TEMPLATE_DECODE_MAX_SECONDS = 120.0
_SCHEMA_VERSION = 2


def load_template(source: Path) -> Dict[str, Any]:
    """Load a single template from *source* (zip file or directory).

    The export format is:
      - cue.flac   16 kHz mono s16 FLAC
      - template.json  manifest (schemaVersion, label, cueType, durationS, ...)

    Validation mirrors the import route in api/cue_templates.py lines 601-621:
    - mono, 16-bit, 16 kHz
    - at least 3 MFCC frames

    Returns a dict compatible with AudioCueTemplateMatcher's *templates* list:
    keys id, label, cue_type, duration_s, n_coeffs, mfcc_blob, pcm_blob.

    Raises ValueError on validation failures.
    """
    source = Path(source)
    if source.suffix.lower() == ".zip":
        flac_bytes, manifest = _read_zip(source)
    elif source.is_dir():
        flac_bytes, manifest = _read_dir(source)
    else:
        raise ValueError(f"source must be a .zip file or directory: {source}")

    _validate_manifest(manifest, source)

    wav_bytes = flac_to_wav(flac_bytes, TEMPLATE_DECODE_MAX_SECONDS)
    pcm_blob, duration_s = _decode_wav(wav_bytes, source)

    pcm = int16_bytes_to_pcm(pcm_blob)
    mfcc = compute_mfcc(pcm)
    if mfcc.shape[0] < 3:
        raise ValueError(f"{source}: cue audio is too short (< 3 MFCC frames)")

    label = manifest.get("label") or str(source.name)
    cue_type = manifest.get("cueType") or "content_transition"

    return {
        "id": hash(str(source)) & 0x7FFFFFFF,
        "label": label,
        "cue_type": cue_type,
        "duration_s": duration_s,
        "n_coeffs": N_COEFFS,
        "mfcc_blob": serialize_mfcc(mfcc),
        "pcm_blob": pcm_blob,
    }


_logger = logging.getLogger("cuebench.templates")


def load_templates(sources: List[Path]) -> List[Dict[str, Any]]:
    """Load and validate multiple template sources. Skips failures with a warning."""
    logger = _logger
    rows = []
    for src in sources:
        try:
            rows.append(load_template(src))
        except Exception as e:
            logger.warning("skipping template %s: %s", src, e)
    return rows


# -- internal helpers --

def _read_zip(path: Path):
    with zipfile.ZipFile(path, "r") as z:
        names = z.namelist()
        if "cue.flac" not in names:
            raise ValueError(f"{path}: zip missing cue.flac")
        if "template.json" not in names:
            raise ValueError(f"{path}: zip missing template.json")
        flac_bytes = z.read("cue.flac")
        manifest = json.loads(z.read("template.json"))
    return flac_bytes, manifest


def _read_dir(path: Path):
    flac_path = path / "cue.flac"
    manifest_path = path / "template.json"
    if not flac_path.exists():
        raise ValueError(f"{path}: directory missing cue.flac")
    if not manifest_path.exists():
        raise ValueError(f"{path}: directory missing template.json")
    flac_bytes = flac_path.read_bytes()
    manifest = json.loads(manifest_path.read_text())
    return flac_bytes, manifest


def _validate_manifest(manifest: dict, source: Path) -> None:
    version = manifest.get("schemaVersion")
    if version not in (1, 2):
        raise ValueError(
            f"{source}: unsupported schemaVersion {version!r}; expected 1 or 2"
        )
    sample_rate = manifest.get("sampleRate")
    if sample_rate is not None and sample_rate != SAMPLE_RATE_HZ:
        raise ValueError(
            f"{source}: manifest sampleRate {sample_rate!r} != {SAMPLE_RATE_HZ};"
            f" re-export the cue at {SAMPLE_RATE_HZ} Hz"
        )
    n_coeffs = manifest.get("nCoeffs")
    if n_coeffs is not None and n_coeffs != N_COEFFS:
        raise ValueError(
            f"{source}: manifest nCoeffs {n_coeffs!r} != {N_COEFFS};"
            f" re-export the cue with {N_COEFFS} MFCC coefficients"
        )


def _decode_wav(wav_bytes: bytes, source: Path):
    """Validate WAV header and return (pcm_blob bytes, duration_s float).

    ffmpeg writes nframes=INT_MAX when encoding to a pipe (unknown size).
    Derive duration from the actual PCM bytes read rather than from the
    WAV header nframes field to avoid that sentinel value.
    """
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        if wf.getnchannels() != 1:
            raise ValueError(
                f"{source}: cue audio must be mono (1 channel), "
                f"got {wf.getnchannels()}"
            )
        if wf.getsampwidth() != 2:
            raise ValueError(
                f"{source}: cue audio must be 16-bit PCM, "
                f"got {wf.getsampwidth()} bytes/sample"
            )
        sr = wf.getframerate()
        if sr != SAMPLE_RATE_HZ:
            raise ValueError(
                f"{source}: sample rate must be {SAMPLE_RATE_HZ}, got {sr}"
            )
        # Read all available PCM bytes; do not trust wf.getnframes() because
        # ffmpeg pipe output sets it to INT_MAX as a placeholder.
        pcm_blob = wf.readframes(wf.getnframes())
    bytes_per_frame = 2  # 16-bit mono
    actual_frames = len(pcm_blob) // bytes_per_frame
    duration_s = round(actual_frames / float(sr), 3)
    return pcm_blob, duration_s
