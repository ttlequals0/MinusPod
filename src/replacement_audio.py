"""Manage the replacement audio spliced in where an ad was cut.

The operator can upload their own file over the shipped default. Uploads land
on the data volume so they survive a redeploy, and are transcoded to MP3 so the
render path always gets the format the filename claims.
"""
import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from audio_processor import (
    get_replace_audio_path,
    get_uploaded_replace_audio_path,
)
from utils.subprocess_registry import tracked_run

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
# Every cut becomes this long, so a long file silently inflates every episode.
MAX_DURATION_SECONDS = 30.0
MIN_DURATION_SECONDS = 0.05
FFMPEG_TIMEOUT_S = 60

SOURCE_UPLOADED = 'uploaded'
# The image copies assets/ into assets_builtin/ too, so an operator cannot tell
# a bind-mounted override from the shipped file. Both report as the default.
SOURCE_DEFAULT = 'default'


class ReplacementAudioError(Exception):
    """Upload rejected. The message is shown to the operator verbatim."""


def _run(cmd, input_bytes=None, op_desc='ffmpeg') -> bytes:
    try:
        proc = tracked_run(cmd, input=input_bytes, capture_output=True,
                           timeout=FFMPEG_TIMEOUT_S)
    except subprocess.TimeoutExpired as e:
        raise ReplacementAudioError(f'{op_desc} timed out') from e
    except FileNotFoundError as e:
        raise ReplacementAudioError(f'{op_desc} is not installed') from e
    if proc.returncode != 0:
        stderr = (proc.stderr or b'').decode('utf-8', errors='replace')[:300]
        logger.warning("%s failed: %s", op_desc, stderr)
        raise ReplacementAudioError('That file could not be read as audio. Try an MP3 or WAV.')
    return proc.stdout or b''


def probe_audio(path: str) -> Dict[str, Any]:
    """Duration, channel count and sample rate of an audio file."""
    out = _run([
        'ffprobe', '-v', 'error', '-select_streams', 'a:0',
        '-show_entries', 'stream=channels,sample_rate:format=duration',
        '-of', 'json', str(path),
    ], op_desc='ffprobe')
    try:
        parsed = json.loads(out or b'{}') or {}
    except ValueError as e:
        raise ReplacementAudioError('That file could not be read as audio.') from e
    streams = parsed.get('streams') or []
    if not streams:
        raise ReplacementAudioError('That file has no audio track.')
    stream = streams[0]
    duration = parsed.get('format', {}).get('duration')
    return {
        'durationSeconds': round(float(duration), 3) if duration else None,
        'channels': int(stream.get('channels') or 0) or None,
        'sampleRateHz': int(stream.get('sample_rate') or 0) or None,
    }


def _source_of(path: str) -> str:
    if Path(path) == get_uploaded_replace_audio_path():
        return SOURCE_UPLOADED
    return SOURCE_DEFAULT


def describe() -> Dict[str, Any]:
    """Metadata for the replacement audio currently in use."""
    path = get_replace_audio_path()
    source = _source_of(path)
    info: Dict[str, Any] = {
        'source': source,
        'canRevert': source == SOURCE_UPLOADED,
        'exists': os.path.exists(path),
        'sizeBytes': None,
        'updatedAt': None,
        'durationSeconds': None,
        'channels': None,
        'sampleRateHz': None,
    }
    if not info['exists']:
        return info
    stat = os.stat(path)
    info['sizeBytes'] = stat.st_size
    info['updatedAt'] = int(stat.st_mtime)
    try:
        info.update(probe_audio(path))
    except ReplacementAudioError as e:
        # A corrupt file still has to render a page, so report rather than raise.
        logger.warning("Could not probe replacement audio %s: %s", path, e)
    return info


def _validate_probe(info: Dict[str, Any]) -> None:
    duration = info.get('durationSeconds')
    if duration is None:
        raise ReplacementAudioError('That file has no readable duration.')
    if duration > MAX_DURATION_SECONDS:
        raise ReplacementAudioError(
            f'That file is {duration:.1f} seconds. The limit is '
            f'{MAX_DURATION_SECONDS:.0f}, since every cut becomes this long.'
        )
    if duration < MIN_DURATION_SECONDS:
        raise ReplacementAudioError('That file is too short to hear.')


def save_upload(raw: bytes) -> Dict[str, Any]:
    """Validate, transcode to MP3 and install an uploaded replacement.

    Returns the new metadata. Raises ReplacementAudioError with an
    operator-facing message on any rejection.
    """
    if not raw:
        raise ReplacementAudioError('That file is empty.')
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ReplacementAudioError(
            f'That file is {len(raw) / 1024 / 1024:.1f} MB. The limit is '
            f'{MAX_UPLOAD_BYTES // 1024 // 1024} MB.'
        )

    target = get_uploaded_replace_audio_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    # Probe the source before transcoding so a long or non-audio file is
    # rejected without spending an encode on it.
    with tempfile.NamedTemporaryFile(suffix='.upload', delete=False) as src:
        src.write(raw)
        src_path = src.name
    tmp_out = None
    try:
        info = probe_audio(src_path)
        _validate_probe(info)

        # Encode into the data dir so the final rename cannot cross a
        # filesystem boundary and lose atomicity.
        fd, tmp_out = tempfile.mkstemp(suffix='.mp3', dir=str(target.parent))
        os.close(fd)
        _run([
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
            '-i', src_path, '-vn', '-c:a', 'libmp3lame', '-q:a', '2',
            tmp_out,
        ], op_desc='MP3 encode')

        result = probe_audio(tmp_out)
        _validate_probe(result)
        os.replace(tmp_out, target)
        tmp_out = None
    finally:
        for leftover in (src_path, tmp_out):
            if leftover and os.path.exists(leftover):
                os.unlink(leftover)

    logger.info(
        "Replacement audio uploaded: %.2fs, %sch, %s Hz",
        result.get('durationSeconds') or 0,
        result.get('channels'), result.get('sampleRateHz'),
    )
    return describe()


def revert() -> bool:
    """Remove an uploaded replacement so the next-best source applies again."""
    target = get_uploaded_replace_audio_path()
    if not target.exists():
        return False
    target.unlink()
    logger.info("Replacement audio reverted to the shipped default")
    return True


def current_file() -> Tuple[Optional[str], Optional[str]]:
    """(path, mimetype) for serving a preview, or (None, None) if absent."""
    path = get_replace_audio_path()
    if not os.path.exists(path):
        return None, None
    return path, 'audio/mpeg'


__all__ = [
    'MAX_DURATION_SECONDS', 'MAX_UPLOAD_BYTES', 'ReplacementAudioError',
    'SOURCE_DEFAULT', 'SOURCE_UPLOADED', 'current_file', 'describe',
    'probe_audio', 'revert', 'save_upload',
]
