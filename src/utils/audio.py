"""Audio utility functions.

Provides shared audio file operations used across multiple modules.
"""

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import ClassVar

from config import FFPROBE_TIMEOUT
from storage import _detect_image_mime
from utils.subprocess_registry import tracked_run

logger = logging.getLogger(__name__)


def get_audio_codec(audio_path: str) -> str | None:
    """Codec name of the first audio stream via ffprobe (e.g. 'mp3',
    'aac'), or None when it cannot be determined."""
    cmd = [
        'ffprobe', '-v', 'error',
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


def get_audio_duration(audio_path: str) -> float | None:
    """Get audio duration in seconds using ffprobe.

    Args:
        audio_path: Path to audio file

    Returns:
        Duration in seconds, or None if unable to determine
    """
    cmd = [
        'ffprobe', '-v', 'error',
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
            return float(result.stdout.strip())
        logger.warning(f"ffprobe failed for {audio_path}: {result.stderr or 'no output'}")
    except subprocess.TimeoutExpired:
        logger.warning(f"ffprobe timeout for {audio_path}")
    except ValueError as e:
        logger.warning(f"Failed to parse duration for {audio_path}: {e}")
    except Exception as e:
        logger.warning(f"Duration query failed for {audio_path}: {e}")
    return None


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
            'ffmpeg', '-y', '-i', audio_path,
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
    _cache: ClassVar[dict[str, tuple[float, float]]] = {}  # path -> (duration, mtime)

    @classmethod
    def get_duration(cls, path: str) -> float | None:
        """Get audio duration with caching based on file modification time.

        Args:
            path: Path to audio file

        Returns:
            Duration in seconds, or None if unable to determine
        """
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            # File doesn't exist or can't access - fall through to direct query
            return get_audio_duration(path)

        # Check cache
        if path in cls._cache:
            cached_duration, cached_mtime = cls._cache[path]
            if cached_mtime == mtime:
                return cached_duration

        # Query and cache
        duration = get_audio_duration(path)
        if duration is not None:
            cls._cache[path] = (duration, mtime)
            # Evict oldest entries if cache exceeds max size
            while len(cls._cache) > cls._MAX_CACHE_SIZE:
                cls._cache.pop(next(iter(cls._cache)))

        return duration

    @classmethod
    def invalidate(cls, path: str) -> None:
        """Remove a specific path from the cache."""
        cls._cache.pop(path, None)
