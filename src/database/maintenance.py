"""Maintenance and cleanup mixin for MinusPod database."""
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Tuple

from utils.text import extract_text_in_range

logger = logging.getLogger(__name__)


class MaintenanceMixin:
    """Database maintenance, cleanup, and deduplication methods."""

    def vacuum(self) -> int:
        """Run SQLITE VACUUM to reclaim disk space and compact WAL.

        Returns duration in milliseconds.
        """
        start = time.time()
        conn = self.get_connection()
        # VACUUM cannot run inside a transaction
        old_isolation = conn.isolation_level
        conn.isolation_level = None
        try:
            conn.execute("VACUUM")
        finally:
            conn.isolation_level = old_isolation
        duration_ms = int((time.time() - start) * 1000)
        logger.info(f"VACUUM completed in {duration_ms}ms")
        return duration_ms

    def cleanup_old_episodes(self, force_all: bool = False, storage=None) -> Tuple[int, float]:
        """Reset episodes with files older than retention_days back to 'discovered'.

        Deletes audio files and episode_details. Never deletes episode rows.
        force_all=True resets ALL episodes with files regardless of age.
        Returns (count reset, MB freed).
        """
        if storage is None:
            raise ValueError("storage is required for cleanup_old_episodes")

        conn = self.get_connection()

        if not force_all:
            retention_days = int(self.get_setting('retention_days') or '30')
            if retention_days <= 0:
                return 0, 0.0

            cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
            cutoff_str = cutoff.strftime('%Y-%m-%dT%H:%M:%SZ')

            cursor = conn.execute(
                """SELECT e.episode_id, p.slug
                   FROM episodes e
                   JOIN podcasts p ON e.podcast_id = p.id
                   WHERE e.processed_file IS NOT NULL
                     AND e.processed_at < ?
                     AND e.status IN ('processed', 'failed', 'permanently_failed')""",
                (cutoff_str,)
            )
        else:
            cursor = conn.execute(
                """SELECT e.episode_id, p.slug
                   FROM episodes e
                   JOIN podcasts p ON e.podcast_id = p.id
                   WHERE e.processed_file IS NOT NULL
                     AND e.status IN ('processed', 'failed', 'permanently_failed')"""
            )

        episodes_to_reset = cursor.fetchall()
        if not episodes_to_reset:
            return 0, 0.0

        # Group by slug for batch processing
        by_slug = {}
        for row in episodes_to_reset:
            by_slug.setdefault(row['slug'], []).append(row['episode_id'])

        total_reset = 0
        total_freed_mb = 0.0

        for slug, episode_ids in by_slug.items():
            reset, freed = self.delete_episodes(slug, episode_ids, storage)
            total_reset += reset
            total_freed_mb += freed

        if total_reset > 0:
            logger.info(f"Retention cleanup: reset {total_reset} episodes to discovered, freed {total_freed_mb:.1f} MB")

        return total_reset, total_freed_mb

    def delete_old_episodes(self, cutoff_date: str) -> int:
        """Delete episodes older than cutoff date. Returns count deleted."""
        conn = self.get_connection()
        cursor = conn.execute(
            "DELETE FROM episodes WHERE created_at < ?", (cutoff_date,)
        )
        conn.commit()
        return cursor.rowcount

    def cleanup_duplicate_episodes(self, slug: str) -> int:
        """
        Remove duplicate episodes from a feed, keeping only the latest version.

        Duplicates are identified by matching title (case-insensitive) and
        created_at date. When duplicates exist, keeps the one with the most
        recent created_at timestamp.

        Args:
            slug: The podcast feed slug

        Returns:
            Number of duplicate episodes removed
        """
        podcast = self.get_podcast_by_slug(slug)
        podcast_id = podcast['id'] if podcast else None
        if not podcast_id:
            return 0

        conn = self.get_connection()

        # Find duplicate groups by title + date
        cursor = conn.execute("""
            SELECT LOWER(TRIM(title)) as norm_title,
                   DATE(created_at) as created_date,
                   GROUP_CONCAT(episode_id) as episode_ids,
                   COUNT(*) as cnt
            FROM episodes
            WHERE podcast_id = ?
            GROUP BY norm_title, created_date
            HAVING cnt > 1
        """, (podcast_id,))

        duplicates = cursor.fetchall()
        removed = 0

        for row in duplicates:
            episode_ids = row['episode_ids'].split(',')

            # Get full details to find the latest one
            placeholders = ','.join(['?'] * len(episode_ids))
            detail_cursor = conn.execute(f"""
                SELECT episode_id, created_at
                FROM episodes
                WHERE podcast_id = ? AND episode_id IN ({placeholders})
                ORDER BY created_at DESC
            """, [podcast_id] + episode_ids)

            details = detail_cursor.fetchall()

            # Keep the first (most recent), delete the rest
            for old_ep in details[1:]:
                old_id = old_ep['episode_id']
                conn.execute(
                    "DELETE FROM episodes WHERE podcast_id = ? AND episode_id = ?",
                    (podcast_id, old_id)
                )
                removed += 1
                logger.info(f"Removed duplicate episode {old_id} from {slug}")

        if removed > 0:
            conn.commit()
            logger.info(f"Cleaned up {removed} duplicate episodes from {slug}")

        return removed

    def deduplicate_patterns(self) -> int:
        """Remove duplicate patterns, merging stats into the pattern with most confirmations.

        Duplicates are patterns with the same text_template and podcast_id,
        regardless of sponsor (sponsor variations are merged together).

        Returns count of duplicates removed."""
        conn = self.get_connection()

        # Find duplicates - patterns with same text_template and podcast_id
        # This includes patterns with same text but different sponsors
        cursor = conn.execute('''
            SELECT text_template, podcast_id, GROUP_CONCAT(id) as all_ids
            FROM ad_patterns
            WHERE text_template IS NOT NULL
            GROUP BY text_template, podcast_id
            HAVING COUNT(*) > 1
        ''')
        duplicates = cursor.fetchall()

        removed_count = 0
        for dup in duplicates:
            all_ids = [int(x) for x in dup['all_ids'].split(',')]

            # Find the pattern with most confirmations to keep
            patterns_cursor = conn.execute(
                f'''SELECT ap.id, ap.sponsor_id, ks.name AS sponsor,
                          ap.confirmation_count, ap.false_positive_count
                    FROM ad_patterns ap
                    LEFT JOIN known_sponsors ks ON ap.sponsor_id = ks.id
                    WHERE ap.id IN ({','.join('?' * len(all_ids))})
                    ORDER BY ap.confirmation_count DESC, ap.id ASC''',
                all_ids
            )
            patterns = patterns_cursor.fetchall()

            if len(patterns) < 2:
                continue

            # Keep the pattern with most confirmations (first one after sort)
            keep_pattern = patterns[0]
            keep_id = keep_pattern['id']
            remove_ids = [p['id'] for p in patterns[1:]]

            # Sum up all confirmation and false positive counts
            total_confirmations = sum(p['confirmation_count'] for p in patterns)
            total_false_positives = sum(p['false_positive_count'] for p in patterns)

            # If the keeper has no sponsor, try to use one from duplicates
            final_sponsor_id = keep_pattern['sponsor_id']
            if final_sponsor_id is None:
                for p in patterns[1:]:
                    if p['sponsor_id']:
                        final_sponsor_id = p['sponsor_id']
                        break

            # Update the kept pattern with merged stats
            conn.execute(
                '''UPDATE ad_patterns
                   SET confirmation_count = ?, false_positive_count = ?, sponsor_id = ?
                   WHERE id = ?''',
                [total_confirmations, total_false_positives, final_sponsor_id, keep_id]
            )

            # Update corrections to point to the kept pattern
            placeholders = ','.join('?' * len(remove_ids))
            conn.execute(
                f'''UPDATE pattern_corrections
                    SET pattern_id = ?
                    WHERE pattern_id IN ({placeholders})''',
                [keep_id] + remove_ids
            )

            # Delete duplicate patterns
            conn.execute(
                f'''DELETE FROM ad_patterns WHERE id IN ({placeholders})''',
                remove_ids
            )
            removed_count += len(remove_ids)
            logger.info(f"Merged {len(remove_ids)} duplicate patterns into pattern {keep_id} "
                       f"(confirmations: {total_confirmations}, fps: {total_false_positives})")

        conn.commit()
        if removed_count > 0:
            logger.info(f"Deduplicated {removed_count} patterns total")
        return removed_count

    def backfill_patterns_from_corrections(self) -> int:
        """Create patterns from existing 'confirm' corrections that have no pattern_id.

        This retroactively learns from user confirmations that were submitted
        before the pattern learning feature existed.
        Returns count of patterns created.

        Uses utils.time.parse_timestamp and utils.text.extract_text_in_range.
        """
        conn = self.get_connection()
        created_count = 0

        # Find all 'confirm' corrections without a pattern_id
        cursor = conn.execute('''
            SELECT pc.id, pc.episode_id, pc.original_bounds, pc.podcast_title
            FROM pattern_corrections pc
            WHERE pc.correction_type = 'confirm'
              AND pc.pattern_id IS NULL
        ''')
        corrections = cursor.fetchall()

        for correction in corrections:
            correction_id = correction['id']
            episode_id = correction['episode_id']
            original_bounds = correction['original_bounds']

            if not episode_id or not original_bounds:
                continue

            try:
                bounds = json.loads(original_bounds)
                start = bounds.get('start')
                end = bounds.get('end')
                if start is None or end is None:
                    continue

                # Get episode with transcript - need to find by episode_id
                # episode_id in corrections is the episode GUID, not slug
                cursor2 = conn.execute('''
                    SELECT e.*, p.id as podcast_db_id, p.slug, ed.transcript_text
                    FROM episodes e
                    JOIN podcasts p ON e.podcast_id = p.id
                    LEFT JOIN episode_details ed ON e.id = ed.episode_id
                    WHERE e.episode_id = ?
                ''', (episode_id,))
                episode = cursor2.fetchone()

                if not episode:
                    continue

                transcript = episode['transcript_text'] or ''
                podcast_id = episode['podcast_db_id']

                # Extract ad text from transcript
                ad_text = extract_text_in_range(transcript, start, end)

                if ad_text and len(ad_text) >= 50:
                    # Check for existing pattern with same text (deduplication)
                    existing = conn.execute(
                        '''SELECT id FROM ad_patterns
                           WHERE text_template = ? AND podcast_id = ?''',
                        (ad_text, str(podcast_id))
                    ).fetchone()

                    if existing:
                        # Link correction to existing pattern instead of creating duplicate
                        conn.execute(
                            'UPDATE pattern_corrections SET pattern_id = ? WHERE id = ?',
                            (existing['id'], correction_id)
                        )
                        logger.info(f"Linked correction {correction_id} to existing pattern {existing['id']}")
                    else:
                        # Create new pattern
                        cursor3 = conn.execute(
                            '''INSERT INTO ad_patterns
                               (scope, text_template, podcast_id, intro_variants, outro_variants,
                                created_from_episode_id)
                               VALUES (?, ?, ?, ?, ?, ?)''',
                            ('podcast', ad_text, str(podcast_id),
                             json.dumps([ad_text[:200]] if len(ad_text) > 200 else [ad_text]),
                             json.dumps([ad_text[-150:]] if len(ad_text) > 150 else []),
                             episode_id)
                        )
                        new_pattern_id = cursor3.lastrowid

                        # Update correction to link to new pattern
                        conn.execute(
                            'UPDATE pattern_corrections SET pattern_id = ? WHERE id = ?',
                            (new_pattern_id, correction_id)
                        )
                        created_count += 1
                        logger.info(f"Created pattern {new_pattern_id} from correction {correction_id}")

            except (json.JSONDecodeError, KeyError, TypeError) as e:
                logger.warning(f"Failed to process correction {correction_id}: {e}")
                continue

        conn.commit()
        if created_count > 0:
            logger.info(f"Backfilled {created_count} patterns from corrections")
        return created_count

    def extract_sponsors_for_patterns(self) -> int:
        """Extract sponsor names for patterns that have text_template but no sponsor.

        Returns count of patterns updated."""
        from sponsor_service import SponsorService
        from sponsor_normalize import get_or_create_known_sponsor

        conn = self.get_connection()
        updated_count = 0

        # Find patterns without sponsors
        cursor = conn.execute('''
            SELECT id, text_template FROM ad_patterns
            WHERE sponsor_id IS NULL AND text_template IS NOT NULL
        ''')
        patterns = cursor.fetchall()

        for pattern in patterns:
            sponsor = SponsorService.extract_sponsor_from_text(pattern['text_template'])
            if not sponsor:
                continue
            sponsor_id = get_or_create_known_sponsor(self, sponsor)
            if sponsor_id is None:
                continue
            conn.execute(
                'UPDATE ad_patterns SET sponsor_id = ? WHERE id = ?',
                (sponsor_id, pattern['id'])
            )
            updated_count += 1
            logger.info(f"Extracted sponsor '{sponsor}' for pattern {pattern['id']}")

        conn.commit()
        if updated_count > 0:
            logger.info(f"Extracted sponsors for {updated_count} patterns")
        return updated_count
