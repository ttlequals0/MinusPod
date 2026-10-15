"""Audio utility functions.

Provides shared audio file operations used across multiple modules.
"""

import json
import logging
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from config import FFMPEG_SHORT_TIMEOUT, FFPROBE_TIMEOUT
from storage import _detect_image_mime
from utils.subprocess_registry import tracked_run
from utils.ffmpeg_run import SAFE_MEDIA_INPUT_ARGS, SAFE_MEDIA_PROBE_ARGS, decode_stderr, ffmpeg_timeout

logger = logging.getLogger(__name__)


def get_audio_codec(audio_path: str) -> str | None:
    """Codec name of the first audio stream via ffprobe (e.g. 'mp3',
    'aac'), or None when it cannot be determined."""
    cmd = [
        'ffprobe', *SAFE_MEDIA_PROBE_ARGS, '-v', 'error',
        '-select_streams', 'a:0',
        '-show_entries', 'stream=codec_name',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        audio_path
    ]
    try:
        result = tracked_run(cmd, capture_output=True, text=True,
                             timeout=FFPROBE_TIMEOUT)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().lower()
        logger.warning(f"ffprobe codec query failed for {audio_path}: "
                       f"{result.stderr or 'no output'}")
    except Exception as e:
        logger.warning(f"Codec query failed for {audio_path}: {e}")
    return None


@dataclass(frozen=True)
class RenderInput:
    """What a render needs from its input file, read by one ffprobe call."""
    duration: float | None
    audio_format: tuple[int, int, str] | None  # (sample_rate, channels, channel_layout)
    chapters: list[dict]  # raw ffprobe chapter entries


def probe_render_input(audio_path: str) -> RenderInput | None:
    """Duration, first audio stream format and chapters of a file, or None when ffprobe fails."""
    cmd = [
        'ffprobe', *SAFE_MEDIA_PROBE_ARGS, '-v', 'warning',
        '-select_streams', 'a:0',
        '-show_entries', 'format=duration:stream=sample_rate,channels,channel_layout',
        '-show_chapters', '-of', 'json',
        audio_path
    ]
    try:
        result = tracked_run(cmd, capture_output=True, timeout=FFPROBE_TIMEOUT)
        if result.returncode != 0:
            logger.warning(f"ffprobe failed for {audio_path}: "
                           f"{result.stderr.decode('utf-8', errors='replace') or 'no output'}")
            return None
        info = json.loads(result.stdout.decode('utf-8', errors='replace'))
    except Exception as e:
        logger.warning(f"Render input probe failed for {audio_path}: {e}")
        return None
    try:
        duration = float(info['format']['duration'])
    except (KeyError, TypeError, ValueError):
        duration = None
    if duration is not None:
        duration = _exact_duration(audio_path, duration, decode_stderr(result))
    try:
        stream = info['streams'][0]
        audio_format = (int(stream['sample_rate']), int(stream['channels']),
                        stream.get('channel_layout') or '')
    except (KeyError, IndexError, TypeError, ValueError):
        audio_format = None
    return RenderInput(duration, audio_format, info.get('chapters') or [])


# ffprobe's stderr wording for an MP3 with no Xing/Info/VBRI header; the JSON output carries no flag.
_ESTIMATED_DURATION = 'Estimating duration from bitrate'


def _decoded_duration(audio_path: str, estimate: float) -> float | None:
    """Exact length from a full decode, for files whose header only lets ffprobe estimate it."""
    cmd = [
        'ffmpeg', *SAFE_MEDIA_INPUT_ARGS, '-v', 'error', '-nostats',
        '-i', audio_path, '-vn', '-f', 'null', '-progress', 'pipe:1', '-',
    ]
    try:
        result = tracked_run(cmd, capture_output=True, text=True, timeout=ffmpeg_timeout(estimate))
    except Exception as e:
        logger.warning(f"Decode for exact duration failed for {audio_path}: {e}")
        return None
    times = re.findall(r'^out_time_us=(\d+)$', result.stdout, re.MULTILINE)
    if result.returncode == 0 and times:
        return int(times[-1]) / 1_000_000
    logger.warning(f"Decode for exact duration failed for {audio_path}: {result.stderr or 'no output'}")
    return None


def _exact_duration(audio_path: str, probed: float, stderr: str) -> float:
    """The probed duration, replaced by a decoded one when ffprobe said it was estimating."""
    if _ESTIMATED_DURATION not in stderr:
        return probed
    cached = AudioMetadata.lookup(audio_path)
    if cached is not None:
        return cached
    decoded = _decoded_duration(audio_path, probed)
    if decoded is not None:
        logger.info(f"Header duration {probed:.2f}s was an estimate; decoded length is {decoded:.2f}s for {audio_path}")
    # A failed decode keeps the estimate, cached so the file is not decoded on every later probe.
    AudioMetadata.store(audio_path, decoded if decoded is not None else probed)
    return decoded if decoded is not None else probed


def get_audio_duration(audio_path: str) -> float | None:
    """Get audio duration in seconds using ffprobe.

    Args:
        audio_path: Path to audio file

    Returns:
        Duration in seconds, or None if unable to determine
    """
    cached = AudioMetadata.lookup(audio_path)
    if cached is not None:
        return cached
    cmd = [
        'ffprobe', *SAFE_MEDIA_PROBE_ARGS, '-v', 'warning',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        audio_path
    ]
    try:
        result = tracked_run(
            cmd,
            capture_output=True,
            text=True,
            timeout=FFPROBE_TIMEOUT
        )
        if result.returncode == 0 and result.stdout.strip():
            return _exact_duration(audio_path, float(result.stdout.strip()), result.stderr or '')
        logger.warning(f"ffprobe failed for {audio_path}: {result.stderr or 'no output'}")
    except subprocess.TimeoutExpired:
        logger.warning(f"ffprobe timeout for {audio_path}")
    except ValueError as e:
        logger.warning(f"Failed to parse duration for {audio_path}: {e}")
    except Exception as e:
        logger.warning(f"Duration query failed for {audio_path}: {e}")
    return None


_MEAN_VOLUME_RE = re.compile(r'mean_volume:\s*(-?[\d.]+|-inf) dB')


def mean_volume_db(audio_path: str, start: float | None = None,
                   duration: float | None = None) -> float | None:
    """Mean volume in dB from ffmpeg volumedetect over an optional range, or None when unreadable."""
    seek = ['-ss', str(start)] if start is not None else []
    span = ['-t', str(duration)] if duration is not None else []
    cmd = [
        'ffmpeg', *SAFE_MEDIA_INPUT_ARGS, '-hide_banner', '-nostats',
        *seek, '-i', audio_path, *span, '-vn', '-af', 'volumedetect', '-f', 'null', '-',
    ]
    try:
        result = tracked_run(cmd, capture_output=True, text=True,
                             timeout=FFMPEG_SHORT_TIMEOUT)
    except Exception as e:
        logger.warning(f"Volume probe failed for {audio_path}: {e}")
        return None
    match = _MEAN_VOLUME_RE.search(result.stderr or '')
    if not match:
        return None
    return float(match.group(1))


def extract_embedded_artwork(audio_path: str) -> tuple[bytes, str] | None:
    """First embedded picture stream as (bytes, 'image/jpeg'|'image/png').

    Stream-copies the video (picture) stream to a single frame via ffmpeg
    (``-an -codec:v copy -frames:v 1``) rather than decoding, so this works
    for any embedded cover regardless of its original codec. Returns None
    when the file has no embedded picture, ffmpeg fails, the output is not
    a JPEG/PNG (magic-number checked, mirroring the upload validation path
    in storage.py), or the probe times out.
    """
    fd, out_path = tempfile.mkstemp(suffix='.img')
    os.close(fd)
    try:
        cmd = [
            'ffmpeg', *SAFE_MEDIA_INPUT_ARGS, '-y', '-i', audio_path,
            '-an', '-codec:v', 'copy', '-frames:v', '1',
            '-f', 'image2', out_path,
        ]
        result = tracked_run(cmd, capture_output=True, timeout=FFPROBE_TIMEOUT)
        if result.returncode != 0:
            return None
        data = Path(out_path).read_bytes()
        if not data:
            return None
        mime = _detect_image_mime(data)
        if mime not in ('image/jpeg', 'image/png'):
            return None
        return data, mime
    except subprocess.TimeoutExpired:
        logger.warning(f"Embedded artwork extraction timeout for {audio_path}")
        return None
    except (OSError, subprocess.SubprocessError) as e:
        logger.warning(f"Embedded artwork extraction failed for {audio_path}: {e}")
        return None
    finally:
        try:
            os.unlink(out_path)
        except FileNotFoundError:
            pass


class AudioMetadata:
    """Cached audio file metadata to avoid redundant ffprobe calls.

    Usage:
        duration = AudioMetadata.get_duration('/path/to/audio.mp3')
    """

    _MAX_CACHE_SIZE = 500
    _cache: ClassVar[dict[str, tuple[float, tuple[int, int]]]] = {}  # path -> (duration, (mtime_ns, size))

    @staticmethod
    def _stamp(path: str) -> tuple[int, int]:
        st = os.stat(path)
        return st.st_mtime_ns, st.st_size

    @classmethod
    def lookup(cls, path: str) -> float | None:
        """Cached duration, or None when the file is unknown or changed since."""
        try:
            stamp = cls._stamp(path)
        except OSError:
            return None
        cached = cls._cache.get(path)
        return cached[0] if cached and cached[1] == stamp else None

    @classmethod
    def store(cls, path: str, duration: float) -> None:
        try:
            cls._cache[path] = (duration, cls._stamp(path))
        except OSError:
            return
        while len(cls._cache) > cls._MAX_CACHE_SIZE:
            cls._cache.pop(next(iter(cls._cache)))

    @classmethod
    def get_duration(cls, path: str) -> float | None:
        """Get audio duration with caching based on file modification time."""
        cached = cls.lookup(path)
        if cached is not None:
            return cached
        duration = get_audio_duration(path)
        if duration is not None:
            cls.store(path, duration)
        return duration

    @classmethod
    def invalidate(cls, path: str) -> None:
        """Remove a specific path from the cache."""
        cls._cache.pop(path, None)
