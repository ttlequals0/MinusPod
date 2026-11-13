"""Storage management with SQLite database and file operations."""
import hashlib
import json
import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any
import tempfile
import shutil

from config import (
    HTTP_MAX_REDIRECTS_FEED, HTTP_TIMEOUT_FETCH,
    count_pending_review,
    get_env_backed_int, MAX_ARTWORK_BYTES_MIN, MAX_ARTWORK_BYTES_MAX,
)
from artwork_watermark import (
    composite_watermark, cover_badge_salt, badge_path, normalize_badge_position,
)
from user_agent import download_user_agent
from utils.episode_paths import (
    episode_filename, is_peaks_housekeeping_name, peaks_file_path, remove_peaks_artifacts,
    sweep_stale_peaks_temp_files,
)
from utils.http import safe_url_for_log
from utils.url import SSRFError
from utils.validation import is_dangerous_slug, is_valid_episode_id
from utils.safe_http import (
    ResponseTooLargeError,
    URLTrust,
    read_response_capped,
    safe_get,
)
from utils.ttl_cache import TTLCache
from utils.paths import resolve_data_dir
from utils.time import utc_now, utc_now_iso, parse_iso_utc


_ALLOWED_IMAGE_TYPES = frozenset({
    'image/jpeg',
    'image/jpg',
    'image/png',
    'image/gif',
    'image/webp',
})

# TTL for get_storage_stats(); disk usage does not change second-to-second
# and the walk is thousands of stat() calls.
STORAGE_STATS_TTL_SECONDS = 45
# A bad artwork URL fails identically on every refresh; back off so retries stop
# but a publisher fix is picked up the same day. A forced refresh ignores it.
ARTWORK_FAILURE_TTL_SECONDS = 6 * 3600
# A 404 means the resource is confirmed gone rather than transiently broken,
# so it is backed off for longer than a generic fetch error.
ARTWORK_FAILURE_TTL_NOT_FOUND_SECONDS = 24 * 3600
# Per-status backoff window, keyed by the classification _download_artwork_uncached
# returns on failure.
_ARTWORK_FAILURE_WINDOWS = {
    'not_found': ARTWORK_FAILURE_TTL_NOT_FOUND_SECONDS,
    'error': ARTWORK_FAILURE_TTL_SECONDS,
}
# Ceiling on the durable per-URL failure map, kept newest-first: a feed that
# churns through candidate URLs must not grow its podcast row without bound.
ARTWORK_FAILURE_STATE_MAX_ENTRIES = 50

# Extension per stored image type, and the reverse lookup used to find a
# cached cover when only the base name is known.
_EXTENSION_BY_TYPE = {
    'image/png': '.png',
    'image/gif': '.gif',
    'image/webp': '.webp',
    'image/jpeg': '.jpg',
    'image/jpg': '.jpg',
}
_ARTWORK_EXTENSIONS = (
    ('.jpg', 'image/jpeg'),
    ('.png', 'image/png'),
    ('.gif', 'image/gif'),
    ('.webp', 'image/webp'),
)

# Per-feed episode covers live here, one file per episode id (issue #617).
_EPISODE_ARTWORK_DIR = "episode-artwork"
_EPISODE_ARTWORK_TOUCH_SECONDS = 86400
_CHAPTER_IMAGE_NAME_RE = re.compile(r'[0-9a-f]{64}\.(jpg|png)')
# Per-feed ceiling on that directory. Publisher episode covers run large (a
# 3000x3000 JPEG is ~600 KB), and a long back catalogue would otherwise grow
# without limit, so the least recently served files are dropped past this.
EPISODE_ARTWORK_CACHE_BYTES = 64 * 1024 * 1024

# Cached cover-art badge variant (issue #420), one per podcast dir.
_WATERMARK_VARIANT = "artwork-minuspod.jpg"
# Sidecar recording the cover_badge_salt the variant was rendered with; mtimes
# alone miss a BADGE_REVISION bump (same asset file, new rendering).
_WATERMARK_SALT = "artwork-minuspod.salt"


def is_chapter_image_filename(value: str) -> bool:
    return bool(_CHAPTER_IMAGE_NAME_RE.fullmatch(value))


def _detect_image_mime(data: bytes) -> str | None:
    """Canonical Content-Type from file magic, or None if unsupported. SVG is rejected (script execution)."""
    if len(data) < 12:
        return None
    if data[:3] == b'\xff\xd8\xff':
        return 'image/jpeg'
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return 'image/png'
    if data[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'image/webp'
    return None


def _max_artwork_bytes() -> int:
    """Artwork size cap in bytes (default 25 MB), clamped so a typo cannot cause a memory DoS.

    Env-backed: the UI value wins, the env var seeds the default (config.ENV_BACKED_SETTINGS).
    """
    return get_env_backed_int('max_artwork_bytes',
                              floor=MAX_ARTWORK_BYTES_MIN,
                              ceiling=MAX_ARTWORK_BYTES_MAX)

logger = logging.getLogger(__name__)


class PathContainmentError(ValueError):
    """Raised when a slug or episode_id would resolve outside the storage root."""


def _safe_join_under(base: Path, *parts: str) -> Path:
    """Join ``parts`` under ``base``; symlink and ``..`` escapes raise (resolve + relative_to).

    ``base`` must already exist; the joined path need not.
    """
    base_resolved = base.resolve()
    joined = base_resolved.joinpath(*parts).resolve()
    try:
        joined.relative_to(base_resolved)
    except ValueError as exc:
        raise PathContainmentError(
            f"path {joined!r} escapes storage root {base_resolved!r}"
        ) from exc
    return joined


class Storage:
    """Storage manager using SQLite for metadata and filesystem for large files.

    Singleton, mirroring Database, so repeated get_storage() calls do not re-fire the init log.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls, data_dir: str | None = None):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, data_dir: str | None = None):
        if self._initialized:
            return
        # Tests and non-container deploys need a configurable root;
        # /app/data is the in-container default.
        if data_dir is None:
            data_dir = resolve_data_dir()
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Create podcasts subdirectory
        self.podcasts_dir = self.data_dir / "podcasts"
        self.podcasts_dir.mkdir(exist_ok=True)

        # Initialize database
        from database import Database
        self.db = Database(str(self.data_dir))

        # get_storage_stats() cache. TTLCache is lock-free and this singleton is shared
        # across request threads; the lock serializes the check-walk-store sequence.
        self._storage_stats_cache = TTLCache(STORAGE_STATS_TTL_SECONDS)
        self._storage_stats_lock = threading.Lock()

        # Artwork URLs that failed validation, so a broken one is not refetched
        # on every feed refresh. Keyed by slug and URL, so a changed URL retries
        # at once. Two caches so a 404 backs off longer than a transient error;
        # both are an in-process fast path only: the podcasts.artwork_failure_state
        # column is the durable record consulted when a cache is cold (restart).
        self._artwork_failure_cache = TTLCache(ARTWORK_FAILURE_TTL_SECONDS)
        self._artwork_404_cache = TTLCache(ARTWORK_FAILURE_TTL_NOT_FOUND_SECONDS)
        # Keys whose skip was already logged this backoff window; bounded by TTLCache max_size.
        self._artwork_skip_logged = TTLCache(max(_ARTWORK_FAILURE_WINDOWS.values()))

        self._initialized = True
        logger.info(f"Storage initialized with data_dir: {self.data_dir}")

    def get_podcast_dir(self, slug: str) -> Path:
        """Get podcast directory, creating if necessary; rejects slugs that escape ``podcasts_dir``."""
        podcast_dir = self._podcast_path(slug)
        podcast_dir.mkdir(exist_ok=True)

        # Ensure episodes directory exists
        episodes_dir = podcast_dir / "episodes"
        episodes_dir.mkdir(exist_ok=True)

        return podcast_dir

    def _podcast_path(self, slug: str) -> Path:
        """Validated, contained path for a slug; does not touch the disk."""
        if is_dangerous_slug(slug):
            raise PathContainmentError(f"refusing dangerous slug {slug!r}")
        return _safe_join_under(self.podcasts_dir, slug)

    def import_staging_dir(self, slug: str, create: bool = False) -> Path:
        """Per-feed staging directory for an archive-import commit (``<data>/import-staging/<slug>/``).

        Browser-uploaded files land here before the commit engine moves them into episode
        storage. A commit with source 'staging' or 'both' sweeps it afterwards: committed
        entries are already moved, rejected files (bad naming, empty, stray .part) are
        removed, but skipped or errored entries keep their audio and sidecars so fixing a
        sidecar does not mean re-uploading. The directory goes only when nothing preserved
        remains; source='directory' never touches it. `DELETE /feeds/{slug}/import/staging`
        and the next "Choose files" selection also clear it.
        """
        if is_dangerous_slug(slug):
            raise PathContainmentError(f"refusing dangerous slug {slug!r}")
        path = _safe_join_under(self.data_dir, 'import-staging', slug)
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def import_source_dir(self, slug: str) -> Path:
        """The operator-managed archive-import directory for a feed (``<data>/import/<slug>/``).

        Never created here; absence means nothing was dropped in yet. The commit engine
        moves each committed entry's audio out and deletes its sidecars (json/txt/artwork);
        rejected or errored entries are left untouched for the operator to fix and re-scan.
        """
        if is_dangerous_slug(slug):
            raise PathContainmentError(f"refusing dangerous slug {slug!r}")
        return _safe_join_under(self.data_dir, 'import', slug)

    def _validated_episode_leaf(self, slug: str, episode_id: str, filename: str) -> Path:
        """Return a resolved path inside the episodes directory for ``slug``.

        Validates ``episode_id`` shape so a malicious filename cannot escape
        the per-podcast episodes directory via ``..`` or absolute paths.
        """
        if not is_valid_episode_id(episode_id):
            raise PathContainmentError(f"refusing invalid episode id {episode_id!r}")
        podcast_dir = self.get_podcast_dir(slug)
        return _safe_join_under(podcast_dir, "episodes", filename)

    def get_episode_path(self, slug: str, episode_id: str,
                          extension: str = ".mp3",
                          version: int | None = None) -> Path:
        """Get path for an episode audio file.

        version=None or 0 -> ``{episode_id}{extension}`` (unversioned, back-compat).
        version>=1        -> ``{episode_id}-v{N}{extension}`` (incremented per reprocess).
        """
        return self._validated_episode_leaf(
            slug, episode_id, episode_filename(episode_id, version, extension)
        )

    def iter_episode_audio_paths(self, slug: str, episode_id: str,
                                   extension: str = ".mp3") -> list[Path]:
        """Return all audio files for this episode (unversioned + any v1..vN)."""
        if not is_valid_episode_id(episode_id):
            raise PathContainmentError(f"refusing invalid episode id {episode_id!r}")
        episodes_dir = self.get_podcast_dir(slug) / "episodes"
        if not episodes_dir.exists():
            return []
        unversioned = self.get_episode_path(slug, episode_id, extension)
        versioned = sorted(episodes_dir.glob(f"{episode_id}-v*{extension}"))
        paths = []
        if unversioned.exists():
            paths.append(unversioned)
        paths.extend(versioned)
        return paths

    def cleanup_stale_audio_versions(self, slug: str, episode_id: str,
                                       current_version: int,
                                       extension: str = ".mp3") -> int:
        """Remove every audio file except the current version; returns the number deleted.

        Legacy unversioned URLs still resolve via ``serve_episode`` (it reads
        ``processed_version``), so the rest can go on finalize. The retained
        ``{episode_id}-original`` file is not listed by ``iter_episode_audio_paths``.
        """
        if current_version <= 0:
            return 0
        current = self.get_episode_path(slug, episode_id, extension,
                                          version=current_version)
        keep = {current.resolve()}
        removed = 0
        for path in self.iter_episode_audio_paths(slug, episode_id, extension):
            if path.resolve() in keep:
                continue
            try:
                path.unlink()
                removed += 1
                logger.info(
                    f"[{slug}:{episode_id}] Removed stale audio version: {path.name}"
                )
            except Exception as e:
                logger.warning(f"[{slug}:{episode_id}] Failed to delete {path}: {e}")
        return removed

    def get_original_path(self, slug: str, episode_id: str, extension: str = ".mp3") -> Path:
        """Get path for the retained original (pre-cut) audio file."""
        return self._validated_episode_leaf(
            slug, episode_id, f"{episode_id}-original{extension}"
        )

    def get_peaks_path(self, slug: str, episode_id: str) -> Path:
        """Get path for the peaks file that sits beside the retained original."""
        return peaks_file_path(self.get_original_path(slug, episode_id))

    def delete_peaks_file(self, slug: str, episode_id: str) -> bool:
        """Remove the episode's peaks file and its lock; never raises.

        True when the peaks file itself was removed.
        """
        try:
            return remove_peaks_artifacts(self.get_peaks_path(slug, episode_id))
        except PathContainmentError as exc:
            logger.debug(f"[{slug}:{episode_id}] No peaks path: {exc}")
            return False
        except OSError as exc:
            logger.warning(f"[{slug}:{episode_id}] Failed to delete peaks file: {exc}")
            return False

    def sweep_peaks_temp_files(self) -> int:
        """Remove stale peaks temp files in every feed's episodes directory."""
        removed = 0
        for podcast_dir in self.podcasts_dir.iterdir():
            if podcast_dir.is_dir():
                removed += sweep_stale_peaks_temp_files(podcast_dir / "episodes")
        return removed

    def save_rss(self, slug: str, content: str) -> None:
        """Save modified RSS feed to filesystem."""
        podcast_dir = self.get_podcast_dir(slug)
        rss_file = podcast_dir / "modified-rss.xml"

        # Atomic write
        with tempfile.NamedTemporaryFile(mode='w', delete=False,
                                         dir=podcast_dir, suffix='.tmp') as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        shutil.move(tmp_path, rss_file)
        logger.debug(f"[{slug}] Saved modified RSS feed")

    def get_rss(self, slug: str) -> str | None:
        """Get cached RSS feed from filesystem."""
        podcast_dir = self.get_podcast_dir(slug)
        rss_file = podcast_dir / "modified-rss.xml"

        if rss_file.exists():
            with open(rss_file, 'r') as f:
                return f.read()
        return None

    def save_transcript(self, slug: str, episode_id: str, transcript: str) -> None:
        """Save episode transcript to database."""
        try:
            self.db.save_episode_details(slug, episode_id, transcript_text=transcript)
        except ValueError:
            logger.warning(f"[{slug}:{episode_id}] Episode not in DB, transcript not saved")

        logger.debug(f"[{slug}:{episode_id}] Saved transcript")

    def get_transcript(self, slug: str, episode_id: str) -> str | None:
        """Get episode transcript from database."""
        episode = self.db.get_episode(slug, episode_id)
        if episode and episode.get('transcript_text'):
            return episode['transcript_text']
        return None

    def save_original_transcript(self, slug: str, episode_id: str, transcript: str) -> None:
        """Save original (pre-cut) transcript to database. Write-once."""
        self.db.save_original_transcript(slug, episode_id, transcript)

    def save_original_segments(self, slug: str, episode_id: str, segments: list) -> None:
        """Save original (pre-cut) Whisper segments JSON to database. Write-once."""
        self.db.save_original_segments(slug, episode_id, segments)

    def save_final_segments(self, slug: str, episode_id: str, segments: list) -> None:
        """Save final (post-cut) segments JSON to database. Overwrites on reprocess."""
        self.db.save_final_segments(slug, episode_id, segments)

    def save_chapters_and_applied_cuts(self, slug: str, episode_id: str,
                                       chapters: dict, cuts: list) -> None:
        """Atomically persist chapters JSON and the cut list it was generated against.

        One DB statement, so fresh chapters are never paired with stale cuts (which would poison the next recut remap)."""
        self.db.save_chapters_and_applied_cuts(
            slug, episode_id, json.dumps(chapters), cuts
        )

    def get_applied_cuts(self, slug: str, episode_id: str) -> list | None:
        """Get the persisted applied cut list, or None when never persisted."""
        return self.db.get_applied_cuts(slug, episode_id)

    # ========== VTT Transcript Methods (Podcasting 2.0) ==========

    def save_transcript_vtt(self, slug: str, episode_id: str, vtt_content: str) -> None:
        """Save VTT transcript to database."""
        try:
            self.db.save_episode_details(slug, episode_id, transcript_vtt=vtt_content)
            logger.debug(f"[{slug}:{episode_id}] Saved VTT transcript to database")
        except ValueError:
            logger.warning(f"[{slug}:{episode_id}] Episode not in DB, VTT not saved")

    def get_transcript_vtt(self, slug: str, episode_id: str) -> str | None:
        """Get VTT transcript from database."""
        episode = self.db.get_episode(slug, episode_id)
        if episode and episode.get('transcript_vtt'):
            return episode['transcript_vtt']
        return None

    def has_transcript_vtt(self, slug: str, episode_id: str) -> bool:
        """Check if VTT transcript exists in database."""
        episode = self.db.get_episode(slug, episode_id)
        return bool(episode and episode.get('transcript_vtt'))

    # ========== Chapters Methods (Podcasting 2.0) ==========

    def save_chapters_json(self, slug: str, episode_id: str, chapters: dict) -> None:
        """Save chapters JSON to database."""
        try:
            chapters_str = json.dumps(chapters)
            self.db.save_episode_details(slug, episode_id, chapters_json=chapters_str)
            logger.debug(f"[{slug}:{episode_id}] Saved chapters JSON to database")
        except ValueError:
            logger.warning(f"[{slug}:{episode_id}] Episode not in DB, chapters not saved")

    def get_chapters_json(self, slug: str, episode_id: str) -> dict | None:
        """Get chapters JSON from database."""
        episode = self.db.get_episode(slug, episode_id)
        if episode and episode.get('chapters_json'):
            try:
                return json.loads(episode['chapters_json'])
            except json.JSONDecodeError:
                return None
        return None

    def has_chapters_json(self, slug: str, episode_id: str) -> bool:
        """Check if chapters JSON exists in database."""
        episode = self.db.get_episode(slug, episode_id)
        return bool(episode and episode.get('chapters_json'))

    def save_ads_json(self, slug: str, episode_id: str, ads_data: Any,
                      pass_number: int = 1) -> None:
        """Save the ad detection response to the database with a pass marker.

        ``ads_data`` is a dict with 'ads', 'raw_response' and 'prompt' keys.
        """
        try:
            ad_markers = ads_data.get('ads', []) if isinstance(ads_data, dict) else []
            raw_response = ads_data.get('raw_response') if isinstance(ads_data, dict) else None
            prompt = ads_data.get('prompt') if isinstance(ads_data, dict) else None

            # Mark each ad with its detection stage if not already set
            for ad in ad_markers:
                if 'detection_stage' not in ad:
                    if pass_number == 1:
                        ad['detection_stage'] = 'first_pass'
                    else:
                        ad['detection_stage'] = 'verification'

            if pass_number == 1:
                self.db.save_episode_details(
                    slug, episode_id,
                    ad_markers=ad_markers,
                    first_pass_response=raw_response,
                    first_pass_prompt=prompt
                )
            else:
                # For verification pass, save the prompt/response separately
                self.db.save_episode_details(
                    slug, episode_id,
                    second_pass_prompt=prompt,
                    second_pass_response=raw_response
                )
        except ValueError:
            logger.warning(f"[{slug}:{episode_id}] Episode not in DB, ads not saved")

        logger.debug(f"[{slug}:{episode_id}] Saved pass {pass_number} ads detection data")

    def save_combined_ads(self, slug: str, episode_id: str, all_ads: list[dict]) -> None:
        """Save combined ad markers from both passes to database."""
        pending_count = count_pending_review(all_ads)
        try:
            self.db.save_episode_details(slug, episode_id, ad_markers=all_ads,
                                         pending_review_count=pending_count)
        except ValueError:
            logger.warning(f"[{slug}:{episode_id}] Episode not in DB, combined ads not saved")
            return

        logger.debug(f"[{slug}:{episode_id}] Saved {len(all_ads)} combined ad markers")
        try:
            self.db.record_fingerprint_matches_from_markers(slug, episode_id, all_ads)
        except Exception as e:
            logger.warning(f"[{slug}:{episode_id}] Could not record fingerprint match history: {e}")


    # ========== Artwork Methods ==========

    def save_artwork(self, slug: str, image_data: bytes, content_type: str,
                    source_url: str = None) -> bool:
        """Save podcast artwork to filesystem."""
        try:
            podcast_dir = self.get_podcast_dir(slug)

            ext = _EXTENSION_BY_TYPE.get(content_type.lower(), '.jpg')

            artwork_path = podcast_dir / f"artwork{ext}"

            # Atomically move a temp file into place, then delete other-extension stale files,
            # so a failed write never leaves the podcast without artwork.
            with tempfile.NamedTemporaryFile(mode='wb', delete=False,
                                             dir=podcast_dir, suffix='.tmp') as tmp:
                tmp.write(image_data)
                tmp_path = tmp.name
            os.replace(tmp_path, artwork_path)

            for old_ext in ('.jpg', '.png', '.gif', '.webp'):
                old_path = podcast_dir / f"artwork{old_ext}"
                if old_path.exists() and old_path != artwork_path:
                    old_path.unlink()

            # Drop the cached watermark variant so it regenerates from the new
            # source the next time it's requested.
            self.clear_watermark_cache(slug)

            # Update database
            self.db.update_podcast(
                slug,
                artwork_url=source_url,
                artwork_cached=1
            )

            logger.debug(f"[{slug}] Saved artwork ({len(image_data)} bytes)")
            return True

        except Exception as e:
            logger.error(f"[{slug}] Failed to save artwork: {e}")
            return False

    def podcast_dir_if_exists(self, slug: str) -> Path | None:
        """get_podcast_dir without the mkdir: read paths must not create
        directories for probed unknown slugs."""
        podcast_dir = self._podcast_path(slug)
        return podcast_dir if podcast_dir.is_dir() else None

    def artwork_file(self, slug: str) -> tuple[Path, str] | None:
        """Path and content type of the cached artwork, or None."""
        podcast_dir = self.podcast_dir_if_exists(slug)
        if not podcast_dir:
            return None
        for ext, content_type in _ARTWORK_EXTENSIONS:
            artwork_path = podcast_dir / f"artwork{ext}"
            if artwork_path.exists():
                return artwork_path.resolve(), content_type
        return None

    def artwork_mtime_ns(self, slug: str) -> int | None:
        """mtime_ns of the cached artwork (one stat, no read), or None."""
        found = self.artwork_file(slug)
        if not found:
            return None
        try:
            return found[0].stat().st_mtime_ns
        except OSError:
            return None

    def get_artwork(self, slug: str) -> tuple[bytes, str] | None:
        """Get cached artwork. Returns (data, content_type) or None."""
        found = self.artwork_file(slug)
        if not found:
            return None
        try:
            return found[0].read_bytes(), found[1]
        except OSError:
            return None

    def _chapter_images_dir(self, slug: str, episode_id: str,
                            create: bool = False) -> Path | None:
        """Resolve one episode's contained chapter-image directory."""
        if not is_valid_episode_id(episode_id):
            return None
        podcast_dir = (self.get_podcast_dir(slug) if create
                       else self.podcast_dir_if_exists(slug))
        if not podcast_dir:
            return None
        path = _safe_join_under(podcast_dir, 'chapter-images', episode_id)
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def save_chapter_image(self, slug: str, episode_id: str,
                           image_data: bytes) -> str | None:
        """Store a bounded JPEG/PNG chapter image without replacing older assets."""
        if len(image_data) > _max_artwork_bytes():
            return None
        content_type = _detect_image_mime(image_data)
        if content_type not in ('image/jpeg', 'image/png'):
            return None
        filename = hashlib.sha256(image_data).hexdigest() + _EXTENSION_BY_TYPE[content_type]
        tmp_path = None
        try:
            image_dir = self._chapter_images_dir(slug, episode_id, create=True)
            if image_dir is None:
                return None
            path = _safe_join_under(image_dir, filename)
            if not path.exists():
                with tempfile.NamedTemporaryFile(mode='wb', delete=False,
                                                 dir=image_dir, suffix='.tmp') as tmp:
                    tmp_path = Path(tmp.name)
                    tmp.write(image_data)
                os.replace(tmp_path, path)
        except (OSError, PathContainmentError):
            logger.warning(f'[{slug}:{episode_id}] Could not store chapter image')
            return None
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)
        return f'/episodes/{slug}/{episode_id}/chapter-images/{filename}'

    def get_chapter_image(self, slug: str, episode_id: str,
                          filename: str) -> tuple[bytes, str] | None:
        """Read only contained, correctly named JPEG/PNG chapter images."""
        if not is_chapter_image_filename(filename):
            return None
        try:
            image_dir = self._chapter_images_dir(slug, episode_id)
            if image_dir is None:
                return None
            path = _safe_join_under(image_dir, filename)
            if not path.is_file():
                return None
            limit = _max_artwork_bytes()
            with path.open('rb') as image_file:
                data = image_file.read(limit + 1)
            if len(data) > limit:
                return None
        except (OSError, PathContainmentError):
            return None
        content_type = _detect_image_mime(data)
        if (content_type not in ('image/jpeg', 'image/png')
                or not filename.endswith(_EXTENSION_BY_TYPE[content_type])
                or hashlib.sha256(data).hexdigest() != filename.split('.')[0]):
            return None
        return data, content_type

    def remove_chapter_images(self, slug: str, episode_id: str) -> int:
        """Remove an episode's chapter images and return freed bytes."""
        try:
            image_dir = self._chapter_images_dir(slug, episode_id)
            if image_dir is None or not image_dir.is_dir():
                return 0
            freed = sum(path.stat().st_size for path in image_dir.iterdir()
                        if path.is_file() and not path.is_symlink())
            shutil.rmtree(image_dir)
            return freed
        except (OSError, PathContainmentError):
            logger.warning(f'[{slug}:{episode_id}] Could not remove chapter images')
            return 0

    # ---------- Episode covers (issue #617) ----------

    def _episode_artwork_dir(self, slug: str, create: bool = False) -> Path | None:
        """The feed's episode-cover directory, or None when the feed has none."""
        podcast_dir = (self.get_podcast_dir(slug) if create
                       else self.podcast_dir_if_exists(slug))
        if not podcast_dir:
            return None
        art_dir = _safe_join_under(podcast_dir, _EPISODE_ARTWORK_DIR)
        if create:
            art_dir.mkdir(exist_ok=True)
        return art_dir

    def has_episode_artwork(self, slug: str, episode_id: str) -> bool:
        """True if a cached episode cover exists, without a read (a read would bump its LRU mtime)."""
        if not is_valid_episode_id(episode_id):
            return False
        art_dir = self._episode_artwork_dir(slug)
        if not art_dir or not art_dir.is_dir():
            return False
        return any(_safe_join_under(art_dir, f"{episode_id}{ext}").exists()
                   for ext, _ in _ARTWORK_EXTENSIONS)

    def remove_episode_artwork(self, slug: str, episode_id: str) -> bool:
        """Delete a cached episode cover, if any (used on hard delete); True if a file was removed."""
        if not is_valid_episode_id(episode_id):
            return False
        art_dir = self._episode_artwork_dir(slug)
        if not art_dir or not art_dir.is_dir():
            return False
        removed = False
        for ext, _ in _ARTWORK_EXTENSIONS:
            path = _safe_join_under(art_dir, f"{episode_id}{ext}")
            if path.exists():
                try:
                    path.unlink()
                    removed = True
                except OSError as exc:
                    logger.warning(
                        f"[{slug}:{episode_id}] Failed to delete episode artwork: {exc}")
        return removed

    def episode_artwork_file(self, slug: str,
                             episode_id: str) -> tuple[Path, str] | None:
        """Path and content type of a cached episode cover, or None.

        Refreshes the mtime for LRU eviction at most daily so the ETag stays stable.
        """
        if not is_valid_episode_id(episode_id):
            return None
        art_dir = self._episode_artwork_dir(slug)
        if not art_dir or not art_dir.is_dir():
            return None
        for ext, content_type in _ARTWORK_EXTENSIONS:
            path = _safe_join_under(art_dir, f"{episode_id}{ext}")
            if path.exists():
                try:
                    if time.time() - path.stat().st_mtime > _EPISODE_ARTWORK_TOUCH_SECONDS:
                        os.utime(path, None)
                except OSError:
                    pass
                return path.resolve(), content_type
        return None

    def download_episode_artwork(self, slug: str, episode_id: str,
                                 artwork_url: str) -> bool:
        """Fetch and cache one episode's cover.

        Same validation as ``download_artwork`` (magic allowlist, size cap, failure cache).
        Does not touch the podcast-level badge variant.
        """
        if not artwork_url or not is_valid_episode_id(episode_id):
            return False

        ua = download_user_agent(self.db.get_podcast_by_slug(slug))
        failure_key = f"{slug}\n{episode_id}\n{artwork_url}\n{ua}"
        if self._artwork_failure_cache.get(failure_key):
            logger.debug(
                f"[{slug}:{episode_id}] Skipping episode artwork retry, "
                f"this URL failed recently")
            return False

        ok = self._download_episode_artwork_uncached(slug, episode_id, artwork_url, user_agent=ua)
        # Store only failures: cached successes would evict failure entries before their TTL.
        # No delete-on-success: with no force bypass, a live failure entry returns early above.
        if not ok:
            self._artwork_failure_cache.set(failure_key, True)
        return ok

    def _download_episode_artwork_uncached(self, slug: str, episode_id: str,
                                          artwork_url: str, user_agent: str | None = None) -> bool:
        """Fetch, validate, and save one episode cover. See download_episode_artwork."""
        try:
            logger.info(f"[{slug}:{episode_id}] Downloading episode artwork from "
                        f"{safe_url_for_log(artwork_url)}")

            headers = {
                'User-Agent': user_agent or download_user_agent(self.db.get_podcast_by_slug(slug)),
                'Accept': '*/*',
                'Accept-Language': 'en-US,en;q=0.9',
            }
            try:
                response = safe_get(
                    artwork_url,
                    trust=URLTrust.FEED_CONTENT,
                    max_redirects=HTTP_MAX_REDIRECTS_FEED,
                    timeout=HTTP_TIMEOUT_FETCH,
                    stream=True,
                    headers=headers,
                )
            except SSRFError as e:
                logger.warning(
                    f"[{slug}:{episode_id}] SSRF blocked in "
                    f"download_episode_artwork: {e}")
                return False
            response.raise_for_status()

            declared_type = (response.headers.get('Content-Type') or '').split(';', 1)[0].strip().lower()
            if declared_type and declared_type not in _ALLOWED_IMAGE_TYPES:
                logger.warning(
                    "[%s:%s] episode_artwork_rejected_content_type declared=%s url=%s",
                    slug, episode_id, declared_type, safe_url_for_log(artwork_url),
                )
                return False

            max_bytes = _max_artwork_bytes()
            try:
                image_data = read_response_capped(response, max_bytes, chunk_size=65536)
            except ResponseTooLargeError:
                logger.warning(
                    "[%s:%s] episode_artwork_size_cap_exceeded max=%d url=%s",
                    slug, episode_id, max_bytes, safe_url_for_log(artwork_url),
                )
                return False

            detected = _detect_image_mime(image_data)
            if not detected:
                logger.warning(
                    "[%s:%s] episode_artwork_rejected_magic declared=%s url=%s",
                    slug, episode_id, declared_type, safe_url_for_log(artwork_url),
                )
                return False

            return self._save_episode_artwork(slug, episode_id, image_data, detected)

        except Exception as e:
            logger.warning(
                f"[{slug}:{episode_id}] Failed to download episode artwork: {e}")
            return False

    def save_episode_artwork(self, slug: str, episode_id: str,
                             image_data: bytes, content_type: str,
                             evict: bool = True) -> bool:
        """Public wrapper around ``_save_episode_artwork`` for API-driven uploads.

        ``content_type`` must already be validated (e.g. via ``_detect_image_mime``).

        ``evict=False`` skips the LRU trim: a local-feed cover is the only copy (no upstream
        URL), while a subscribed feed's downloaded cover is re-fetchable and safe to trim.
        """
        return self._save_episode_artwork(slug, episode_id, image_data,
                                          content_type, evict=evict)

    def _save_episode_artwork(self, slug: str, episode_id: str,
                              image_data: bytes, content_type: str,
                              evict: bool = True) -> bool:
        """Write one episode cover, replacing any stale extension, then trim the feed's cache
        under EPISODE_ARTWORK_CACHE_BYTES (unless ``evict=False``, see ``save_episode_artwork``)."""
        art_dir = self._episode_artwork_dir(slug, create=True)
        if art_dir is None:
            return False

        ext = _EXTENSION_BY_TYPE.get(content_type.lower(), '.jpg')
        artwork_path = _safe_join_under(art_dir, f"{episode_id}{ext}")

        with tempfile.NamedTemporaryFile(mode='wb', delete=False,
                                         dir=art_dir, suffix='.tmp') as tmp:
            tmp.write(image_data)
            tmp_path = tmp.name
        os.replace(tmp_path, artwork_path)

        for old_ext, _ in _ARTWORK_EXTENSIONS:
            old_path = _safe_join_under(art_dir, f"{episode_id}{old_ext}")
            if old_path.exists() and old_path != artwork_path:
                old_path.unlink()

        if evict:
            self._evict_episode_artwork(art_dir)
        return True

    def _evict_episode_artwork(self, art_dir: Path) -> int:
        """Drop least-recently-served covers until the directory fits the cap.
        Returns the number of files removed."""
        known_suffixes = {ext for ext, _ in _ARTWORK_EXTENSIONS}
        entries = []
        total = 0
        for path in art_dir.iterdir():
            # Skip .tmp files: another thread is mid-write, and unlinking one
            # would break the os.replace it is about to do.
            if not path.is_file() or path.suffix not in known_suffixes:
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((stat.st_mtime, stat.st_size, path))
            total += stat.st_size

        if total <= EPISODE_ARTWORK_CACHE_BYTES:
            return 0

        removed = 0
        for _, size, path in sorted(entries):
            if total <= EPISODE_ARTWORK_CACHE_BYTES:
                break
            try:
                path.unlink()
            except OSError:
                continue
            total -= size
            removed += 1

        if removed:
            logger.info("episode_artwork_evicted count=%d dir=%s", removed, art_dir.name)
        return removed

    def clear_watermark_cache(self, slug: str) -> None:
        """Drop the cached badge variant (issue #420) so it recomposites on the next request.

        Called on a new source cover and on artwork refresh, so badge or toggle changes apply."""
        variant_path = self.get_podcast_dir(slug) / _WATERMARK_VARIANT
        variant_path.unlink(missing_ok=True)

    def _badge_position(self) -> str:
        """Configured badge corner (issue #600). Folded into the salt so a
        change re-composites the cached variant on the next fetch."""
        return normalize_badge_position(self.db.get_setting('artwork_badge_position'))

    def get_watermarked_artwork(self, slug: str) -> tuple[bytes, str] | None:
        """Cover art with the badge composited (issue #420), cached as artwork-minuspod.jpg.

        Returns (jpeg_bytes, 'image/jpeg'), or None without source artwork or on compositing failure."""
        podcast_dir = self.podcast_dir_if_exists(slug)
        if not podcast_dir:
            return None
        variant_path = podcast_dir / _WATERMARK_VARIANT

        if variant_path.exists() and not self._watermark_variant_stale(
                podcast_dir, variant_path):
            try:
                with open(variant_path, 'rb') as f:
                    return f.read(), 'image/jpeg'
            except OSError as e:
                logger.warning(f"[{slug}] failed reading watermark cache: {e}")

        source = self.get_artwork(slug)
        if not source:
            return None
        position = self._badge_position()
        composited = composite_watermark(source[0], position)
        if not composited:
            return None

        try:
            with tempfile.NamedTemporaryFile(mode='wb', delete=False,
                                             dir=podcast_dir, suffix='.tmp') as tmp:
                tmp.write(composited)
                tmp_path = tmp.name
            os.replace(tmp_path, variant_path)
            (podcast_dir / _WATERMARK_SALT).write_text(cover_badge_salt(position))
        except OSError as e:
            logger.warning(f"[{slug}] failed caching watermark: {e}")

        return composited, 'image/jpeg'

    def artwork_version(self, slug: str) -> str | None:
        """Short content-addressed token to cache-bust the badged cover-art URL.

        Changes only when the source cover bytes or the badge (cover_badge_salt) change, since
        podcast apps cache channel art by URL. None when there is no readable source cover, so
        the caller uses the bare URL instead of aborting the feed render.
        """
        try:
            source = self.get_artwork(slug)
        except OSError as e:
            logger.warning(f"[{slug}] artwork_version read failed: {e}")
            return None
        if not source:
            return None
        digest = hashlib.md5(source[0], usedforsecurity=False)
        digest.update(cover_badge_salt(self._badge_position()).encode())
        return digest.hexdigest()[:8]

    def _watermark_variant_stale(self, podcast_dir, variant_path) -> bool:
        """True if the cached badge variant predates the source cover or badge asset.

        Covers the passive refresh path that never clears the cache.
        """
        try:
            variant_mtime = variant_path.stat().st_mtime
        except OSError:
            return True
        try:
            recorded_salt = (podcast_dir / _WATERMARK_SALT).read_text()
        except OSError:
            recorded_salt = None
        if recorded_salt != cover_badge_salt(self._badge_position()):
            return True
        newest = 0.0
        inputs = [podcast_dir / f"artwork{ext}"
                  for ext in ('.jpg', '.png', '.gif', '.webp')]
        inputs.append(badge_path())
        for path in inputs:
            try:
                if path is not None and path.exists():
                    newest = max(newest, path.stat().st_mtime)
            except OSError:
                return True
        return newest > variant_mtime

    def has_artwork(self, slug: str) -> bool:
        """True if any cached source artwork file exists (no read)."""
        podcast_dir = self.get_podcast_dir(slug)
        return any((podcast_dir / f"artwork{ext}").exists()
                   for ext in ('.jpg', '.png', '.gif', '.webp'))

    @staticmethod
    def _normalize_artwork_candidates(artwork_urls) -> list[str]:
        """Coerce a single URL or an ordered list into a deduped candidate list."""
        if not artwork_urls:
            return []
        if isinstance(artwork_urls, str):
            return [artwork_urls]
        seen = set()
        out = []
        for url in artwork_urls:
            if url and url not in seen:
                seen.add(url)
                out.append(url)
        return out

    @staticmethod
    def _artwork_negative_cache_key(slug: str, url: str, user_agent: str) -> str:
        return f"{slug}\n{url}\n{user_agent}"

    @staticmethod
    def _artwork_failure_state(podcast: dict | None) -> dict:
        """Parse the durable per-URL failure record off a podcast row."""
        raw = (podcast or {}).get('artwork_failure_state')
        if not raw:
            return {}
        try:
            state = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return state if isinstance(state, dict) else {}

    @staticmethod
    def _prune_artwork_failure_state(state: dict) -> dict:
        """Drop entries past their backoff window, then keep the newest N."""
        now = utc_now()
        live = {}
        for url, entry in state.items():
            failed_at = parse_iso_utc((entry or {}).get('at'))
            if failed_at is None:
                continue
            window = _ARTWORK_FAILURE_WINDOWS.get(
                entry.get('status'), ARTWORK_FAILURE_TTL_SECONDS)
            if (now - failed_at).total_seconds() < window:
                live[url] = entry
        if len(live) <= ARTWORK_FAILURE_STATE_MAX_ENTRIES:
            return live
        newest = sorted(live.items(), key=lambda item: item[1].get('at') or '',
                        reverse=True)[:ARTWORK_FAILURE_STATE_MAX_ENTRIES]
        return dict(newest)

    def _artwork_in_backoff(self, slug: str, url: str, podcast: dict | None,
                            user_agent: str | None = None) -> bool:
        """True if `url` failed recently enough to skip.

        Checks the in-process caches, then the durable DB record (authoritative, re-read each call).
        """
        ua = user_agent or download_user_agent(podcast)
        key = self._artwork_negative_cache_key(slug, url, ua)
        if self._artwork_failure_cache.get(key) is not None:
            return True
        if self._artwork_404_cache.get(key) is not None:
            return True
        entry = self._artwork_failure_state(podcast).get(url)
        if not entry or entry.get('ua') != ua:
            return False
        failed_at = parse_iso_utc(entry.get('at'))
        if failed_at is None:
            return False
        status = entry.get('status')
        window = _ARTWORK_FAILURE_WINDOWS.get(status, ARTWORK_FAILURE_TTL_SECONDS)
        # Do not warm the in-process cache: its full TTL would restart now and could outlast this deadline.
        return (utc_now() - failed_at).total_seconds() < window

    def _record_artwork_negative_cache(self, slug: str, podcast: dict | None,
                                       url: str, status: str, user_agent: str | None = None) -> None:
        """Memoize a failed candidate URL, in-process and durably.

        Mutates podcast['artwork_failure_state'] in place so later candidates see earlier
        failures instead of clobbering them from a stale snapshot.
        """
        ua = user_agent or download_user_agent(podcast)
        key = self._artwork_negative_cache_key(slug, url, ua)
        self._artwork_skip_logged.delete(key)
        if status == 'not_found':
            self._artwork_404_cache.set(key, True)
            self._artwork_failure_cache.delete(key)
        else:
            self._artwork_failure_cache.set(key, True)
            self._artwork_404_cache.delete(key)
        state = self._artwork_failure_state(podcast)
        state[url] = {'status': status, 'at': utc_now_iso(), 'ua': ua}
        state = self._prune_artwork_failure_state(state)
        try:
            self.db.update_podcast(slug, artwork_failure_state=json.dumps(state))
            if podcast is not None:
                podcast['artwork_failure_state'] = json.dumps(state)
        except Exception as e:
            logger.warning(f"[{slug}] Failed to persist artwork failure state: {e}")

    def _clear_artwork_negative_cache(self, slug: str, podcast: dict | None, url: str,
                                      user_agent: str | None = None) -> None:
        """Drop a URL's failure memo, in-process and durably, on success."""
        ua = user_agent or download_user_agent(podcast)
        key = self._artwork_negative_cache_key(slug, url, ua)
        self._artwork_failure_cache.delete(key)
        self._artwork_404_cache.delete(key)
        self._artwork_skip_logged.delete(key)
        state = self._artwork_failure_state(podcast)
        # The write path prunes, so clearing only has to drop this entry.
        if state.pop(url, None) is not None:
            try:
                self.db.update_podcast(slug, artwork_failure_state=json.dumps(state))
                if podcast is not None:
                    podcast['artwork_failure_state'] = json.dumps(state)
            except Exception as e:
                logger.warning(f"[{slug}] Failed to clear artwork failure state: {e}")

    def download_artwork(self, slug: str, artwork_urls: str | list[str] | None,
                         force: bool = False, bypass_backoff: bool | None = None) -> bool:
        """Download and cache podcast artwork, trying each candidate in order.

        Takes one URL or an ordered list of feed-declared candidates. The cached artwork_url
        is appended as a final fallback: if every candidate fails, the existing file and DB row
        are left untouched. Failures are memoized per candidate (see _artwork_in_backoff).
        """
        # force bypasses the "already cached" guard but not the per-URL failure backoff
        # unless the caller asks (an explicit user refresh; a new candidate has no record).
        if bypass_backoff is None:
            bypass_backoff = force
        candidates = self._normalize_artwork_candidates(artwork_urls)
        if not candidates:
            return False

        podcast = self.db.get_podcast_by_slug(slug)
        ua = download_user_agent(podcast)
        cached_url = (podcast or {}).get('artwork_url')
        if cached_url and cached_url not in candidates:
            candidates = [*candidates, cached_url]

        for url in candidates:
            if not bypass_backoff and self._artwork_in_backoff(slug, url, podcast, user_agent=ua):
                skip_key = self._artwork_negative_cache_key(slug, url, ua)
                if self._artwork_skip_logged.get(skip_key) is None:
                    self._artwork_skip_logged.set(skip_key, True)
                    logger.debug(f"[{slug}] Skipping artwork retry for {safe_url_for_log(url)}, "
                                 "it failed recently")
                continue
            ok, status = self._download_artwork_uncached(slug, url, force, user_agent=ua)
            if ok:
                self._clear_artwork_negative_cache(slug, podcast, url, user_agent=ua)
                return True
            self._record_artwork_negative_cache(slug, podcast, url, status, user_agent=ua)

        return False

    def _download_artwork_uncached(self, slug: str, artwork_url: str,
                                   force: bool, user_agent: str | None = None) -> tuple[bool, str | None]:
        """Validate artwork; return (success, status), mapping non-retryable 4xx responses to not_found."""
        try:
            # Check if we already have this artwork on disk. Callers that
            # already wrote the new URL to the row pass force, since the
            # comparison below would then match the URL against itself.
            podcast = self.db.get_podcast_by_slug(slug)
            if not force and podcast and podcast.get('artwork_url') == artwork_url and podcast.get('artwork_cached'):
                if self.get_artwork(slug) is not None:
                    logger.debug(f"[{slug}] Artwork already cached")
                    return True, None
                logger.info(f"[{slug}] artwork_cached flag set but file missing, re-downloading")

            logger.info(f"[{slug}] Downloading artwork from {safe_url_for_log(artwork_url)}")

            headers = {
                'User-Agent': user_agent or download_user_agent(podcast),
                'Accept': '*/*',
                'Accept-Language': 'en-US,en;q=0.9',
            }
            try:
                response = safe_get(
                    artwork_url,
                    trust=URLTrust.FEED_CONTENT,
                    max_redirects=HTTP_MAX_REDIRECTS_FEED,
                    timeout=HTTP_TIMEOUT_FETCH,
                    stream=True,
                    headers=headers,
                )
            except SSRFError as e:
                logger.warning(f"[{slug}] SSRF blocked in download_artwork: {e}")
                return False, 'error'
            try:
                response.raise_for_status()
            except Exception:
                status_code = getattr(response, 'status_code', None)
                logger.warning(
                    "[%s] artwork_fetch_failed status=%s url=%s",
                    slug, status_code, safe_url_for_log(artwork_url),
                )
                not_found = (status_code is not None and 400 <= status_code < 500
                             and status_code not in (408, 429))
                return False, ('not_found' if not_found else 'error')

            declared_type = (response.headers.get('Content-Type') or '').split(';', 1)[0].strip().lower()
            if declared_type and declared_type not in _ALLOWED_IMAGE_TYPES:
                logger.warning(
                    "[%s] artwork_rejected_content_type declared=%s url=%s",
                    slug, declared_type, safe_url_for_log(artwork_url),
                )
                return False, 'error'

            max_bytes = _max_artwork_bytes()
            try:
                image_data = read_response_capped(response, max_bytes, chunk_size=65536)
            except ResponseTooLargeError:
                logger.warning(
                    "[%s] artwork_size_cap_exceeded max=%d url=%s",
                    slug, max_bytes, safe_url_for_log(artwork_url),
                )
                return False, 'error'

            detected = _detect_image_mime(image_data)
            if not detected:
                logger.warning(
                    "[%s] artwork_rejected_magic declared=%s url=%s",
                    slug, declared_type, safe_url_for_log(artwork_url),
                )
                return False, 'error'

            ok = self.save_artwork(slug, image_data, detected, artwork_url)
            return ok, (None if ok else 'error')

        except Exception as e:
            logger.warning(f"[{slug}] Failed to download artwork: {e}")
            return False, 'error'

    # ========== Cleanup Methods ==========

    def delete_processed_file(self, slug: str, episode_id: str,
                               keep_original: bool = False,
                               can_delete=None) -> bool:
        """Delete the processed audio file(s) and any retained original.

        keep_original=True skips the original: local feeds keep it as the
        only copy (no upstream to re-download).
        """
        audio_deleted = False
        candidates = list(self.iter_episode_audio_paths(slug, episode_id, ".mp3"))
        peaks = None
        if not keep_original:
            peaks = self.get_peaks_path(slug, episode_id)
            if peaks.exists():
                candidates.append(peaks)  # before the original so a fence stop never orphans it
            original = self.get_original_path(slug, episode_id, ".mp3")
            if original and original.exists():
                candidates.append(original)
        fenced = False
        for path in candidates:
            if can_delete is not None and not can_delete():
                fenced = True
                break
            if path.exists():
                path.unlink()
                audio_deleted = audio_deleted or path != peaks
        if not keep_original and not fenced:
            remove_peaks_artifacts(peaks)
        if audio_deleted:
            logger.debug(f"[{slug}:{episode_id}] Deleted processed/original audio files")
        return audio_deleted

    def delete_original_only(self, slug: str, episode_id: str,
                             extension: str = ".mp3") -> tuple[bool, int]:
        """Delete just the retained pre-cut original; returns (deleted, bytes_freed).

        Processed files, DB rows and transcripts are untouched, so originals can expire sooner.
        """
        original = self.get_original_path(slug, episode_id, extension)
        if not original or not original.exists():
            self.delete_peaks_file(slug, episode_id)
            return False, 0
        try:
            size = original.stat().st_size
            original.unlink()
            self.delete_peaks_file(slug, episode_id)
            logger.debug(
                f"[{slug}:{episode_id}] Deleted original audio (freed {size} bytes)"
            )
            return True, size
        except OSError as exc:
            logger.warning(
                f"[{slug}:{episode_id}] Failed to delete original audio: {exc}"
            )
            return False, 0


    def cleanup_episode_files(self, slug: str, episode_id: str) -> int:
        """Delete all files for an episode. Returns bytes freed.

        VTT and chapters live in the database; cascade delete handles episode_details.
        """
        freed = 0

        # Only delete MP3 files - VTT and chapters are now in database.
        # Originals (retained for ad-editor review) are cleaned on the
        # same schedule as the processed output.
        paths = list(self.iter_episode_audio_paths(slug, episode_id, '.mp3'))
        original = self.get_original_path(slug, episode_id, '.mp3')
        if original.exists():
            paths.append(original)
        for path in paths:
            if path.exists():
                try:
                    freed += path.stat().st_size
                    path.unlink()
                except Exception as e:
                    logger.warning(f"Failed to delete {path}: {e}")

        try:
            peaks_size = self.get_peaks_path(slug, episode_id).stat().st_size
        except OSError:
            peaks_size = 0
        if self.delete_peaks_file(slug, episode_id):
            freed += peaks_size
        freed += self.remove_chapter_images(slug, episode_id)
        return freed

    def cleanup_podcast_dir(self, slug: str) -> bool:
        """Delete podcast directory and all files."""
        try:
            podcast_dir = _safe_join_under(self.podcasts_dir, slug)
        except PathContainmentError:
            logger.warning(f"Refusing to delete non-contained path for slug {slug!r}")
            return False

        if podcast_dir.exists():
            try:
                shutil.rmtree(podcast_dir)
                logger.info(f"[{slug}] Deleted podcast directory")
                return True
            except Exception as e:
                logger.error(f"[{slug}] Failed to delete directory: {e}")
                return False

        return True

    def get_storage_stats(self) -> dict[str, Any]:
        """Get storage statistics, cached for STORAGE_STATS_TTL_SECONDS (the walk stat()s every file)."""
        with self._storage_stats_lock:
            cached = self._storage_stats_cache.get('storage')
            if cached is not None:
                return cached

            total_size = 0
            file_count = 0

            for podcast_dir in self.podcasts_dir.iterdir():
                if podcast_dir.is_dir():
                    for f in podcast_dir.rglob('*'):
                        if f.is_file() and not is_peaks_housekeeping_name(f.name):
                            total_size += f.stat().st_size
                            file_count += 1

            stats = {
                'total_size_bytes': total_size,
                'total_size_mb': total_size / (1024 * 1024),
                'file_count': file_count
            }
            self._storage_stats_cache.set('storage', stats)
            return stats
