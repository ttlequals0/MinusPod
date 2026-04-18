"""Statistics and token usage mixin for MinusPod database."""
import logging
from typing import Dict, List, Optional, Tuple

from config import normalize_model_key

logger = logging.getLogger(__name__)


class StatsMixin:
    """Statistics, token usage, and processing history methods."""

    def get_stats(self) -> Dict:
        """Get database statistics."""
        conn = self.get_connection()

        # Podcast count
        cursor = conn.execute("SELECT COUNT(*) FROM podcasts")
        podcast_count = cursor.fetchone()[0]

        # Episode counts by status
        cursor = conn.execute("""
            SELECT status, COUNT(*) as count
            FROM episodes
            GROUP BY status
        """)
        status_counts = {row['status']: row['count'] for row in cursor}

        # Total episodes
        total_episodes = sum(status_counts.values())

        # Storage estimate (processed files)
        total_size = 0
        for podcast_dir in self.data_dir.iterdir():
            if podcast_dir.is_dir():
                episodes_dir = podcast_dir / "episodes"
                if episodes_dir.exists():
                    for f in episodes_dir.glob("*.mp3"):
                        total_size += f.stat().st_size

        return {
            'podcast_count': podcast_count,
            'episode_count': total_episodes,
            'episodes_by_status': status_counts,
            'storage_mb': total_size / (1024 * 1024)
        }

    def get_feeds_config(self) -> List[Dict]:
        """Get feed configuration in feeds.json format for compatibility."""
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT slug, source_url FROM podcasts WHERE source_url != ''"
        )
        return [
            {'in': row['source_url'], 'out': f"/{row['slug']}"}
            for row in cursor
        ]

    # ========== Cumulative Stats Methods ==========

    def increment_total_time_saved(self, seconds: float):
        """Add to the cumulative total time saved. Called when episode processing completes."""
        if seconds <= 0:
            return

        conn = self.get_connection()
        conn.execute(
            """INSERT INTO stats (key, value, updated_at)
               VALUES ('total_time_saved', ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
               ON CONFLICT(key) DO UPDATE SET
                 value = value + excluded.value,
                 updated_at = excluded.updated_at""",
            (seconds,)
        )
        conn.commit()
        logger.debug(f"Incremented total time saved by {seconds:.1f} seconds")

    def get_total_time_saved(self) -> float:
        """Get the cumulative total time saved across all processed episodes."""
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT value FROM stats WHERE key = 'total_time_saved'"
        )
        row = cursor.fetchone()
        return row['value'] if row else 0.0

    def get_stat(self, key: str) -> float:
        """Get a single cumulative stat value by key."""
        conn = self.get_connection()
        cursor = conn.execute("SELECT value FROM stats WHERE key = ?", (key,))
        row = cursor.fetchone()
        return row['value'] if row else 0.0

    # ========== Token Usage Methods ==========

    def _calculate_token_cost(self, conn, model_id: str,
                              input_tokens: int, output_tokens: int,
                              match_key: str = '') -> float:
        """Calculate cost using normalized match_key lookup.

        Resolution: exact match on match_key -> prefix match on match_key -> $0.
        """
        if not match_key:
            match_key = normalize_model_key(model_id)

        logger.debug(f"Cost lookup: model_id='{model_id}' -> match_key='{match_key}'")

        # Exact match on match_key
        cursor = conn.execute(
            "SELECT input_cost_per_mtok, output_cost_per_mtok "
            "FROM model_pricing WHERE match_key = ?",
            (match_key,)
        )
        row = cursor.fetchone()

        # Prefix match fallback -- match_key must cover >= 80% of lookup key length
        # to prevent short keys matching distinct longer models (e.g. 'gpt4o' must not match 'gpt4omini')
        if not row:
            cursor = conn.execute(
                """SELECT input_cost_per_mtok, output_cost_per_mtok
                   FROM model_pricing
                   WHERE ? LIKE match_key || '%'
                     AND length(match_key) >= length(?) * 0.8
                   ORDER BY length(match_key) DESC LIMIT 1""",
                (match_key, match_key)
            )
            row = cursor.fetchone()
            if row:
                logger.warning(f"Cost lookup: prefix match for match_key='{match_key}' "
                               f"-- verify pricing is correct")

        if not row:
            logger.warning(
                f"No pricing found for model '{model_id}' "
                f"(match_key='{match_key}'), cost recorded as $0"
            )
            return 0.0

        input_cost = (input_tokens / 1_000_000) * row['input_cost_per_mtok']
        output_cost = (output_tokens / 1_000_000) * row['output_cost_per_mtok']
        return input_cost + output_cost

    def record_token_usage(self, model_id: str, input_tokens: int, output_tokens: int) -> float:
        """Record token usage for an LLM call. Atomic upsert to per-model and global stats.
        Returns the calculated cost for this call."""
        if not model_id or (input_tokens <= 0 and output_tokens <= 0):
            return 0.0

        conn = self.get_connection()
        match_key = normalize_model_key(model_id)
        cost = self._calculate_token_cost(conn, model_id, input_tokens, output_tokens,
                                          match_key=match_key)

        # Upsert per-model token_usage row
        conn.execute(
            """INSERT INTO token_usage
                   (model_id, match_key, total_input_tokens, total_output_tokens,
                    total_cost, call_count, updated_at)
               VALUES (?, ?, ?, ?, ?, 1, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
               ON CONFLICT(model_id) DO UPDATE SET
                 match_key = excluded.match_key,
                 total_input_tokens = total_input_tokens + excluded.total_input_tokens,
                 total_output_tokens = total_output_tokens + excluded.total_output_tokens,
                 total_cost = total_cost + excluded.total_cost,
                 call_count = call_count + 1,
                 updated_at = excluded.updated_at""",
            (model_id, match_key, input_tokens, output_tokens, cost)
        )

        # Update global stats counters
        for stat_key, value in [('total_input_tokens', float(input_tokens)),
                                ('total_output_tokens', float(output_tokens)),
                                ('total_llm_cost', cost)]:
            conn.execute(
                """INSERT INTO stats (key, value, updated_at)
                   VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                   ON CONFLICT(key) DO UPDATE SET
                     value = value + excluded.value,
                     updated_at = excluded.updated_at""",
                (stat_key, value)
            )

        conn.commit()
        logger.debug(
            f"Token usage: model={model_id} match_key={match_key} "
            f"in={input_tokens} out={output_tokens} cost=${cost:.6f}"
        )
        return cost

    def get_token_usage_summary(self) -> Dict:
        """Get global totals and per-model breakdown of token usage."""
        conn = self.get_connection()

        # Global totals from stats table
        total_input = self.get_stat('total_input_tokens')
        total_output = self.get_stat('total_output_tokens')
        total_cost = self.get_stat('total_llm_cost')

        # Per-model breakdown with pricing info
        cursor = conn.execute(
            """SELECT tu.model_id, tu.total_input_tokens, tu.total_output_tokens,
                      tu.total_cost, tu.call_count,
                      mp.display_name, mp.input_cost_per_mtok, mp.output_cost_per_mtok
               FROM token_usage tu
               LEFT JOIN model_pricing mp ON tu.match_key = mp.match_key
               ORDER BY tu.total_cost DESC"""
        )

        models = []
        for row in cursor:
            models.append({
                'modelId': row['model_id'],
                'displayName': row['display_name'] or row['model_id'],
                'totalInputTokens': row['total_input_tokens'],
                'totalOutputTokens': row['total_output_tokens'],
                'totalCost': round(row['total_cost'], 6),
                'callCount': row['call_count'],
                'inputCostPerMtok': row['input_cost_per_mtok'],
                'outputCostPerMtok': row['output_cost_per_mtok'],
            })

        return {
            'totalInputTokens': int(total_input),
            'totalOutputTokens': int(total_output),
            'totalCost': round(total_cost, 6),
            'models': models,
        }

    def record_processing_history(self, podcast_id: int, podcast_slug: str,
                                   podcast_title: str, episode_id: str,
                                   episode_title: str, status: str,
                                   processing_duration_seconds: float = None,
                                   ads_detected: int = 0,
                                   error_message: str = None,
                                   input_tokens: int = 0,
                                   output_tokens: int = 0,
                                   llm_cost: float = 0.0) -> int:
        """Record a processing attempt in history. Returns history entry ID."""
        conn = self.get_connection()

        # Calculate reprocess number (count existing entries + 1)
        cursor = conn.execute(
            """SELECT COUNT(*) FROM processing_history
               WHERE podcast_id = ? AND episode_id = ?""",
            (podcast_id, episode_id)
        )
        existing_count = cursor.fetchone()[0]
        reprocess_number = existing_count + 1

        cursor = conn.execute(
            """INSERT INTO processing_history
               (podcast_id, podcast_slug, podcast_title, episode_id, episode_title,
                processed_at, processing_duration_seconds, status, ads_detected,
                error_message, reprocess_number, input_tokens, output_tokens, llm_cost)
               VALUES (?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), ?, ?, ?, ?, ?, ?, ?, ?)""",
            (podcast_id, podcast_slug, podcast_title, episode_id, episode_title,
             processing_duration_seconds, status, ads_detected, error_message,
             reprocess_number, input_tokens, output_tokens, llm_cost)
        )
        conn.commit()
        logger.info(f"Recorded processing history: {podcast_slug}/{episode_id} - {status} (reprocess #{reprocess_number})")
        return cursor.lastrowid

    def increment_episode_token_usage(self, episode_id: str,
                                       input_tokens: int,
                                       output_tokens: int,
                                       llm_cost: float) -> bool:
        """Increment token usage on the most recent completed processing_history entry.

        Used by standalone API endpoints (regenerate-chapters, retry-ad-detection)
        that make LLM calls outside the full processing pipeline.
        Returns True if a row was updated.
        """
        conn = self.get_connection()
        cursor = conn.execute(
            """UPDATE processing_history
               SET input_tokens = input_tokens + ?,
                   output_tokens = output_tokens + ?,
                   llm_cost = llm_cost + ?
               WHERE id = (
                   SELECT id FROM processing_history
                   WHERE episode_id = ? AND status = 'completed'
                   ORDER BY processed_at DESC LIMIT 1
               )""",
            (input_tokens, output_tokens, llm_cost, episode_id)
        )
        conn.commit()
        updated = cursor.rowcount > 0
        if updated:
            logger.info(f"Incremented token usage for episode {episode_id}: +{input_tokens} in, +{output_tokens} out, +${llm_cost:.6f}")
        else:
            logger.warning(f"No completed processing_history entry found for episode {episode_id} to increment tokens")
        return updated

    def backfill_processing_history(self) -> int:
        """Migrate existing processed episodes to processing_history table.
        Only backfills episodes that don't already have history entries.
        Returns count of records created."""
        conn = self.get_connection()

        # Only backfill episodes not already in history
        # Note: processed_at is often NULL in older records, so use updated_at as fallback
        cursor = conn.execute('''
            INSERT INTO processing_history
                (podcast_id, podcast_slug, podcast_title, episode_id, episode_title,
                 processed_at, processing_duration_seconds, status, ads_detected,
                 error_message, reprocess_number)
            SELECT
                e.podcast_id,
                p.slug,
                p.title,
                e.episode_id,
                e.title,
                COALESCE(e.processed_at, e.updated_at),
                NULL,
                CASE
                    WHEN e.status = 'failed' THEN 'failed'
                    ELSE 'completed'
                END,
                COALESCE(e.ads_removed, 0),
                e.error_message,
                1
            FROM episodes e
            JOIN podcasts p ON e.podcast_id = p.id
            WHERE e.status IN ('processed', 'failed')
              AND NOT EXISTS (
                  SELECT 1 FROM processing_history h
                  WHERE h.podcast_id = e.podcast_id
                    AND h.episode_id = e.episode_id
              )
        ''')

        count = cursor.rowcount
        conn.commit()
        if count > 0:
            logger.info(f"Backfilled {count} records to processing_history")
        return count

    def get_processing_history(self, limit: int = 50, offset: int = 0,
                                status_filter: str = None,
                                podcast_slug: str = None,
                                sort_by: str = 'processed_at',
                                sort_dir: str = 'desc') -> Tuple[List[Dict], int]:
        """Get processing history with pagination. Returns (entries, total_count)."""
        conn = self.get_connection()

        # Build WHERE clause
        where_clauses = []
        params = []

        if status_filter and status_filter in ('completed', 'failed'):
            where_clauses.append("status = ?")
            params.append(status_filter)

        if podcast_slug:
            where_clauses.append("podcast_slug = ?")
            params.append(podcast_slug)

        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

        # Validate sort column
        valid_sort_cols = ['processed_at', 'podcast_title', 'episode_title',
                          'processing_duration_seconds', 'ads_detected',
                          'reprocess_number', 'status', 'llm_cost']
        if sort_by not in valid_sort_cols:
            sort_by = 'processed_at'
        sort_dir = 'DESC' if sort_dir.lower() == 'desc' else 'ASC'

        # Get total count
        cursor = conn.execute(
            f"SELECT COUNT(*) FROM processing_history WHERE {where_sql}",
            params
        )
        total_count = cursor.fetchone()[0]

        # Get paginated results
        query_params = params + [limit, offset]
        cursor = conn.execute(
            f"""SELECT * FROM processing_history
                WHERE {where_sql}
                ORDER BY {sort_by} {sort_dir}
                LIMIT ? OFFSET ?""",
            query_params
        )

        entries = [dict(row) for row in cursor.fetchall()]
        return entries, total_count

    def get_processing_history_stats(self) -> Dict:
        """Get aggregate statistics from processing history in a single query."""
        conn = self.get_connection()

        cursor = conn.execute(
            """SELECT
                COUNT(*) AS total_processed,
                COUNT(CASE WHEN status = 'completed' THEN 1 END) AS completed_count,
                COUNT(CASE WHEN status = 'failed' THEN 1 END) AS failed_count,
                AVG(CASE WHEN status = 'completed' AND processing_duration_seconds IS NOT NULL
                         THEN processing_duration_seconds END) AS avg_time,
                COALESCE(SUM(CASE WHEN status = 'completed' THEN ads_detected END), 0) AS total_ads,
                COUNT(CASE WHEN reprocess_number > 1 THEN 1 END) AS reprocess_count,
                COUNT(DISTINCT podcast_slug || '/' || episode_id) AS unique_episodes,
                COALESCE(SUM(CASE WHEN status = 'completed' THEN input_tokens END), 0) AS total_input_tokens,
                COALESCE(SUM(CASE WHEN status = 'completed' THEN output_tokens END), 0) AS total_output_tokens,
                COALESCE(SUM(CASE WHEN status = 'completed' THEN llm_cost END), 0.0) AS total_llm_cost
            FROM processing_history"""
        )
        row = cursor.fetchone()

        return {
            'total_processed': row['total_processed'],
            'completed_count': row['completed_count'],
            'failed_count': row['failed_count'],
            'avg_processing_time_seconds': round(row['avg_time'] or 0, 2),
            'total_ads_detected': row['total_ads'],
            'reprocess_count': row['reprocess_count'],
            'unique_episodes': row['unique_episodes'],
            'total_input_tokens': row['total_input_tokens'],
            'total_output_tokens': row['total_output_tokens'],
            'total_llm_cost': round(row['total_llm_cost'], 6),
        }

    def get_episode_reprocess_count(self, podcast_id: int, episode_id: str) -> int:
        """Get the number of times an episode has been processed."""
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT COUNT(*) FROM processing_history
               WHERE podcast_id = ? AND episode_id = ?""",
            (podcast_id, episode_id)
        )
        return cursor.fetchone()[0]

    def get_episode_token_usage(self, episode_id: str) -> Optional[Dict]:
        """Get token usage for the most recent completed processing of an episode.
        Returns {input_tokens, output_tokens, llm_cost} or None."""
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT input_tokens, output_tokens, llm_cost
               FROM processing_history
               WHERE episode_id = ? AND status = 'completed'
               ORDER BY processed_at DESC LIMIT 1""",
            (episode_id,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        return {
            'input_tokens': row['input_tokens'] or 0,
            'output_tokens': row['output_tokens'] or 0,
            'llm_cost': row['llm_cost'] or 0.0,
        }

    def export_processing_history(self, status_filter: str = None,
                                   podcast_slug: str = None) -> List[Dict]:
        """Export all processing history (no pagination) for export.

        Kept as a materialised list for callers that need random access;
        for large-table exports use :meth:`iter_processing_history_rows`
        instead, which yields ``sqlite3.Row`` objects lazily.
        """
        return list(self.iter_processing_history_rows(status_filter, podcast_slug))

    def iter_processing_history_rows(self, status_filter: str = None,
                                     podcast_slug: str = None):
        """Generator that yields per-row dicts without materialising the
        whole result set in memory.

        Intended for streaming export endpoints; the underlying sqlite3
        cursor is iterated lazily so the peak memory footprint stays near
        one row's worth regardless of table size.
        """
        conn = self.get_connection()
        where_clauses = []
        params = []
        if status_filter and status_filter in ('completed', 'failed'):
            where_clauses.append("status = ?")
            params.append(status_filter)
        if podcast_slug:
            where_clauses.append("podcast_slug = ?")
            params.append(podcast_slug)
        where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"
        cursor = conn.execute(
            f"""SELECT * FROM processing_history
                WHERE {where_sql}
                ORDER BY processed_at DESC""",
            params
        )
        try:
            for row in cursor:
                yield dict(row)
        finally:
            cursor.close()

    def get_latest_completed_processing(self) -> Optional[Dict]:
        """Get the most recent completed processing history entry with episode durations.

        Returns a dict with keys: episode_id, podcast_slug, episode_title,
        processing_duration_seconds, llm_cost, ads_detected,
        original_duration, new_duration, podcast_title. Returns None if no completed entries.
        """
        conn = self.get_connection()
        row = conn.execute(
            """SELECT h.episode_id, h.podcast_slug, h.episode_title,
                      h.processing_duration_seconds, h.llm_cost, h.ads_detected,
                      e.original_duration, e.new_duration, h.podcast_title
               FROM processing_history h
               LEFT JOIN episodes e ON e.episode_id = h.episode_id
                   AND e.podcast_id = h.podcast_id
               WHERE h.status = 'completed'
               ORDER BY h.processed_at DESC
               LIMIT 1"""
        ).fetchone()
        if row is None:
            return None
        return {
            'episode_id': row['episode_id'],
            'podcast_slug': row['podcast_slug'],
            'episode_title': row['episode_title'],
            'processing_duration_seconds': row['processing_duration_seconds'],
            'llm_cost': row['llm_cost'],
            'ads_detected': row['ads_detected'],
            'original_duration': row['original_duration'],
            'new_duration': row['new_duration'],
            'podcast_title': row['podcast_title'],
        }

    def get_dashboard_stats(self, podcast_slug: str = None) -> Dict:
        """Get aggregate dashboard stats with avg/min/max, optionally filtered by podcast."""
        conn = self.get_connection()
        where_clauses = ["h.status = 'completed'"]
        params = []
        if podcast_slug:
            where_clauses.append("h.podcast_slug = ?")
            params.append(podcast_slug)
        where_sql = " AND ".join(where_clauses)

        row = conn.execute(
            f"""SELECT
                COUNT(*) AS total_episodes,
                COALESCE(AVG(h.ads_detected), 0) AS avg_ads_removed,
                COALESCE(MIN(h.ads_detected), 0) AS min_ads_removed,
                COALESCE(MAX(h.ads_detected), 0) AS max_ads_removed,
                COALESCE(SUM(h.ads_detected), 0) AS total_ads_removed,
                COALESCE(AVG(h.llm_cost), 0) AS avg_cost,
                COALESCE(MIN(h.llm_cost), 0) AS min_cost,
                COALESCE(MAX(h.llm_cost), 0) AS max_cost,
                COALESCE(AVG(h.processing_duration_seconds), 0) AS avg_processing_time,
                COALESCE(MIN(h.processing_duration_seconds), 0) AS min_processing_time,
                COALESCE(MAX(h.processing_duration_seconds), 0) AS max_processing_time,
                COALESCE(AVG(e.original_duration), 0) AS avg_episode_length,
                COALESCE(MIN(e.original_duration), 0) AS min_episode_length,
                COALESCE(MAX(e.original_duration), 0) AS max_episode_length,
                COALESCE(AVG(CASE WHEN e.original_duration > 0 AND e.new_duration > 0
                    THEN e.original_duration - e.new_duration END), 0) AS avg_time_saved,
                COALESCE(MIN(CASE WHEN e.original_duration > 0 AND e.new_duration > 0
                    THEN e.original_duration - e.new_duration END), 0) AS min_time_saved,
                COALESCE(MAX(CASE WHEN e.original_duration > 0 AND e.new_duration > 0
                    THEN e.original_duration - e.new_duration END), 0) AS max_time_saved,
                COALESCE(SUM(CASE WHEN e.original_duration > 0 AND e.new_duration > 0
                    THEN e.original_duration - e.new_duration END), 0) AS total_time_saved,
                COALESCE(SUM(h.input_tokens), 0) AS total_input_tokens,
                COALESCE(SUM(h.output_tokens), 0) AS total_output_tokens,
                COALESCE(SUM(h.llm_cost), 0) AS total_llm_cost,
                COALESCE(AVG(h.input_tokens), 0) AS avg_input_tokens,
                COALESCE(AVG(h.output_tokens), 0) AS avg_output_tokens
            FROM processing_history h
            LEFT JOIN episodes e ON e.episode_id = h.episode_id
                AND e.podcast_id = h.podcast_id
            WHERE {where_sql}""",
            params
        ).fetchone()

        if not row or row['total_episodes'] == 0:
            return {k: 0 for k in [
                'totalEpisodesProcessed', 'avgTimeSavedSeconds', 'minTimeSavedSeconds',
                'maxTimeSavedSeconds', 'totalTimeSavedSeconds', 'avgAdsRemoved',
                'minAdsRemoved', 'maxAdsRemoved', 'totalAdsRemoved',
                'avgCostPerEpisode', 'minCostPerEpisode', 'maxCostPerEpisode',
                'avgProcessingTimeSeconds', 'minProcessingTimeSeconds',
                'maxProcessingTimeSeconds', 'avgEpisodeLengthSeconds',
                'minEpisodeLengthSeconds', 'maxEpisodeLengthSeconds',
                'totalInputTokens', 'totalOutputTokens', 'totalLlmCost',
                'avgInputTokens', 'avgOutputTokens',
            ]}

        return {
            'totalEpisodesProcessed': row['total_episodes'],
            'avgTimeSavedSeconds': round(row['avg_time_saved'], 1),
            'minTimeSavedSeconds': round(row['min_time_saved'], 1),
            'maxTimeSavedSeconds': round(row['max_time_saved'], 1),
            'totalTimeSavedSeconds': round(row['total_time_saved'], 1),
            'avgAdsRemoved': round(row['avg_ads_removed'], 1),
            'minAdsRemoved': row['min_ads_removed'],
            'maxAdsRemoved': row['max_ads_removed'],
            'totalAdsRemoved': row['total_ads_removed'],
            'avgCostPerEpisode': round(row['avg_cost'], 6),
            'minCostPerEpisode': round(row['min_cost'], 6),
            'maxCostPerEpisode': round(row['max_cost'], 6),
            'avgProcessingTimeSeconds': round(row['avg_processing_time'], 1),
            'minProcessingTimeSeconds': round(row['min_processing_time'], 1),
            'maxProcessingTimeSeconds': round(row['max_processing_time'], 1),
            'avgEpisodeLengthSeconds': round(row['avg_episode_length'], 1),
            'minEpisodeLengthSeconds': round(row['min_episode_length'], 1),
            'maxEpisodeLengthSeconds': round(row['max_episode_length'], 1),
            'totalInputTokens': row['total_input_tokens'],
            'totalOutputTokens': row['total_output_tokens'],
            'totalLlmCost': round(row['total_llm_cost'], 6),
            'avgInputTokens': round(row['avg_input_tokens']),
            'avgOutputTokens': round(row['avg_output_tokens']),
        }

    def get_stats_by_day(self, podcast_slug: str = None) -> List[Dict]:
        """Get episode processing counts by day of week."""
        conn = self.get_connection()
        where_clauses = ["status = 'completed'"]
        params = []
        if podcast_slug:
            where_clauses.append("podcast_slug = ?")
            params.append(podcast_slug)
        where_sql = " AND ".join(where_clauses)

        rows = conn.execute(
            f"""SELECT
                CAST(strftime('%w', processed_at) AS INTEGER) AS day_of_week,
                COUNT(*) AS count,
                COALESCE(AVG(ads_detected), 0) AS avg_ads
            FROM processing_history
            WHERE {where_sql} AND processed_at IS NOT NULL
            GROUP BY day_of_week
            ORDER BY day_of_week""",
            params
        ).fetchall()

        day_names = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
        result = []
        day_map = {r['day_of_week']: r for r in rows}
        for i in range(7):
            r = day_map.get(i)
            result.append({
                'day': day_names[i],
                'dayIndex': i,
                'count': r['count'] if r else 0,
                'avgAds': round(r['avg_ads'], 1) if r else 0,
            })
        return result

    def get_stats_by_podcast(self) -> List[Dict]:
        """Get per-podcast aggregate stats, ordered by total ads removed."""
        conn = self.get_connection()
        rows = conn.execute(
            """SELECT
                h.podcast_slug,
                h.podcast_title,
                COUNT(*) AS episode_count,
                COALESCE(SUM(h.ads_detected), 0) AS total_ads,
                COALESCE(AVG(h.ads_detected), 0) AS avg_ads,
                COALESCE(AVG(e.original_duration), 0) AS avg_episode_length,
                COALESCE(AVG(CASE WHEN e.original_duration > 0 AND e.new_duration > 0
                    THEN e.original_duration - e.new_duration END), 0) AS avg_time_saved,
                COALESCE(SUM(h.llm_cost), 0) AS total_cost,
                COALESCE(SUM(h.input_tokens), 0) AS total_input_tokens,
                COALESCE(SUM(h.output_tokens), 0) AS total_output_tokens,
                COALESCE(AVG(h.input_tokens + h.output_tokens), 0) AS avg_tokens_per_episode
            FROM processing_history h
            LEFT JOIN episodes e ON e.episode_id = h.episode_id
                AND e.podcast_id = h.podcast_id
            WHERE h.status = 'completed'
            GROUP BY h.podcast_slug
            ORDER BY total_ads DESC"""
        ).fetchall()

        return [{
            'podcastSlug': r['podcast_slug'],
            'podcastTitle': r['podcast_title'],
            'episodeCount': r['episode_count'],
            'totalAds': r['total_ads'],
            'avgAds': round(r['avg_ads'], 1),
            'avgEpisodeLengthSeconds': round(r['avg_episode_length'], 1),
            'avgTimeSavedSeconds': round(r['avg_time_saved'], 1),
            'totalCost': round(r['total_cost'], 6),
            'totalInputTokens': r['total_input_tokens'],
            'totalOutputTokens': r['total_output_tokens'],
            'avgTokensPerEpisode': round(r['avg_tokens_per_episode']),
        } for r in rows]
