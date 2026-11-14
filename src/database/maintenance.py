"""Maintenance and cleanup mixin for MinusPod database."""
import json
import logging
import re
import sqlite3
import time
from datetime import timedelta
from pathlib import Path

from config import CORRECTION_ORIGIN_USER
from database.fingerprint_cleanup import delete_pattern_dependents, move_matches
from utils.text import extract_text_in_range
from utils.time import ISO_FORMAT, utc_now

logger = logging.getLogger(__name__)

# SQLite caps bound variables per statement, so IN clauses are chunked.
_SQL_VAR_CHUNK = 500
_TRUNCATE_BUSY_TIMEOUT_MS = 250


def _chunked(items, size: int = _SQL_VAR_CHUNK):
    """Yield successive slices small enough for one SQLite IN clause."""
    for i in range(0, len(items), size):
        yield items[i:i + size]


class MaintenanceMixin:
    """Database maintenance, cleanup, and deduplication methods."""

    def sqlite_diagnostics(self) -> dict:
        """Return operational SQLite state without exposing the database path."""
        from database import sqlite_metrics_snapshot

        conn = self.get_connection()
        pragma = {}
        for name in ('journal_mode', 'synchronous', 'busy_timeout', 'page_size',
                     'page_count', 'freelist_count', 'wal_autocheckpoint'):
            pragma[name] = conn.execute(f'PRAGMA {name}').fetchone()[0]
        base = str(self.db_path)
        sizes = {}
        for label, suffix in (('databaseBytes', ''), ('walBytes', '-wal'), ('shmBytes', '-shm')):
            try:
                sizes[label] = Path(base + suffix).stat().st_size
            except FileNotFoundError:
                sizes[label] = 0
        return {
            'journalMode': pragma['journal_mode'],
            'synchronous': pragma['synchronous'],
            'busyTimeoutMs': pragma['busy_timeout'],
            'pageSizeBytes': pragma['page_size'],
            'pageCount': pragma['page_count'],
            'freelistPages': pragma['freelist_count'],
            'walAutocheckpointPages': pragma['wal_autocheckpoint'],
            **sizes,
            'instrumentation': sqlite_metrics_snapshot(),
        }

    def checkpoint_wal(self, mode: str = 'passive') -> dict:
        """Run a WAL checkpoint (passive, or truncate) and return SQLite's result; other modes raise ValueError."""
        if mode not in ('passive', 'truncate'):
            raise ValueError(f'unknown checkpoint mode: {mode!r}')
        conn = self.get_connection()
        started = time.monotonic()
        previous_timeout = None
        try:
            if mode == 'truncate':
                # A blocked truncate must give up fast, not hold writers for the full busy timeout.
                previous_timeout = conn.execute('PRAGMA busy_timeout').fetchone()[0]
                conn.execute(f'PRAGMA busy_timeout = {_TRUNCATE_BUSY_TIMEOUT_MS}')
            busy, log_pages, checkpointed_pages = conn.execute(
                f'PRAGMA wal_checkpoint({mode.upper()})').fetchone()
        finally:
            if previous_timeout is not None:
                try:
                    conn.execute(f'PRAGMA busy_timeout = {previous_timeout}')
                except sqlite3.Error as exc:
                    logger.warning("Could not restore busy_timeout after checkpoint: %s", exc)
        return {
            'mode': mode,
            'busy': bool(busy),
            'logPages': log_pages,
            'checkpointedPages': checkpointed_pages,
            'durationMs': round((time.monotonic() - started) * 1000, 2),
        }

    def vacuum(self) -> int:
        """Run SQLite VACUUM to reclaim disk space and compact WAL; returns duration in ms."""
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

    @staticmethod
    def _retention_cutoff_str(days: int) -> str:
        """ISO-8601 UTC string for `days` ago. Used by both the original-only
        pre-pass and the main processed-file pass below."""
        return (utc_now() - timedelta(days=days)).strftime(ISO_FORMAT)

    def _retention_groups(self, conn) -> tuple[dict[int, list[str]], dict[str, bool]]:
        """(resolved retention window -> slugs sharing it, slug -> retains originals), from one query over podcasts.

        Feeds cluster into few distinct windows, so the sweep runs one query per
        window, not per feed. Precedence uses the per-feed resolvers' helpers so the
        two paths cannot drift. The keep-original map rides along to avoid a podcast
        aggregate per slug on every cleanup tick.
        """
        from database.podcasts import (
            effective_keep_original, effective_retention_days)
        try:
            global_days = int(self.get_setting('retention_days') or '30')
        except (TypeError, ValueError):
            global_days = 30
        global_keep = (self.get_setting('keep_original_audio') or 'true').lower() != 'false'
        groups: dict[int, list[str]] = {}
        keep_by_slug: dict[str, bool] = {}
        rows = conn.execute(
            "SELECT slug, feed_type, retention_days_override, "
            "keep_original_audio_override FROM podcasts").fetchall()
        for row in rows:
            if row['feed_type'] == 'local':
                # Local feeds: the retained original is the only copy; never sweep.
                continue
            days = effective_retention_days(
                row['retention_days_override'], global_days)
            groups.setdefault(days, []).append(row['slug'])
            keep_by_slug[row['slug']] = effective_keep_original(
                row['keep_original_audio_override'], global_keep)
        return groups, keep_by_slug

    def _resolve_original_retention(self, retention_days: int):
        """Return original_retention_days if the pre-pass should run, else None.

        The pre-pass matters only when a smaller original window is set; every
        other shape collapses to the main pass. Whether originals exist is
        per-feed, so callers filter slugs by resolve_keep_original_audio first.
        """
        raw = self.get_setting('original_retention_days')
        if not raw:
            return None
        try:
            days = int(raw)
        except (TypeError, ValueError):
            return None
        if days <= 0 or days >= retention_days:
            return None
        return days

    def _cleanup_originals_only(self, conn, slugs: list[str],
                                retention_days: int, storage) -> tuple[int, float]:
        """Drop the retained original for episodes past their original retention but within the main one.

        `slugs` are feeds sharing this window that also retain originals.
        Returns (count dropped, MB freed).
        """
        if not slugs:
            return 0, 0.0
        original_days = self._resolve_original_retention(retention_days)
        if original_days is None:
            return 0, 0.0

        original_cutoff = self._retention_cutoff_str(original_days)
        processed_cutoff = self._retention_cutoff_str(retention_days)

        # Original past its window, processed file still inside it; the
        # processed_cutoff bound skips rows the main pass is about to reset.
        rows = []
        for chunk in _chunked(slugs):
            placeholders = ','.join('?' * len(chunk))
            rows.extend(conn.execute(
                f"""SELECT e.id, e.episode_id, p.slug
                   FROM episodes e
                   JOIN podcasts p ON e.podcast_id = p.id
                   WHERE e.processed_file IS NOT NULL
                     AND COALESCE(e.processed_at, e.updated_at) < ?
                     AND COALESCE(e.processed_at, e.updated_at) >= ?
                     AND e.status = 'processed'
                     AND p.slug IN ({placeholders})""",  # noqa: S608
                (original_cutoff, processed_cutoff, *chunk),
            ).fetchall())

        # Note: no original_file IS NOT NULL predicate, since episodes processed
        # before that column existed can have an original on disk with a NULL column.
        dropped = 0
        freed_bytes = 0
        cleared_ids = []
        for row in rows:
            ok, size = storage.delete_original_only(row['slug'], row['episode_id'])
            if ok:
                dropped += 1
                freed_bytes += size
            # Clear the column when we deleted the file OR it was already
            # gone (rows left stale by pre-2.52.0 sweeps); keep it only
            # when the file still exists (unlink failed, retry next run).
            if ok or not storage.get_original_path(
                    row['slug'], row['episode_id']).exists():
                cleared_ids.append(row['id'])

        if cleared_ids:
            # Clear original_file so the Ad Review play button and original.mp3 route
            # don't point at a deleted file (#517). Chunked for SQLite's variable cap.
            for i in range(0, len(cleared_ids), 500):
                chunk = cleared_ids[i:i + 500]
                placeholders = ','.join('?' * len(chunk))
                conn.execute(
                    f"UPDATE episodes SET original_file = NULL WHERE id IN ({placeholders})",  # noqa: S608
                    chunk,
                )
            conn.commit()

        if dropped:
            freed_mb = freed_bytes / (1024 * 1024)
            logger.info(
                f"Retention cleanup: dropped {dropped} original audio file(s), "
                f"freed {freed_mb:.1f} MB (processed files kept)"
            )
        return dropped, freed_bytes / (1024 * 1024)

    def cleanup_old_episodes(self, force_all: bool = False, storage=None) -> tuple[int, float]:
        """Reset episodes with files older than retention_days back to 'discovered'.

        Deletes audio files and episode_details, never episode rows. force_all=True
        resets every episode with files regardless of age. Returns (count reset, MB freed).

        When `original_retention_days < retention_days`, an original-only sweep runs
        first, freeing just the retained pre-cut original of episodes past their
        original retention while the processed file stays.
        """
        if storage is None:
            raise ValueError("storage is required for cleanup_old_episodes")

        conn = self.get_connection()

        if not force_all:
            episodes_to_reset = []
            groups, keep_by_slug = self._retention_groups(conn)
            for retention_days, slugs in groups.items():
                # Archived feeds (explicit 0) and a globally disabled
                # retention both land here and are simply never swept.
                if retention_days <= 0:
                    continue

                # First pass: original-only deletion for a shorter pre-cut retention.
                # Gated up front since it is a no-op when original_retention_days is unset.
                if self._resolve_original_retention(retention_days) is not None:
                    self._cleanup_originals_only(
                        conn, [s for s in slugs if keep_by_slug.get(s)],
                        retention_days, storage)

                cutoff_str = self._retention_cutoff_str(retention_days)
                for chunk in _chunked(slugs):
                    placeholders = ','.join('?' * len(chunk))
                    episodes_to_reset.extend(conn.execute(
                        f"""SELECT e.episode_id, p.slug
                           FROM episodes e
                           JOIN podcasts p ON e.podcast_id = p.id
                           WHERE e.processed_file IS NOT NULL
                             AND COALESCE(e.processed_at, e.updated_at) < ?
                             AND e.status IN ('processed', 'failed', 'permanently_failed')
                             AND p.slug IN ({placeholders})""",  # noqa: S608
                        (cutoff_str, *chunk),
                    ).fetchall())
        else:
            # An explicit wipe still honours archive mode: retention_days_override of 0
            # is a deliberate "never delete this feed". Local feeds hold the only copy
            # of their audio, so they are exempt as in _retention_groups.
            episodes_to_reset = conn.execute(
                """SELECT e.episode_id, p.slug
                   FROM episodes e
                   JOIN podcasts p ON e.podcast_id = p.id
                   WHERE e.processed_file IS NOT NULL
                     AND e.status IN ('processed', 'failed', 'permanently_failed')
                     AND p.feed_type != 'local'
                     AND (p.retention_days_override IS NULL
                          OR p.retention_days_override > 0)"""
            ).fetchall()

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

    def deduplicate_patterns(self) -> int:
        """Remove duplicate patterns, merging stats into the highest-tier survivor.

        Duplicates share text_template and podcast_id, regardless of sponsor.
        Precedence: active over disabled (a switched-off row must not delete the
        live one), then user or community over auto-learned, then confirmation
        count. A duplicate's audio fingerprint and match history move to the
        survivor when it has none and the audio was not deleted.

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

            # Find the pattern to keep. Active first, or a switched-off row would
            # delete the live one and take its stats; then tier, then confirmations.
            patterns_cursor = conn.execute(
                f'''SELECT ap.id, ap.sponsor_id, ks.name AS sponsor,
                          ap.confirmation_count, ap.false_positive_count, ap.audio_removed,
                          COALESCE(ap.is_active, 1) AS is_active
                    FROM ad_patterns ap
                    LEFT JOIN known_sponsors ks ON ap.sponsor_id = ks.id
                    WHERE ap.id IN ({','.join('?' * len(all_ids))})
                    ORDER BY COALESCE(ap.is_active, 1) DESC,
                             (CASE WHEN ap.created_by = 'user' OR ap.source = 'community'
                                   THEN 0 ELSE 1 END),
                             ap.confirmation_count DESC,
                             ap.id ASC''',  # noqa: S608
                all_ids
            )
            patterns = patterns_cursor.fetchall()

            if len(patterns) < 2:
                continue

            # Keep the pattern the query ranked first (highest tier, then confirmations)
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
                    WHERE pattern_id IN ({placeholders})''',  # noqa: S608
                [keep_id] + remove_ids
            )

            # Duplicates share a text_template, so a loser's fingerprint describes
            # the keeper's audio too; promote one when the keeper has none.
            group_ids = remove_ids + [keep_id]
            fingerprinted = {row['pattern_id'] for row in conn.execute(
                'SELECT pattern_id FROM audio_fingerprints WHERE pattern_id IN '  # noqa: S608
                f"({','.join('?' * len(group_ids))})",
                group_ids
            )}
            # Preserve the keeper's inactive or audio-deleted state.
            donor = next((pid for pid in remove_ids if pid in fingerprinted), None)
            if (donor is not None and keep_id not in fingerprinted
                    and keep_pattern['is_active'] and not keep_pattern['audio_removed']):
                conn.execute(
                    'UPDATE audio_fingerprints SET pattern_id = ? WHERE pattern_id = ?',
                    [keep_id, donor]
                )
                move_matches(conn, donor, keep_id)

            # Drop the rest before their patterns go; the FK cascade would too,
            # but enforcement is per connection so do not lean on it.
            delete_pattern_dependents(conn, remove_ids)

            # Delete duplicate patterns
            conn.execute(
                f'''DELETE FROM ad_patterns WHERE id IN ({placeholders})''',  # noqa: S608
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

        Retroactively learns from confirmations submitted before pattern learning
        existed. Returns count of patterns created.
        """
        conn = self.get_connection()
        created_count = 0

        # Find all 'confirm' corrections without a pattern_id
        cursor = conn.execute('''
            SELECT pc.id, pc.episode_id, pc.original_bounds, pc.podcast_title,
                   pc.podcast_id
            FROM pattern_corrections pc
            WHERE pc.correction_type = 'confirm'
              AND pc.pattern_id IS NULL
              AND pc.origin = ?
        ''', (CORRECTION_ORIGIN_USER,))
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
                    WHERE e.podcast_id = ? AND e.episode_id = ?
                ''', (correction['podcast_id'], episode_id))
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
            # Require the canonical sponsor name, not just an alias, as a whole word.
            # Alias-only matches caused a 2.2.7 cascade relabeling unrelated
            # transcripts that merely contained a short alias.
            if not re.search(r'\b' + re.escape(sponsor) + r'\b',
                             pattern['text_template'], re.IGNORECASE):
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
