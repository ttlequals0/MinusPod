"""Storage management with SQLite database and file operations."""
import json
import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
import tempfile
import shutil

from config import BROWSER_USER_AGENT, HTTP_MAX_REDIRECTS_FEED, HTTP_TIMEOUT_FETCH
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


_ALLOWED_IMAGE_TYPES = frozenset({
    'image/jpeg',
    'image/jpg',
    'image/png',
    'image/gif',
    'image/webp',
})


def _detect_image_mime(data: bytes) -> Optional[str]:
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
    podcast covers without a code change. Default is 5 MB; clamped to a
    sensible floor/ceiling so a typo cannot turn this into a memory DoS."""
    try:
        raw = int(os.environ.get('MINUSPOD_MAX_ARTWORK_BYTES', 5 * 1024 * 1024))
    except ValueError:
        raw = 5 * 1024 * 1024
    return max(64 * 1024, min(raw, 50 * 1024 * 1024))

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
    """Storage manager using SQLite for metadata and filesystem for large files."""

    def __init__(self, data_dir: Optional[str] = None):
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

        logger.info(f"Storage initialized with data_dir: {self.data_dir}")

    def get_podcast_dir(self, slug: str) -> Path:
        """Get podcast directory, creating if necessary.

        Validates ``slug`` against traversal patterns and confirms the
        resolved path stays under ``self.podcasts_dir``.
        """
        if is_dangerous_slug(slug):
            raise PathContainmentError(f"refusing dangerous slug {slug!r}")
        podcast_dir = _safe_join_under(self.podcasts_dir, slug)
        podcast_dir.mkdir(exist_ok=True)

        # Ensure episodes directory exists
        episodes_dir = podcast_dir / "episodes"
        episodes_dir.mkdir(exist_ok=True)

        return podcast_dir

    def load_data_json(self, slug: str) -> Dict[str, Any]:
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

    def save_data_json(self, slug: str, data: Dict[str, Any]) -> None:
        """Save episode data to SQLite."""
        # Ensure podcast exists
        podcast = self.db.get_podcast_by_slug(slug)
        if not podcast:
            self.db.create_podcast(slug, "")

        # Update last_checked
        if data.get('last_checked'):
            self.db.update_podcast(slug, last_checked_at=data['last_checked'])

        # Upsert episodes
        for episode_id, ep_data in data.get('episodes', {}).items():
            self.db.upsert_episode(
                slug,
                episode_id,
                original_url=ep_data.get('original_url', ''),
                title=ep_data.get('title'),
                status=ep_data.get('status', 'pending'),
                processed_file=ep_data.get('processed_file'),
                processed_at=ep_data.get('processed_at') or ep_data.get('failed_at'),
                original_duration=ep_data.get('original_duration'),
                new_duration=ep_data.get('new_duration'),
                ads_removed=ep_data.get('ads_removed', 0),
                error_message=ep_data.get('error')
            )

        logger.debug(f"[{slug}] Saved data to database")

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
                          version: Optional[int] = None) -> Path:
        """Get path for an episode audio file.

        version=None or 0 -> ``{episode_id}{extension}`` (unversioned, back-compat).
        version>=1        -> ``{episode_id}-v{N}{extension}`` (incremented per reprocess).
        """
        return self._validated_episode_leaf(
            slug, episode_id, episode_filename(episode_id, version, extension)
        )

    def iter_episode_audio_paths(self, slug: str, episode_id: str,
                                   extension: str = ".mp3") -> List[Path]:
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

    def get_rss(self, slug: str) -> Optional[str]:
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

    def get_transcript(self, slug: str, episode_id: str) -> Optional[str]:
        """Get episode transcript from database."""
        episode = self.db.get_episode(slug, episode_id)
        if episode and episode.get('transcript_text'):
            return episode['transcript_text']
        return None

    def save_original_transcript(self, slug: str, episode_id: str, transcript: str) -> None:
        """Save original (pre-cut) transcript to database. Write-once."""
        self.db.save_original_transcript(slug, episode_id, transcript)

    # ========== VTT Transcript Methods (Podcasting 2.0) ==========

    def save_transcript_vtt(self, slug: str, episode_id: str, vtt_content: str) -> None:
        """Save VTT transcript to database."""
        try:
            self.db.save_episode_details(slug, episode_id, transcript_vtt=vtt_content)
            logger.debug(f"[{slug}:{episode_id}] Saved VTT transcript to database")
        except ValueError:
            logger.warning(f"[{slug}:{episode_id}] Episode not in DB, VTT not saved")

    def get_transcript_vtt(self, slug: str, episode_id: str) -> Optional[str]:
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

    def save_chapters_json(self, slug: str, episode_id: str, chapters: Dict) -> None:
        """Save chapters JSON to database."""
        try:
            chapters_str = json.dumps(chapters)
            self.db.save_episode_details(slug, episode_id, chapters_json=chapters_str)
            logger.debug(f"[{slug}:{episode_id}] Saved chapters JSON to database")
        except ValueError:
            logger.warning(f"[{slug}:{episode_id}] Episode not in DB, chapters not saved")

    def get_chapters_json(self, slug: str, episode_id: str) -> Optional[Dict]:
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

    def save_combined_ads(self, slug: str, episode_id: str, all_ads: List[Dict]) -> None:
        """Save combined ad markers from both passes to database."""
        try:
            self.db.save_episode_details(slug, episode_id, ad_markers=all_ads)
        except ValueError:
            logger.warning(f"[{slug}:{episode_id}] Episode not in DB, combined ads not saved")

        logger.debug(f"[{slug}:{episode_id}] Saved {len(all_ads)} combined ad markers")

    def save_verification_data(self, slug: str, episode_id: str,
                               verification_prompt: str = None,
                               verification_response: str = None) -> None:
        """Save verification pass detection data to database."""
        try:
            self.db.save_episode_details(
                slug, episode_id,
                second_pass_prompt=verification_prompt,
                second_pass_response=verification_response
            )
        except ValueError:
            logger.warning(f"[{slug}:{episode_id}] Episode not in DB, verification data not saved")

        logger.debug(f"[{slug}:{episode_id}] Saved verification detection data")


    # ========== Artwork Methods ==========

    def save_artwork(self, slug: str, image_data: bytes, content_type: str,
                    source_url: str = None) -> bool:
        """Save podcast artwork to filesystem."""
        try:
            podcast_dir = self.get_podcast_dir(slug)

            extension_by_type = {
                'image/png': '.png',
                'image/gif': '.gif',
                'image/webp': '.webp',
                'image/jpeg': '.jpg',
                'image/jpg': '.jpg',
            }
            ext = extension_by_type.get(content_type.lower(), '.jpg')

            artwork_path = podcast_dir / f"artwork{ext}"

            for old_ext in ('.jpg', '.png', '.gif', '.webp'):
                old_path = podcast_dir / f"artwork{old_ext}"
                if old_path.exists() and old_path != artwork_path:
                    old_path.unlink()

            # Save image
            with open(artwork_path, 'wb') as f:
                f.write(image_data)

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

    def get_artwork(self, slug: str) -> Optional[Tuple[bytes, str]]:
        """Get cached artwork. Returns (data, content_type) or None."""
        podcast_dir = self.get_podcast_dir(slug)

        for ext, content_type in [
            ('.jpg', 'image/jpeg'),
            ('.png', 'image/png'),
            ('.gif', 'image/gif'),
            ('.webp', 'image/webp'),
        ]:
            artwork_path = podcast_dir / f"artwork{ext}"
            if artwork_path.exists():
                with open(artwork_path, 'rb') as f:
                    return f.read(), content_type

        return None

    def download_artwork(self, slug: str, artwork_url: str) -> bool:
        """Download and cache podcast artwork.

        Content-Type header is advisory only; the saved bytes are validated
        against a fixed file-magic allowlist (JPEG/PNG/GIF/WebP). SVG is
        excluded because it admits script execution. Oversize responses are
        rejected outright with a structured log rather than saved partially.
        """
        if not artwork_url:
            return False

        try:
            # Check if we already have this artwork on disk
            podcast = self.db.get_podcast_by_slug(slug)
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

    def delete_processed_file(self, slug: str, episode_id: str) -> bool:
        """Delete the processed audio file(s) and any retained original."""
        deleted = False
        candidates = list(self.iter_episode_audio_paths(slug, episode_id, ".mp3"))
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
        podcast_dir = self.podcasts_dir / slug

        if podcast_dir.exists():
            try:
                shutil.rmtree(podcast_dir)
                logger.info(f"[{slug}] Deleted podcast directory")
                return True
            except Exception as e:
                logger.error(f"[{slug}] Failed to delete directory: {e}")
                return False

        return True

    def get_storage_stats(self) -> Dict[str, Any]:
        """Get storage statistics."""
        total_size = 0
        file_count = 0

        for podcast_dir in self.podcasts_dir.iterdir():
            if podcast_dir.is_dir():
                for f in podcast_dir.rglob('*'):
                    if f.is_file():
                        total_size += f.stat().st_size
                        file_count += 1

        return {
            'total_size_bytes': total_size,
            'total_size_mb': total_size / (1024 * 1024),
            'file_count': file_count
        }
