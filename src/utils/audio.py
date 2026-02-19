"""Audio utility functions.

Provides shared audio file operations used across multiple modules.
"""

import logging
import os
import subprocess
from typing import Dict, Optional, Tuple

from config import FFPROBE_TIMEOUT

logger = logging.getLogger(__name__)


def get_audio_duration(audio_path: str) -> Optional[float]:
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
        result = subprocess.run(
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


class AudioMetadata:
    """Cached audio file metadata to avoid redundant ffprobe calls.

    Usage:
        duration = AudioMetadata.get_duration('/path/to/audio.mp3')
    """

    _cache: Dict[str, Tuple[float, float]] = {}  # path -> (duration, mtime)

    @classmethod
    def get_duration(cls, path: str) -> Optional[float]:
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

        return duration

    @classmethod
    def clear_cache(cls) -> None:
        """Clear the duration cache."""
        cls._cache.clear()

    @classmethod
    def invalidate(cls, path: str) -> None:
        """Remove a specific path from the cache."""
        cls._cache.pop(path, None)
