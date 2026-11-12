"""Shared builders for versioned episode filenames and URLs."""
import os
import re
import time
from pathlib import Path


def episode_ui_url(slug: str, episode_id: str) -> str:
    """Absolute web UI URL of an episode page (UI_BASE_URL, else BASE_URL)."""
    base = (os.environ.get('UI_BASE_URL') or os.environ.get('BASE_URL', 'http://localhost:8000')).rstrip('/')
    return f"{base}/ui/feeds/{slug}/episodes/{episode_id}"


def episode_version_suffix(version: int | None) -> str:
    """Return '-v{N}' for N>=1, empty string otherwise.

    Kept separate from ``Storage.get_episode_path`` so callers that only need
    the string suffix (DB `processed_file` column, RSS enclosure URL, API
    response `processedUrl`) avoid depending on a Storage instance.
    """
    return f"-v{int(version)}" if version and int(version) > 0 else ""


def episode_filename(episode_id: str, version: int | None = None,
                      extension: str = ".mp3") -> str:
    """Return the bare filename: ``{episode_id}[-v{N}]{extension}``."""
    return f"{episode_id}{episode_version_suffix(version)}{extension}"


def episode_relative_path(episode_id: str, version: int | None = None,
                           extension: str = ".mp3") -> str:
    """Return ``episodes/{filename}`` for DB ``processed_file`` storage."""
    return f"episodes/{episode_filename(episode_id, version, extension)}"


def published_episode_version(episode: dict | None) -> int | None:
    """Return the version only when its stored path proves publication."""
    if not episode:
        return None
    version = episode.get('processed_version')
    if version is None:
        version = 0
    if type(version) is not int or version < 0:
        return None
    episode_id = episode.get('episode_id')
    if not isinstance(episode_id, str) or not episode_id:
        return None
    if episode.get('processed_file') != episode_relative_path(episode_id, version):
        return None
    return version


def episode_public_url(base_url: str, slug: str, episode_id: str,
                        version: int | None = None,
                        extension: str = ".mp3",
                        key: str | None = None) -> str:
    """Return the public-facing enclosure URL.

    ``key`` is the global feed auth key (authenticated feeds); when set it is
    appended as the ``?key=`` query param the public routes enforce.
    """
    url = f"{base_url}/episodes/{slug}/{episode_filename(episode_id, version, extension)}"
    return f"{url}?key={key}" if key else url


def peaks_file_path(audio_path: Path | str) -> Path:
    """Return the sibling ``.peaks`` path for an audio file."""
    return Path(audio_path).with_suffix('.peaks')


# A write interrupted this long ago is not still running
PEAKS_TEMP_STALE_SECONDS = 3600
# mkstemp names: <peaks name>.<8 random chars>.tmp
_PEAKS_TEMP_RE = re.compile(r'-original\.peaks\.[A-Za-z0-9_]{8}\.tmp$')


def is_peaks_temp_name(name: str) -> bool:
    """True for a leftover temp file of an interrupted peaks write."""
    return _PEAKS_TEMP_RE.search(name) is not None


def is_peaks_housekeeping_name(name: str) -> bool:
    """True for a peaks build lock or an interrupted-write temp file."""
    return is_peaks_temp_name(name) or name.endswith('-original.peaks.lock')


def peaks_lock_path(peaks_path: Path | str) -> Path:
    """Return the cross-process build lock path for a peaks file."""
    p = Path(peaks_path)
    return p.with_name(p.name + '.lock')


def remove_peaks_artifacts(peaks_path: Path | str) -> bool:
    """Remove a peaks file and its build lock; True when the file itself was removed."""
    peaks = Path(peaks_path)
    peaks_lock_path(peaks).unlink(missing_ok=True)
    try:
        peaks.unlink()
    except FileNotFoundError:
        return False
    return True


def sweep_stale_peaks_temp_files(directory: Path | str, min_age_seconds: float = PEAKS_TEMP_STALE_SECONDS) -> int:
    """Remove peaks temp files left by interrupted writes in one directory; returns the count."""
    removed = 0
    try:
        entries = list(os.scandir(directory))
    except OSError:
        return 0
    now = time.time()
    for entry in entries:
        if not is_peaks_temp_name(entry.name):
            continue
        try:
            if now - entry.stat().st_mtime < min_age_seconds:
                continue
            os.unlink(entry.path)
            removed += 1
        except OSError:
            continue
    return removed
