"""Statistics and token usage mixin for MinusPod database."""
import logging
from typing import Dict, List, Optional, Tuple

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
                              input_tokens: int, output_tokens: int) -> float:
        """Calculate cost for a single LLM call based on model pricing.

        Tries exact match first, then prefix match for versioned model IDs.
        Returns 0.0 with a warning for unknown models.
        """
        # Exact match
        cursor = conn.execute(
            "SELECT input_cost_per_mtok, output_cost_per_mtok FROM model_pricing WHERE model_id = ?",
            (model_id,)
        )
        row = cursor.fetchone()

        # Prefix match: strip trailing version suffix (e.g. claude-sonnet-4-5-20250929 -> claude-sonnet-4-5)
        if not row:
            cursor = conn.execute(
                """SELECT input_cost_per_mtok, output_cost_per_mtok FROM model_pricing
                   WHERE ? LIKE model_id || '%'
                   ORDER BY length(model_id) DESC LIMIT 1""",
                (model_id,)
            )
            row = cursor.fetchone()

        if not row:
            logger.warning(f"No pricing found for model '{model_id}', cost recorded as $0")
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
        cost = self._calculate_token_cost(conn, model_id, input_tokens, output_tokens)

        # Upsert per-model token_usage row
        conn.execute(
            """INSERT INTO token_usage (model_id, total_input_tokens, total_output_tokens, total_cost, call_count, updated_at)
               VALUES (?, ?, ?, ?, 1, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
               ON CONFLICT(model_id) DO UPDATE SET
                 total_input_tokens = total_input_tokens + excluded.total_input_tokens,
                 total_output_tokens = total_output_tokens + excluded.total_output_tokens,
                 total_cost = total_cost + excluded.total_cost,
                 call_count = call_count + 1,
                 updated_at = excluded.updated_at""",
            (model_id, input_tokens, output_tokens, cost)
        )

        # Update global stats counters
        for key, value in [('total_input_tokens', float(input_tokens)),
                           ('total_output_tokens', float(output_tokens)),
                           ('total_llm_cost', cost)]:
            conn.execute(
                """INSERT INTO stats (key, value, updated_at)
                   VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                   ON CONFLICT(key) DO UPDATE SET
                     value = value + excluded.value,
                     updated_at = excluded.updated_at""",
                (key, value)
            )

        conn.commit()
        logger.debug(
            f"Token usage: model={model_id} in={input_tokens} out={output_tokens} cost=${cost:.6f}"
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
               LEFT JOIN model_pricing mp ON tu.model_id = mp.model_id
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
                'inputCostPerMtok': row['input_cost_per_mtok'] if row['input_cost_per_mtok'] is not None else None,
                'outputCostPerMtok': row['output_cost_per_mtok'] if row['output_cost_per_mtok'] is not None else None,
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
        """Get aggregate statistics from processing history."""
        conn = self.get_connection()

        # Total processed
        cursor = conn.execute("SELECT COUNT(*) FROM processing_history")
        total_processed = cursor.fetchone()[0]

        # Completed count
        cursor = conn.execute("SELECT COUNT(*) FROM processing_history WHERE status = 'completed'")
        completed_count = cursor.fetchone()[0]

        # Failed count
        cursor = conn.execute("SELECT COUNT(*) FROM processing_history WHERE status = 'failed'")
        failed_count = cursor.fetchone()[0]

        # Average processing time (for completed only)
        cursor = conn.execute(
            """SELECT AVG(processing_duration_seconds)
               FROM processing_history
               WHERE status = 'completed' AND processing_duration_seconds IS NOT NULL"""
        )
        avg_time = cursor.fetchone()[0] or 0

        # Total ads detected
        cursor = conn.execute("SELECT SUM(ads_detected) FROM processing_history WHERE status = 'completed'")
        total_ads = cursor.fetchone()[0] or 0

        # Reprocess count (entries with reprocess_number > 1)
        cursor = conn.execute("SELECT COUNT(*) FROM processing_history WHERE reprocess_number > 1")
        reprocess_count = cursor.fetchone()[0]

        # Unique episodes processed
        cursor = conn.execute("SELECT COUNT(DISTINCT podcast_slug || '/' || episode_id) FROM processing_history")
        unique_episodes = cursor.fetchone()[0]

        # LLM token/cost totals from completed entries
        cursor = conn.execute(
            """SELECT COALESCE(SUM(input_tokens), 0),
                      COALESCE(SUM(output_tokens), 0),
                      COALESCE(SUM(llm_cost), 0.0)
               FROM processing_history WHERE status = 'completed'"""
        )
        row = cursor.fetchone()
        total_input_tokens = row[0]
        total_output_tokens = row[1]
        total_llm_cost = row[2]

        return {
            'total_processed': total_processed,
            'completed_count': completed_count,
            'failed_count': failed_count,
            'avg_processing_time_seconds': round(avg_time, 2),
            'total_ads_detected': total_ads,
            'reprocess_count': reprocess_count,
            'unique_episodes': unique_episodes,
            'total_input_tokens': total_input_tokens,
            'total_output_tokens': total_output_tokens,
            'total_llm_cost': round(total_llm_cost, 6),
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
        """Export all processing history (no pagination) for export."""
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

        cursor = conn.execute(
            f"""SELECT * FROM processing_history
                WHERE {where_sql}
                ORDER BY processed_at DESC""",
            params
        )

        return [dict(row) for row in cursor.fetchall()]

    def get_latest_completed_processing(self) -> Optional[Dict]:
        """Get the most recent completed processing history entry with episode durations.

        Returns a dict with keys: episode_id, podcast_slug, episode_title,
        processing_duration_seconds, llm_cost, ads_detected,
        original_duration, new_duration. Returns None if no completed entries.
        """
        conn = self.get_connection()
        row = conn.execute(
            """SELECT h.episode_id, h.podcast_slug, h.episode_title,
                      h.processing_duration_seconds, h.llm_cost, h.ads_detected,
                      e.original_duration, e.new_duration
               FROM processing_history h
               LEFT JOIN episodes e ON e.episode_id = h.episode_id
                   AND e.podcast_slug = h.podcast_slug
               WHERE h.status = 'completed'
               ORDER BY h.processed_at DESC
               LIMIT 1"""
        ).fetchone()
        if row is None:
            return None
        return {
            'episode_id': row[0],
            'podcast_slug': row[1],
            'episode_title': row[2],
            'processing_duration_seconds': row[3],
            'llm_cost': row[4],
            'ads_detected': row[5],
            'original_duration': row[6],
            'new_duration': row[7],
        }
