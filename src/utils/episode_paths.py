"""Shared builders for versioned episode filenames and URLs."""
from typing import Optional


def episode_version_suffix(version: Optional[int]) -> str:
    """Return '-v{N}' for N>=1, empty string otherwise.

    Kept separate from ``Storage.get_episode_path`` so callers that only need
    the string suffix (DB `processed_file` column, RSS enclosure URL, API
    response `processedUrl`) avoid depending on a Storage instance.
    """
    return f"-v{int(version)}" if version and int(version) > 0 else ""


def episode_filename(episode_id: str, version: Optional[int] = None,
                      extension: str = ".mp3") -> str:
    """Return the bare filename: ``{episode_id}[-v{N}]{extension}``."""
    return f"{episode_id}{episode_version_suffix(version)}{extension}"


def episode_relative_path(episode_id: str, version: Optional[int] = None,
                           extension: str = ".mp3") -> str:
    """Return ``episodes/{filename}`` for DB ``processed_file`` storage."""
    return f"episodes/{episode_filename(episode_id, version, extension)}"


def episode_public_url(base_url: str, slug: str, episode_id: str,
                        version: Optional[int] = None,
                        extension: str = ".mp3") -> str:
    """Return the public-facing enclosure URL."""
    return f"{base_url}/episodes/{slug}/{episode_filename(episode_id, version, extension)}"
