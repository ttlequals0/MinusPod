"""Episode CRUD mixin for MinusPod database."""
import json
import logging
from email.utils import parsedate_to_datetime
from typing import Optional, Dict, List, Tuple

logger = logging.getLogger(__name__)


class EpisodeMixin:
    """Episode management methods."""

    VALID_SORT_COLUMNS = {'published_at', 'created_at', 'episode_number', 'title', 'status'}

    def get_episodes(self, slug: str, status: str = None,
                     limit: int = 50, offset: int = 0,
                     sort_by: str = 'created_at', sort_dir: str = 'desc') -> Tuple[List[Dict], int]:
        """Get episodes for a podcast with pagination and sorting."""
        conn = self.get_connection()

        # Get podcast ID
        podcast = self.get_podcast_by_slug(slug)
        if not podcast:
            return [], 0

        podcast_id = podcast['id']

        # Build query
        where_clause = "WHERE e.podcast_id = ?"
        params = [podcast_id]

        if status and status != 'all':
            where_clause += " AND e.status = ?"
            params.append(status)

        # Get total count
        cursor = conn.execute(
            f"SELECT COUNT(*) FROM episodes e {where_clause}",
            params
        )
        total = cursor.fetchone()[0]

        # Build ORDER BY clause with whitelist validation
        sort_col = sort_by if sort_by in self.VALID_SORT_COLUMNS else 'created_at'
        sort_direction = 'ASC' if sort_dir == 'asc' else 'DESC'

        if sort_col == 'episode_number':
            order_clause = f"ORDER BY e.episode_number IS NULL, e.episode_number {sort_direction}"
        elif sort_col == 'published_at':
            order_clause = f"ORDER BY COALESCE(e.published_at, e.created_at) {sort_direction}"
        else:
            order_clause = f"ORDER BY e.{sort_col} {sort_direction}"

        # Get episodes
        params.extend([limit, offset])
        cursor = conn.execute(
            f"""SELECT e.* FROM episodes e
                {where_clause}
                {order_clause}
                LIMIT ? OFFSET ?""",
            params
        )

        episodes = [dict(row) for row in cursor.fetchall()]
        return episodes, total

    def get_episode(self, slug: str, episode_id: str) -> Optional[Dict]:
        """Get episode by slug and episode_id."""
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT e.*, p.slug, p.title AS podcast_title,
                      ed.transcript_text,
                      (ed.original_transcript_text IS NOT NULL) as has_original_transcript,
                      ed.transcript_vtt,
                      ed.chapters_json, ed.ad_markers_json,
                      ed.first_pass_response, ed.first_pass_prompt,
                      ed.second_pass_prompt, ed.second_pass_response
               FROM episodes e
               JOIN podcasts p ON e.podcast_id = p.id
               LEFT JOIN episode_details ed ON e.id = ed.episode_id
               WHERE p.slug = ? AND e.episode_id = ?""",
            (slug, episode_id)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_episode_by_id(self, db_id: int) -> Optional[Dict]:
        """Get episode by database ID."""
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT e.*, p.slug FROM episodes e
               JOIN podcasts p ON e.podcast_id = p.id
               WHERE e.id = ?""",
            (db_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_episode_by_title_and_date(self, slug: str, title: str, published_at: str) -> Optional[Dict]:
        """Get episode by title and publish date (for deduplication).

        This catches cases where the same episode has different IDs due to
        changing RSS GUIDs or dynamic URL parameters.

        Args:
            slug: Podcast slug
            title: Episode title (exact match)
            published_at: Publish date in ISO format

        Returns:
            Episode dict if found, None otherwise
        """
        if not title or not published_at:
            return None

        conn = self.get_connection()
        podcast = self.get_podcast_by_slug(slug)
        if not podcast:
            return None

        cursor = conn.execute(
            """SELECT e.*, p.slug FROM episodes e
               JOIN podcasts p ON e.podcast_id = p.id
               WHERE p.slug = ? AND e.title = ? AND e.published_at = ?""",
            (slug, title, published_at)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def upsert_episode(self, slug: str, episode_id: str, **kwargs) -> int:
        """Insert or update an episode. Returns episode database ID."""
        conn = self.get_connection()

        # Get podcast ID
        podcast = self.get_podcast_by_slug(slug)
        if not podcast:
            raise ValueError(f"Podcast not found: {slug}")

        podcast_id = podcast['id']

        # Check if episode exists
        cursor = conn.execute(
            "SELECT id FROM episodes WHERE podcast_id = ? AND episode_id = ?",
            (podcast_id, episode_id)
        )
        row = cursor.fetchone()

        if row:
            # Update existing episode
            db_id = row['id']
            if kwargs:
                fields = []
                values = []
                for key, value in kwargs.items():
                    if key in ('original_url', 'title', 'description', 'status', 'processed_file',
                               'processed_at', 'original_duration', 'new_duration',
                               'ads_removed', 'ads_removed_firstpass', 'ads_removed_secondpass',
                               'error_message', 'ad_detection_status', 'artwork_url',
                               'reprocess_mode', 'reprocess_requested_at', 'retry_count',
                               'published_at', 'episode_number'):
                        fields.append(f"{key} = ?")
                        values.append(value)

                if fields:
                    fields.append("updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')")
                    values.append(db_id)
                    conn.execute(
                        f"UPDATE episodes SET {', '.join(fields)} WHERE id = ?",
                        values
                    )
                    conn.commit()
        else:
            # Insert new episode
            cursor = conn.execute(
                """INSERT INTO episodes
                   (podcast_id, episode_id, original_url, title, description, status,
                    processed_file, processed_at, original_duration,
                    new_duration, ads_removed, ads_removed_firstpass, ads_removed_secondpass,
                    error_message, ad_detection_status, artwork_url, episode_number,
                    retry_count, published_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    podcast_id,
                    episode_id,
                    kwargs.get('original_url', ''),
                    kwargs.get('title'),
                    kwargs.get('description'),
                    kwargs.get('status', 'pending'),
                    kwargs.get('processed_file'),
                    kwargs.get('processed_at'),
                    kwargs.get('original_duration'),
                    kwargs.get('new_duration'),
                    kwargs.get('ads_removed', 0),
                    kwargs.get('ads_removed_firstpass', 0),
                    kwargs.get('ads_removed_secondpass', 0),
                    kwargs.get('error_message'),
                    kwargs.get('ad_detection_status'),
                    kwargs.get('artwork_url'),
                    kwargs.get('episode_number'),
                    kwargs.get('retry_count', 0),
                    kwargs.get('published_at')
                )
            )
            db_id = cursor.lastrowid
            conn.commit()

        return db_id

    def _get_episode_db_id(self, slug: str, episode_id: str) -> Optional[int]:
        """Lightweight lookup: resolve (slug, episode_id) to the episodes.id PK.

        Only joins episodes + podcasts (skips episode_details).
        Returns the integer PK, or None if not found.
        """
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT e.id FROM episodes e
               JOIN podcasts p ON e.podcast_id = p.id
               WHERE p.slug = ? AND e.episode_id = ?""",
            (slug, episode_id)
        )
        row = cursor.fetchone()
        return row['id'] if row else None

    def save_episode_details(self, slug: str, episode_id: str,
                            transcript_text: str = None,
                            transcript_vtt: str = None,
                            chapters_json: str = None,
                            ad_markers: List[Dict] = None,
                            first_pass_response: str = None,
                            first_pass_prompt: str = None,
                            second_pass_prompt: str = None,
                            second_pass_response: str = None):
        """Save or update episode details (transcript, VTT, chapters, ad markers, pass data)."""
        conn = self.get_connection()

        db_episode_id = self._get_episode_db_id(slug, episode_id)
        if not db_episode_id:
            raise ValueError(f"Episode not found: {slug}/{episode_id}")

        # Check if details exist
        cursor = conn.execute(
            "SELECT id FROM episode_details WHERE episode_id = ?",
            (db_episode_id,)
        )
        row = cursor.fetchone()

        ad_markers_json_str = json.dumps(ad_markers) if ad_markers is not None else None

        if row:
            # Update existing
            updates = []
            values = []
            if transcript_text is not None:
                updates.append("transcript_text = ?")
                values.append(transcript_text)
            if transcript_vtt is not None:
                updates.append("transcript_vtt = ?")
                values.append(transcript_vtt)
            if chapters_json is not None:
                updates.append("chapters_json = ?")
                values.append(chapters_json)
            if ad_markers_json_str is not None:
                updates.append("ad_markers_json = ?")
                values.append(ad_markers_json_str)
            if first_pass_response is not None:
                updates.append("first_pass_response = ?")
                values.append(first_pass_response)
            if first_pass_prompt is not None:
                updates.append("first_pass_prompt = ?")
                values.append(first_pass_prompt)
            if second_pass_prompt is not None:
                updates.append("second_pass_prompt = ?")
                values.append(second_pass_prompt)
            if second_pass_response is not None:
                updates.append("second_pass_response = ?")
                values.append(second_pass_response)

            if updates:
                values.append(row['id'])
                conn.execute(
                    f"UPDATE episode_details SET {', '.join(updates)} WHERE id = ?",
                    values
                )
        else:
            # Insert new
            conn.execute(
                """INSERT INTO episode_details
                   (episode_id, transcript_text, transcript_vtt, chapters_json,
                    ad_markers_json, first_pass_response, first_pass_prompt,
                    second_pass_prompt, second_pass_response)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (db_episode_id, transcript_text, transcript_vtt, chapters_json,
                 ad_markers_json_str, first_pass_response, first_pass_prompt,
                 second_pass_prompt, second_pass_response)
            )

        conn.commit()

    def save_original_transcript(self, slug: str, episode_id: str, transcript_text: str):
        """Save original (pre-cut) transcript. Write-once: never overwrites an existing value."""
        conn = self.get_connection()

        db_episode_id = self._get_episode_db_id(slug, episode_id)
        if not db_episode_id:
            logger.warning(f"Episode not found for original transcript: {slug}/{episode_id}")
            return

        # Atomic upsert with write-once guard: inserts if no row exists,
        # otherwise sets original_transcript_text only if still NULL.
        conn.execute(
            """INSERT INTO episode_details (episode_id, original_transcript_text)
               VALUES (?, ?)
               ON CONFLICT(episode_id) DO UPDATE
               SET original_transcript_text = COALESCE(
                   episode_details.original_transcript_text, excluded.original_transcript_text
               )""",
            (db_episode_id, transcript_text)
        )

        conn.commit()
        logger.debug(f"[{slug}:{episode_id}] Saved original transcript to database")

    def get_original_transcript(self, slug: str, episode_id: str) -> str:
        """Get original (pre-cut) transcript text, or None."""
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT ed.original_transcript_text FROM episode_details ed
               JOIN episodes e ON ed.episode_id = e.id
               JOIN podcasts p ON e.podcast_id = p.id
               WHERE p.slug = ? AND e.episode_id = ?""",
            (slug, episode_id)
        )
        row = cursor.fetchone()
        return row['original_transcript_text'] if row else None

    def save_episode_audio_analysis(self, slug: str, episode_id: str, audio_analysis_json: str):
        """Save audio analysis results for an episode."""
        conn = self.get_connection()

        db_episode_id = self._get_episode_db_id(slug, episode_id)
        if not db_episode_id:
            logger.warning(f"Episode not found for audio analysis: {slug}/{episode_id}")
            return

        # Check if details exist
        cursor = conn.execute(
            "SELECT id FROM episode_details WHERE episode_id = ?",
            (db_episode_id,)
        )
        row = cursor.fetchone()

        if row:
            # Update existing
            conn.execute(
                "UPDATE episode_details SET audio_analysis_json = ? WHERE id = ?",
                (audio_analysis_json, row['id'])
            )
        else:
            # Insert new
            conn.execute(
                """INSERT INTO episode_details (episode_id, audio_analysis_json)
                   VALUES (?, ?)""",
                (db_episode_id, audio_analysis_json)
            )

        conn.commit()
        logger.debug(f"[{slug}:{episode_id}] Saved audio analysis to database")

    def clear_episode_details(self, slug: str, episode_id: str):
        """Clear transcript and ad markers for an episode."""
        conn = self.get_connection()

        db_episode_id = self._get_episode_db_id(slug, episode_id)
        if not db_episode_id:
            return

        conn.execute(
            "DELETE FROM episode_details WHERE episode_id = ?",
            (db_episode_id,)
        )
        conn.commit()
        logger.debug(f"[{slug}:{episode_id}] Cleared episode details from database")

    def reset_episode_status(self, slug: str, episode_id: str):
        """Reset episode status to pending for reprocessing."""
        conn = self.get_connection()

        podcast = self.get_podcast_by_slug(slug)
        if not podcast:
            return

        conn.execute(
            """UPDATE episodes
               SET status = 'pending',
                   processed_file = NULL,
                   processed_at = NULL,
                   original_duration = NULL,
                   new_duration = NULL,
                   ads_removed = NULL,
                   error_message = NULL,
                   retry_count = 0,
                   updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
               WHERE podcast_id = ? AND episode_id = ?""",
            (podcast['id'], episode_id)
        )
        conn.commit()
        logger.debug(f"[{slug}:{episode_id}] Reset episode status to pending (retry_count reset)")

    def get_processed_episodes_for_feed(self, podcast_id: int) -> List[Dict]:
        """Get all processed episodes with files for inclusion in RSS feed."""
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT episode_id, title, description, published_at,
                      new_duration, episode_number, original_url
               FROM episodes
               WHERE podcast_id = ? AND status = 'processed'
                     AND processed_file IS NOT NULL
               ORDER BY published_at DESC""",
            (podcast_id,)
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_episodes_by_ids(self, slug: str, episode_ids: List[str]) -> List[Dict]:
        """Get multiple episodes by slug and episode_ids in a single query."""
        if not episode_ids:
            return []
        conn = self.get_connection()
        placeholders = ','.join('?' for _ in episode_ids)
        cursor = conn.execute(
            f"""SELECT e.*, p.slug
                FROM episodes e
                JOIN podcasts p ON e.podcast_id = p.id
                WHERE p.slug = ? AND e.episode_id IN ({placeholders})""",
            [slug] + list(episode_ids)
        )
        return [dict(row) for row in cursor.fetchall()]

    def batch_clear_episode_details(self, slug: str, episode_ids: List[str]) -> None:
        """Clear episode_details for multiple episodes in one query."""
        if not episode_ids:
            return
        episodes = self.get_episodes_by_ids(slug, episode_ids)
        if not episodes:
            return
        db_ids = [ep['id'] for ep in episodes]
        conn = self.get_connection()
        placeholders = ','.join('?' for _ in db_ids)
        conn.execute(
            f"DELETE FROM episode_details WHERE episode_id IN ({placeholders})",
            db_ids
        )
        conn.commit()

    def batch_reset_episodes_to_discovered(self, slug: str, episode_ids: List[str]) -> None:
        """Reset multiple episodes to discovered state in one query."""
        if not episode_ids:
            return
        conn = self.get_connection()
        podcast = self.get_podcast_by_slug(slug)
        if not podcast:
            return
        placeholders = ','.join('?' for _ in episode_ids)
        conn.execute(
            f"""UPDATE episodes SET
                status = 'discovered',
                processed_file = NULL, processed_at = NULL,
                original_duration = NULL, new_duration = NULL,
                ads_removed = 0, ads_removed_firstpass = 0, ads_removed_secondpass = 0,
                error_message = NULL, ad_detection_status = NULL,
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
            WHERE podcast_id = ? AND episode_id IN ({placeholders})""",
            [podcast['id']] + list(episode_ids)
        )
        conn.commit()

    def bulk_upsert_discovered_episodes(self, slug: str, episodes: List[Dict]) -> int:
        """Insert or update episodes as 'discovered'.

        On conflict, backfills empty title/description from new data but
        never overwrites an existing episode's status or non-empty metadata.
        Returns count of newly inserted rows.
        """
        conn = self.get_connection()

        podcast = self.get_podcast_by_slug(slug)
        if not podcast:
            logger.error(f"Cannot upsert discovered episodes: podcast not found: {slug}")
            return 0

        podcast_id = podcast['id']
        inserted = 0

        for ep in episodes:
            # Parse RFC2822 published date to ISO format
            iso_published = None
            published_str = ep.get('published', '')
            if published_str:
                try:
                    parsed_pub = parsedate_to_datetime(published_str)
                    iso_published = parsed_pub.strftime('%Y-%m-%dT%H:%M:%SZ')
                except (ValueError, TypeError):
                    pass

            # Check for existing episode with same title+date but different ID
            # Skip insert to prevent duplicate rows from GUID changes
            if ep.get('title') and iso_published:
                existing = conn.execute(
                    """SELECT episode_id, episode_number, status FROM episodes
                       WHERE podcast_id = ? AND title = ? AND published_at = ?
                       AND episode_id != ?""",
                    (podcast_id, ep.get('title'), iso_published, ep['id'])
                ).fetchone()
                if existing:
                    # Update episode_id to match new GUID for discovered episodes
                    # (no cached files yet, safe to update)
                    if existing['status'] == 'discovered':
                        conn.execute(
                            """UPDATE episodes SET episode_id = ?
                               WHERE podcast_id = ? AND episode_id = ?""",
                            (ep['id'], podcast_id, existing['episode_id'])
                        )
                    current_id = ep['id'] if existing['status'] == 'discovered' else existing['episode_id']
                    # Backfill episode_number on existing row if missing
                    if ep.get('episode_number') and not existing['episode_number']:
                        conn.execute(
                            """UPDATE episodes SET episode_number = ?
                               WHERE podcast_id = ? AND episode_id = ?
                               AND episode_number IS NULL""",
                            (ep.get('episode_number'), podcast_id, current_id)
                        )
                    continue  # Skip - episode already exists with different GUID

            try:
                cursor = conn.execute(
                    """INSERT INTO episodes
                       (podcast_id, episode_id, original_url, title, description,
                        artwork_url, episode_number, published_at, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'discovered')
                       ON CONFLICT(podcast_id, episode_id) DO UPDATE SET
                        episode_number = COALESCE(excluded.episode_number, episodes.episode_number),
                        published_at = COALESCE(episodes.published_at, excluded.published_at),
                        original_url = COALESCE(episodes.original_url, excluded.original_url),
                        title = CASE WHEN COALESCE(episodes.title, '') = '' THEN excluded.title ELSE episodes.title END,
                        description = CASE WHEN COALESCE(episodes.description, '') = '' THEN excluded.description ELSE episodes.description END,
                        artwork_url = COALESCE(episodes.artwork_url, excluded.artwork_url)""",
                    (
                        podcast_id,
                        ep['id'],
                        ep.get('url', ''),
                        ep.get('title'),
                        ep.get('description'),
                        ep.get('artwork_url'),
                        ep.get('episode_number'),
                        iso_published,
                    )
                )
                if cursor.rowcount > 0:
                    inserted += 1
            except Exception as e:
                logger.warning(f"Failed to upsert discovered episode {ep.get('id')}: {e}")

        conn.commit()
        return inserted

    def _reset_episode_to_discovered(self, slug: str, episode_id: str) -> None:
        """Clear episode_details and reset an episode back to 'discovered' state."""
        self.clear_episode_details(slug, episode_id)
        self.upsert_episode(
            slug, episode_id,
            status='discovered',
            processed_file=None,
            processed_at=None,
            original_duration=None,
            new_duration=None,
            ads_removed=0,
            ads_removed_firstpass=0,
            ads_removed_secondpass=0,
            error_message=None,
            ad_detection_status=None,
        )

    def batch_set_episodes_pending(self, slug: str, episode_ids: List[str],
                                    reprocess_mode: str = None,
                                    reprocess_requested_at: str = None) -> int:
        """Set multiple episodes to pending status in one query."""
        if not episode_ids:
            return 0
        conn = self.get_connection()
        podcast = self.get_podcast_by_slug(slug)
        if not podcast:
            return 0
        placeholders = ','.join('?' for _ in episode_ids)
        params = [reprocess_mode, reprocess_requested_at, podcast['id']] + list(episode_ids)
        cursor = conn.execute(
            f"""UPDATE episodes SET
                status = 'pending', retry_count = 0, error_message = NULL,
                reprocess_mode = ?, reprocess_requested_at = ?,
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
            WHERE podcast_id = ? AND episode_id IN ({placeholders})""",
            params
        )
        conn.commit()
        return cursor.rowcount

    def delete_episodes(self, slug: str, episode_ids: List[str], storage) -> Tuple[int, float]:
        """Delete audio files and reset episodes to 'discovered'.

        Does NOT delete DB rows. Does NOT touch processing_history.
        Returns (count reset, MB freed).
        """
        episodes = self.get_episodes_by_ids(slug, episode_ids)
        episodes_by_id = {ep['episode_id']: ep for ep in episodes}

        freed_bytes = 0
        ids_to_reset = []

        for episode_id in episode_ids:
            episode = episodes_by_id.get(episode_id)
            if not episode or not episode.get('processed_file'):
                continue

            freed_bytes += storage.cleanup_episode_files(slug, episode_id)
            ids_to_reset.append(episode_id)

        if ids_to_reset:
            self.batch_clear_episode_details(slug, ids_to_reset)
            self.batch_reset_episodes_to_discovered(slug, ids_to_reset)

        freed_mb = freed_bytes / (1024 * 1024)
        return len(ids_to_reset), freed_mb
