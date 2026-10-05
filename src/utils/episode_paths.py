"""Shared builders for versioned episode filenames and URLs."""


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
