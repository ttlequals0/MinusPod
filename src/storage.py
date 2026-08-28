"""Storage management with SQLite database and file operations."""
import hashlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any
import tempfile
import shutil

from config import (
    BROWSER_USER_AGENT, HTTP_MAX_REDIRECTS_FEED, HTTP_TIMEOUT_FETCH,
    count_pending_review,
    get_env_backed_int, MAX_ARTWORK_BYTES_MIN, MAX_ARTWORK_BYTES_MAX,
)
from artwork_watermark import (
    composite_watermark, cover_badge_salt, badge_path, normalize_badge_position,
)
from utils.episode_paths import episode_filename
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
# A feed whose artwork URL serves the wrong content type fails identically on
# every refresh, so retrying each cycle only burns a request and a log line.
# Long enough to stop the churn, short enough that a publisher fix is picked
# up the same day. A forced refresh ignores it.
ARTWORK_FAILURE_TTL_SECONDS = 6 * 3600

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
# Per-feed ceiling on that directory. Publisher episode covers run large (a
# 3000x3000 JPEG is ~600 KB), and a long back catalogue would otherwise grow
# without limit, so the least recently served files are dropped past this.
EPISODE_ARTWORK_CACHE_BYTES = 64 * 1024 * 1024

# Cached cover-art badge variant (issue #420), one per podcast dir.
_WATERMARK_VARIANT = "artwork-minuspod.jpg"
# Sidecar recording the cover_badge_salt the cached variant was rendered with.
# Mtimes alone miss a BADGE_REVISION bump (same asset file, new rendering), so
# without it an upgrade can keep serving the old badge at the new cache-busted
# URL forever.
_WATERMARK_SALT = "artwork-minuspod.salt"


def _detect_image_mime(data: bytes) -> str | None:
    """Return the canonical Content-Type for ``data`` based on file magic,
    or None if the bytes do not match a supported image format. SVG is not
    accepted because it admits script execution.
    """
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
    """Artwork size cap, configurable so operators can host very high-res
    podcast covers without a code change. Default is 25 MB so animated GIF
    covers and 3000x3000 JPEGs are cached comfortably; clamped so a typo
    cannot turn this into a memory DoS. Env-backed: the UI value wins, the
    env var seeds the default at boot (config.ENV_BACKED_SETTINGS).
    """
    return get_env_backed_int('max_artwork_bytes',
                              floor=MAX_ARTWORK_BYTES_MIN,
                              ceiling=MAX_ARTWORK_BYTES_MAX)

logger = logging.getLogger(__name__)


class PathContainmentError(ValueError):
    """Raised when a slug or episode_id would resolve outside the storage root."""


def _safe_join_under(base: Path, *parts: str) -> Path:
    """Join ``parts`` under ``base`` and verify the result stays inside ``base``.

    Uses ``resolve()`` + ``relative_to()`` so symlink and ``..`` tricks raise
    rather than silently escaping. The base is assumed to already exist; the
    joined path may or may not.
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

    Singleton, mirroring Database. Without this, every /api/v1/health call
    (or any get_storage() accessor) constructs a fresh Storage and re-fires
    the init log line - that path was producing ~120 lines/hr in prod.
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
            data_dir = (
                os.environ.get("DATA_PATH")
                or os.environ.get("MINUSPOD_DATA_DIR")
                or "/app/data"
            )
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # Create podcasts subdirectory
        self.podcasts_dir = self.data_dir / "podcasts"
        self.podcasts_dir.mkdir(exist_ok=True)

        # Initialize database
        from database import Database
        self.db = Database(str(self.data_dir))

        # get_storage_stats() result cache (single 'storage' key). TTLCache
        # is documented lock-free, and this singleton is shared across Flask
        # request threads: the lock guards the check-walk-store sequence so
        # concurrent status calls serialize on the walk instead of
        # duplicating it.
        self._storage_stats_cache = TTLCache(STORAGE_STATS_TTL_SECONDS)
        self._storage_stats_lock = threading.Lock()

        # Artwork URLs that failed validation, so a broken one is not refetched
        # on every feed refresh. Keyed by slug and URL, so a changed URL retries
        # at once.
        self._artwork_failure_cache = TTLCache(ARTWORK_FAILURE_TTL_SECONDS)

        self._initialized = True
        logger.info(f"Storage initialized with data_dir: {self.data_dir}")

    def get_podcast_dir(self, slug: str) -> Path:
        """Get podcast directory, creating if necessary.

        Validates ``slug`` against traversal patterns and confirms the
        resolved path stays under ``self.podcasts_dir``.
        """
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
        """Per-feed staging directory for an in-progress archive-import
        commit (``<data>/import-staging/<slug>/``, local-feeds #625 Task 10).

        Browser-uploaded batch files land here ahead of the commit engine's
        move into the podcast's episode storage. A commit whose scan/commit
        request source included staging ('staging' or 'both') sweeps this
        directory clean -- committed audio, consumed sidecars, rejected
        files, and anything else left in it -- and removes the directory
        itself once the commit finishes, whether or not every entry
        actually committed. A commit scanned/run with source='directory'
        never touches this directory at all, even if it has stale content.
        Outside a commit, a stale staging dir just means an upload/scan
        happened and nothing has committed yet; `DELETE
        /feeds/{slug}/import/staging` (local_import.py) clears it on
        demand.
        """
        if is_dangerous_slug(slug):
            raise PathContainmentError(f"refusing dangerous slug {slug!r}")
        path = _safe_join_under(self.data_dir, 'import-staging', slug)
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    def import_source_dir(self, slug: str) -> Path:
        """The user-managed archive-import directory for a feed
        (``<data>/import/<slug>/``, local-feeds #625 Task 10).

        Never created here: an operator populates it directly on the shared
        filesystem, so its absence just means nothing has been dropped in
        yet. The commit engine moves each successfully committed entry's
        audio out of it AND deletes that entry's sidecar files (json/txt/
        artwork) with it -- a rejected or errored entry's files are left in
        place untouched, for the operator to fix and re-scan.
        """
        if is_dangerous_slug(slug):
            raise PathContainmentError(f"refusing dangerous slug {slug!r}")
        return _safe_join_under(self.data_dir, 'import', slug)

    def load_data_json(self, slug: str) -> dict[str, Any]:
        """Load episode data for a podcast from SQLite."""
        # Ensure directory exists
        self.get_podcast_dir(slug)

        podcast = self.db.get_podcast_by_slug(slug)
        if not podcast:
            return {"episodes": {}, "last_checked": None}

        episodes, _ = self.db.get_episodes(slug, limit=10000)

        episodes_dict = {}
        for ep in episodes:
            ep_data = {
                'status': ep['status'],
                'original_url': ep['original_url'],
                'title': ep['title'],
            }
            if ep['processed_file']:
                ep_data['processed_file'] = ep['processed_file']
            if ep['processed_at']:
                ep_data['processed_at'] = ep['processed_at']
            if ep['original_duration']:
                ep_data['original_duration'] = ep['original_duration']
            if ep['new_duration']:
                ep_data['new_duration'] = ep['new_duration']
            if ep['ads_removed']:
                ep_data['ads_removed'] = ep['ads_removed']
            if ep['error_message']:
                ep_data['error'] = ep['error_message']

            episodes_dict[ep['episode_id']] = ep_data

        return {
            "episodes": episodes_dict,
            "last_checked": podcast.get('last_checked_at')
        }

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
        """Remove every audio file except the current version.

        Clients hitting the legacy unversioned URL still resolve via
        ``serve_episode``, which reads ``processed_version`` from the DB and
        falls through to the current file, so we can delete everything else
        immediately on finalize. The retained ``{episode_id}-original`` file
        is untouched (``iter_episode_audio_paths`` does not include it).
        Returns the number of files deleted.
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
        """Atomically persist chapters JSON plus the applied cut list it was
        generated against (single DB statement; a failure can never leave
        fresh chapters paired with stale cuts, which would poison the next
        recut remap)."""
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
        """Save Claude's ad detection response to database with pass marker.

        Args:
            slug: Podcast slug
            episode_id: Episode ID
            ads_data: Dict with 'ads', 'raw_response', and 'prompt' keys
            pass_number: 1 for first pass, 2 for second pass (default: 1)
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

        logger.debug(f"[{slug}:{episode_id}] Saved {len(all_ads)} combined ad markers")


    # ========== Artwork Methods ==========

    def save_artwork(self, slug: str, image_data: bytes, content_type: str,
                    source_url: str = None) -> bool:
        """Save podcast artwork to filesystem."""
        try:
            podcast_dir = self.get_podcast_dir(slug)

            ext = _EXTENSION_BY_TYPE.get(content_type.lower(), '.jpg')

            artwork_path = podcast_dir / f"artwork{ext}"

            # Write the new image to a temp file and atomically move it into
            # place, then delete the other-extension stale files. Deleting only
            # after the new artwork is durable means a failed write can never
            # leave the podcast with no artwork (secrets-storage-6).
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

    def get_artwork(self, slug: str) -> tuple[bytes, str] | None:
        """Get cached artwork. Returns (data, content_type) or None."""
        podcast_dir = self.podcast_dir_if_exists(slug)
        if not podcast_dir:
            return None

        for ext, content_type in _ARTWORK_EXTENSIONS:
            artwork_path = podcast_dir / f"artwork{ext}"
            if artwork_path.exists():
                with open(artwork_path, 'rb') as f:
                    return f.read(), content_type

        return None

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
        """True if a cached episode cover exists -- no read, no LRU touch.

        Mirrors has_artwork's existence-only contract at episode scope.
        Callers that only need a boolean (e.g. deciding whether to emit
        itunes:image) should use this instead of get_episode_artwork, which
        reads the full file and bumps its mtime for LRU eviction purposes.
        """
        if not is_valid_episode_id(episode_id):
            return False
        art_dir = self._episode_artwork_dir(slug)
        if not art_dir or not art_dir.is_dir():
            return False
        return any(_safe_join_under(art_dir, f"{episode_id}{ext}").exists()
                   for ext, _ in _ARTWORK_EXTENSIONS)

    def remove_episode_artwork(self, slug: str, episode_id: str) -> bool:
        """Delete a cached episode cover, if any.

        Used when an episode row is hard-deleted (local feeds, #625 Task 8)
        so no orphaned cover file survives the row it belonged to. Returns
        True if a file was removed.
        """
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

    def get_episode_artwork(self, slug: str,
                            episode_id: str) -> tuple[bytes, str] | None:
        """Cached episode cover. Returns (data, content_type) or None.

        Serving bumps the mtime so eviction can drop the covers nobody is
        looking at rather than the oldest episodes.
        """
        if not is_valid_episode_id(episode_id):
            return None
        art_dir = self._episode_artwork_dir(slug)
        if not art_dir or not art_dir.is_dir():
            return None

        for ext, content_type in _ARTWORK_EXTENSIONS:
            path = _safe_join_under(art_dir, f"{episode_id}{ext}")
            if path.exists():
                with open(path, 'rb') as f:
                    data = f.read()
                try:
                    os.utime(path, None)
                except OSError:
                    pass
                return data, content_type

        return None

    def download_episode_artwork(self, slug: str, episode_id: str,
                                 artwork_url: str) -> bool:
        """Fetch and cache one episode's cover.

        Shares the validation contract of ``download_artwork``: file-magic
        allowlist, size cap, and a failure cache so a host that blocks or
        404s is not re-fetched on every page load. Deliberately does not
        touch the badge variant, which is podcast-level and unaffected.
        """
        if not artwork_url or not is_valid_episode_id(episode_id):
            return False

        failure_key = f"{slug}\n{episode_id}\n{artwork_url}"
        if self._artwork_failure_cache.get(failure_key):
            logger.debug(
                f"[{slug}:{episode_id}] Skipping episode artwork retry, "
                f"this URL failed recently")
            return False

        ok = self._download_episode_artwork_uncached(slug, episode_id, artwork_url)
        # Only failures are stored. Caching successes too would fill the cache
        # with entries nothing reads, and eviction is oldest-first, so a
        # long-lived failure entry would be pushed out well before its TTL.
        # No delete-on-success here: unlike the podcast path there is no
        # force bypass, so a live failure entry always returns early above
        # and success can never coexist with one.
        if not ok:
            self._artwork_failure_cache.set(failure_key, True)
        return ok

    def _download_episode_artwork_uncached(self, slug: str, episode_id: str,
                                          artwork_url: str) -> bool:
        """Fetch, validate, and save one episode cover. See
        download_episode_artwork."""
        try:
            logger.info(f"[{slug}:{episode_id}] Downloading episode artwork from "
                        f"{safe_url_for_log(artwork_url)}")

            headers = {
                'User-Agent': BROWSER_USER_AGENT,
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
                    slug, episode_id, declared_type, artwork_url,
                )
                return False

            max_bytes = _max_artwork_bytes()
            try:
                image_data = read_response_capped(response, max_bytes, chunk_size=65536)
            except ResponseTooLargeError:
                logger.warning(
                    "[%s:%s] episode_artwork_size_cap_exceeded max=%d url=%s",
                    slug, episode_id, max_bytes, artwork_url,
                )
                return False

            detected = _detect_image_mime(image_data)
            if not detected:
                logger.warning(
                    "[%s:%s] episode_artwork_rejected_magic declared=%s url=%s",
                    slug, episode_id, declared_type, artwork_url,
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
        """Public wrapper around ``_save_episode_artwork`` for API-driven
        uploads (local feeds' single-episode artwork upload, #625 Task 8).
        ``content_type`` must already be validated (e.g. via
        ``_detect_image_mime``) -- this method does not re-check it.

        ``evict=False`` skips the LRU cache trim. Local-feed episode
        artwork is the only copy of that cover (there is no upstream URL
        to re-download it from later), so it must never be evicted; a
        subscribed feed's downloaded cover is a re-fetchable cache and is
        safe to trim under the size cap (the default).
        """
        return self._save_episode_artwork(slug, episode_id, image_data,
                                          content_type, evict=evict)

    def _save_episode_artwork(self, slug: str, episode_id: str,
                              image_data: bytes, content_type: str,
                              evict: bool = True) -> bool:
        """Write one episode cover, replacing any stale extension, then trim
        the feed's cache back under EPISODE_ARTWORK_CACHE_BYTES (unless
        ``evict=False`` -- see ``save_episode_artwork``)."""
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
        """Drop the cached MinusPod badge variant (issue #420) so it recomposites
        on the next request. Called when a new source cover is saved and by the
        artwork refresh, so a change to the badge rendering or the toggle takes
        effect even when the upstream cover itself is unchanged."""
        variant_path = self.get_podcast_dir(slug) / _WATERMARK_VARIANT
        variant_path.unlink(missing_ok=True)

    def _badge_position(self) -> str:
        """Configured badge corner (issue #600). Folded into the salt so a
        change re-composites the cached variant on the next fetch."""
        return normalize_badge_position(self.db.get_setting('artwork_badge_position'))

    def get_watermarked_artwork(self, slug: str) -> tuple[bytes, str] | None:
        """Cover art with the MinusPod badge composited (issue #420), cached on
        disk as artwork-minuspod.jpg. Returns (jpeg_bytes, 'image/jpeg'), or None
        when there is no source artwork or compositing fails. save_artwork and the
        artwork refresh clear the cached variant so it recomposites."""
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
        """Short content-addressed token for the badged cover-art URL cache-bust.

        Shifts when the source cover bytes or the badge (cover_badge_salt: badge
        asset fingerprint, BADGE_REVISION, corner) change, and is stable otherwise,
        so downstream apps (Pocket Casts et al. cache channel art by URL and
        rarely re-pull it) only re-fetch when the art actually changed. None when
        there is no readable source cover, so the caller falls back to the bare
        URL rather than aborting the whole feed render on an I/O error.
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
        """True if the cached badge variant predates the source cover or the
        badge asset, so a fresh composite is served after a cover or badge
        change even on the passive refresh path that never clears the cache.
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

    def download_artwork(self, slug: str, artwork_url: str,
                         force: bool = False) -> bool:
        """Download and cache podcast artwork.

        Content-Type header is advisory only; the saved bytes are validated
        against a fixed file-magic allowlist (JPEG/PNG/GIF/WebP). SVG is
        excluded because it admits script execution. Oversize responses are
        rejected outright with a structured log rather than saved partially.
        """
        if not artwork_url:
            return False

        failure_key = f"{slug}\n{artwork_url}"
        if not force and self._artwork_failure_cache.get(failure_key):
            logger.debug(
                f"[{slug}] Skipping artwork retry, this URL failed recently")
            return False

        ok = self._download_artwork_uncached(slug, artwork_url, force)
        # Failures only; see download_episode_artwork. A forced retry that
        # succeeds clears the entry so the unforced path stops being blocked.
        if ok:
            self._artwork_failure_cache.delete(failure_key)
        else:
            self._artwork_failure_cache.set(failure_key, True)
        return ok

    def _download_artwork_uncached(self, slug: str, artwork_url: str,
                                   force: bool) -> bool:
        """Fetch, validate, and save artwork. See download_artwork."""
        try:
            # Check if we already have this artwork on disk. Callers that
            # already wrote the new URL to the row pass force, since the
            # comparison below would then match the URL against itself.
            podcast = None if force else self.db.get_podcast_by_slug(slug)
            if podcast and podcast.get('artwork_url') == artwork_url and podcast.get('artwork_cached'):
                if self.get_artwork(slug) is not None:
                    logger.debug(f"[{slug}] Artwork already cached")
                    return True
                logger.info(f"[{slug}] artwork_cached flag set but file missing, re-downloading")

            logger.info(f"[{slug}] Downloading artwork from {safe_url_for_log(artwork_url)}")

            headers = {
                'User-Agent': BROWSER_USER_AGENT,
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
                return False
            response.raise_for_status()

            declared_type = (response.headers.get('Content-Type') or '').split(';', 1)[0].strip().lower()
            if declared_type and declared_type not in _ALLOWED_IMAGE_TYPES:
                logger.warning(
                    "[%s] artwork_rejected_content_type declared=%s url=%s",
                    slug, declared_type, artwork_url,
                )
                return False

            max_bytes = _max_artwork_bytes()
            try:
                image_data = read_response_capped(response, max_bytes, chunk_size=65536)
            except ResponseTooLargeError:
                logger.warning(
                    "[%s] artwork_size_cap_exceeded max=%d url=%s",
                    slug, max_bytes, artwork_url,
                )
                return False

            detected = _detect_image_mime(image_data)
            if not detected:
                logger.warning(
                    "[%s] artwork_rejected_magic declared=%s url=%s",
                    slug, declared_type, artwork_url,
                )
                return False

            return self.save_artwork(slug, image_data, detected, artwork_url)

        except Exception as e:
            logger.warning(f"[{slug}] Failed to download artwork: {e}")
            return False

    # ========== Cleanup Methods ==========

    def delete_processed_file(self, slug: str, episode_id: str,
                               keep_original: bool = False) -> bool:
        """Delete the processed audio file(s) and any retained original.

        keep_original=True skips the original: local feeds keep it as the
        only copy (no upstream to re-download).
        """
        deleted = False
        candidates = list(self.iter_episode_audio_paths(slug, episode_id, ".mp3"))
        if not keep_original:
            original = self.get_original_path(slug, episode_id, ".mp3")
            if original and original.exists():
                candidates.append(original)
        for path in candidates:
            if path.exists():
                path.unlink()
                deleted = True
        if deleted:
            logger.debug(f"[{slug}:{episode_id}] Deleted processed/original audio files")
        return deleted

    def delete_original_only(self, slug: str, episode_id: str,
                             extension: str = ".mp3") -> tuple[bool, int]:
        """Delete just the retained pre-cut original for one episode.

        Returns (deleted, bytes_freed). Processed file(s), DB rows, and
        transcripts are untouched. Used by the 2.5.14 two-pass retention
        cleanup so an operator can drop originals on a shorter schedule
        than the processed output without resetting the episode to
        Discovered.
        """
        original = self.get_original_path(slug, episode_id, extension)
        if not original or not original.exists():
            return False, 0
        try:
            size = original.stat().st_size
            original.unlink()
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

        Note: VTT and chapters are now stored in database, not files.
        Database cascade delete handles episode_details when episode is deleted.
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
        """Get storage statistics.

        The full-tree walk stat()s every file under every podcast dir, so
        the result is cached for STORAGE_STATS_TTL_SECONDS. The lock
        serializes the check-walk-store sequence across request threads.
        """
        with self._storage_stats_lock:
            cached = self._storage_stats_cache.get('storage')
            if cached is not None:
                return cached

            total_size = 0
            file_count = 0

            for podcast_dir in self.podcasts_dir.iterdir():
                if podcast_dir.is_dir():
                    for f in podcast_dir.rglob('*'):
                        if f.is_file():
                            total_size += f.stat().st_size
                            file_count += 1

            stats = {
                'total_size_bytes': total_size,
                'total_size_mb': total_size / (1024 * 1024),
                'file_count': file_count
            }
            self._storage_stats_cache.set('storage', stats)
            return stats
