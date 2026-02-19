"""Storage management with SQLite database and file operations."""
import json
import logging
import requests
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
import tempfile
import shutil

from config import BROWSER_USER_AGENT

logger = logging.getLogger(__name__)


class Storage:
    """Storage manager using SQLite for metadata and filesystem for large files."""

    def __init__(self, data_dir: str = "/app/data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)

        # Create podcasts subdirectory
        self.podcasts_dir = self.data_dir / "podcasts"
        self.podcasts_dir.mkdir(exist_ok=True)

        # Initialize database
        from database import Database
        self.db = Database(str(self.data_dir))

        logger.info(f"Storage initialized with data_dir: {self.data_dir}")

    def get_podcast_dir(self, slug: str) -> Path:
        """Get podcast directory, creating if necessary."""
        podcast_dir = self.podcasts_dir / slug
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

    def get_episode_path(self, slug: str, episode_id: str, extension: str = ".mp3") -> Path:
        """Get path for episode file."""
        podcast_dir = self.get_podcast_dir(slug)
        return podcast_dir / "episodes" / f"{episode_id}{extension}"

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

            # Determine extension from content type
            if 'png' in content_type.lower():
                ext = '.png'
            elif 'gif' in content_type.lower():
                ext = '.gif'
            else:
                ext = '.jpg'

            artwork_path = podcast_dir / f"artwork{ext}"

            # Remove old artwork files with different extensions
            for old_ext in ['.jpg', '.png', '.gif']:
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

        for ext, content_type in [('.jpg', 'image/jpeg'),
                                   ('.png', 'image/png'),
                                   ('.gif', 'image/gif')]:
            artwork_path = podcast_dir / f"artwork{ext}"
            if artwork_path.exists():
                with open(artwork_path, 'rb') as f:
                    return f.read(), content_type

        return None

    def download_artwork(self, slug: str, artwork_url: str) -> bool:
        """Download and cache podcast artwork."""
        if not artwork_url:
            return False

        try:
            # Check if we already have this artwork
            podcast = self.db.get_podcast_by_slug(slug)
            if podcast and podcast.get('artwork_url') == artwork_url and podcast.get('artwork_cached'):
                logger.debug(f"[{slug}] Artwork already cached")
                return True

            logger.info(f"[{slug}] Downloading artwork from {artwork_url}")

            headers = {
                'User-Agent': BROWSER_USER_AGENT,
                'Accept': '*/*',
                'Accept-Language': 'en-US,en;q=0.9',
            }
            response = requests.get(artwork_url, headers=headers, timeout=30, stream=True)
            response.raise_for_status()

            content_type = response.headers.get('Content-Type', 'image/jpeg')

            # Limit size to 5MB
            max_size = 5 * 1024 * 1024
            image_data = b''
            for chunk in response.iter_content(chunk_size=8192):
                image_data += chunk
                if len(image_data) > max_size:
                    logger.warning(f"[{slug}] Artwork too large, truncating")
                    break

            return self.save_artwork(slug, image_data, content_type, artwork_url)

        except Exception as e:
            logger.warning(f"[{slug}] Failed to download artwork: {e}")
            return False

    # ========== Cleanup Methods ==========

    def delete_processed_file(self, slug: str, episode_id: str) -> bool:
        """Delete the processed audio file for an episode."""
        processed_path = self.get_episode_path(slug, episode_id, ".mp3")
        if processed_path and processed_path.exists():
            processed_path.unlink()
            logger.debug(f"[{slug}:{episode_id}] Deleted processed audio file")
            return True
        return False


    def cleanup_episode_files(self, slug: str, episode_id: str) -> int:
        """Delete all files for an episode. Returns bytes freed.

        Note: VTT and chapters are now stored in database, not files.
        Database cascade delete handles episode_details when episode is deleted.
        """
        freed = 0

        # Only delete MP3 file - VTT and chapters are now in database
        mp3_path = self.get_episode_path(slug, episode_id, '.mp3')
        if mp3_path.exists():
            try:
                freed += mp3_path.stat().st_size
                mp3_path.unlink()
            except Exception as e:
                logger.warning(f"Failed to delete {mp3_path}: {e}")

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
