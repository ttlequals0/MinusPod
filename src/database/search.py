"""Full-text search mixin for MinusPod database."""
import logging

import nh3

logger = logging.getLogger(__name__)


class SearchMixin:
    """Full-text search (FTS5) methods."""

    def rebuild_search_index(self) -> int:
        """Rebuild the FTS5 search index from scratch.

        Indexes:
        - Episodes: title, description, transcript
        - Podcasts: title, description
        - Patterns: text, sponsor
        - Sponsors: name, aliases

        Returns count of indexed items.
        """
        conn = self.get_connection()
        count = 0

        # Clear existing index
        conn.execute("DELETE FROM search_index")

        # Index podcasts
        cursor = conn.execute("""
            SELECT slug, title, description
            FROM podcasts
        """)
        for row in cursor:
            conn.execute("""
                INSERT INTO search_index (content_type, content_id, podcast_slug, title, body, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ('podcast', row['slug'], row['slug'], row['title'],
                  row['description'] or '', ''))
            count += 1

        # Index episodes of every status; body is the transcript when one exists, else ''.
        cursor = conn.execute("""
            SELECT e.episode_id, e.title, e.description, p.slug, ed.transcript_text
            FROM episodes e
            JOIN podcasts p ON e.podcast_id = p.id
            LEFT JOIN episode_details ed ON e.id = ed.episode_id
        """)
        for row in cursor:
            # Limit transcript size to avoid huge index entries
            transcript = (row['transcript_text'] or '')[:100000]  # ~100k chars max
            conn.execute("""
                INSERT INTO search_index (content_type, content_id, podcast_slug, title, body, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ('episode', row['episode_id'], row['slug'], row['title'],
                  transcript, row['description'] or ''))
            count += 1

        # Index patterns
        cursor = conn.execute("""
            SELECT ap.id, ap.text_template, ks.name AS sponsor, ap.scope
            FROM ad_patterns ap
            LEFT JOIN known_sponsors ks ON ap.sponsor_id = ks.id
            WHERE ap.is_active = 1
        """)
        for row in cursor:
            conn.execute("""
                INSERT INTO search_index (content_type, content_id, podcast_slug, title, body, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ('pattern', str(row['id']), row['scope'] or 'global',
                  row['sponsor'] or 'Unknown', row['text_template'] or '', ''))
            count += 1

        # Index sponsors
        cursor = conn.execute("""
            SELECT id, name, aliases
            FROM known_sponsors
            WHERE is_active = 1
        """)
        for row in cursor:
            conn.execute("""
                INSERT INTO search_index (content_type, content_id, podcast_slug, title, body, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ('sponsor', str(row['id']), 'global', row['name'],
                  row['aliases'] or '', ''))
            count += 1

        conn.commit()
        logger.info(f"Search index rebuilt with {count} items")
        return count

    def index_episode(self, episode_id: str, slug: str) -> bool:
        """Index or re-index a single episode in the search index."""
        return self.index_episodes([(episode_id, slug)]) > 0

    def index_episodes(self, pairs: list[tuple[str, str]], conn=None) -> int:
        """Batch (re)index episodes: one DELETE and one INSERT for the whole set.

        pairs is [(episode_id, slug), ...]. When conn is passed, the caller owns
        the transaction: this never commits or rolls back on that connection.
        """
        pairs = list(dict.fromkeys(pairs))
        if not pairs:
            return 0
        own_conn = conn is None
        if own_conn:
            conn = self.get_connection()
        values_sql = ','.join('(?,?)' for _ in pairs)
        flat = [v for pair in pairs for v in pair]
        # values_sql is just "(?,?),(?,?),..." repeated per pair; all values are bound params.
        delete_sql = (
            "DELETE FROM search_index WHERE content_type = 'episode' "  # noqa: S608
            f"AND (content_id, podcast_slug) IN (VALUES {values_sql})"
        )
        select_sql = (
            "SELECT e.episode_id, e.title, e.description, p.slug, ed.transcript_text "  # noqa: S608
            "FROM episodes e "
            "JOIN podcasts p ON e.podcast_id = p.id "
            "LEFT JOIN episode_details ed ON e.id = ed.episode_id "
            f"WHERE (e.episode_id, p.slug) IN (VALUES {values_sql})"
        )
        try:
            conn.execute(delete_sql, flat)
            rows = conn.execute(select_sql, flat).fetchall()
            insert_values = [
                ('episode', row['episode_id'], row['slug'], row['title'],
                 (row['transcript_text'] or '')[:100000], row['description'] or '')
                for row in rows
            ]
            if insert_values:
                conn.executemany("""
                    INSERT INTO search_index (content_type, content_id, podcast_slug, title, body, metadata)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, insert_values)
            if own_conn:
                conn.commit()
            return len(insert_values)
        except Exception as e:
            if own_conn:
                conn.rollback()
            logger.error(f"Batch index failed for {len(pairs)} episode(s): {e}")
            return 0

    @staticmethod
    def _sanitize_snippet(snippet):
        """Sanitize FTS5 snippet HTML, allowing only <mark> highlight tags."""
        if not snippet:
            return snippet
        return nh3.clean(snippet, tags={"mark"}, attributes={})

    def search(self, query: str, content_type: str | None = None, limit: int = 50) -> list[dict]:
        """Full-text search across indexed content.

        Args:
            query: Search query (supports FTS5 query syntax)
            content_type: Filter by type ('episode', 'podcast', 'pattern', 'sponsor')
            limit: Maximum results to return

        Returns:
            List of search results with type, id, slug, title, snippet, and score
        """
        conn = self.get_connection()

        # Clean query for FTS5 (escape special characters)
        clean_query = query.replace('"', '""').strip()
        if not clean_query:
            return []

        # Add wildcards for partial matching
        search_query = f'"{clean_query}"* OR {clean_query}*'

        try:
            if content_type:
                cursor = conn.execute("""
                    SELECT
                        content_type,
                        content_id,
                        podcast_slug,
                        title,
                        snippet(search_index, 4, '<mark>', '</mark>', '...', 64) as snippet,
                        bm25(search_index) as score
                    FROM search_index
                    WHERE search_index MATCH ?
                    AND content_type = ?
                    ORDER BY bm25(search_index)
                    LIMIT ?
                """, (search_query, content_type, limit))
            else:
                cursor = conn.execute("""
                    SELECT
                        content_type,
                        content_id,
                        podcast_slug,
                        title,
                        snippet(search_index, 4, '<mark>', '</mark>', '...', 64) as snippet,
                        bm25(search_index) as score
                    FROM search_index
                    WHERE search_index MATCH ?
                    ORDER BY bm25(search_index)
                    LIMIT ?
                """, (search_query, limit))

            results = []
            for row in cursor:
                results.append({
                    'type': row['content_type'],
                    'id': row['content_id'],
                    'podcastSlug': row['podcast_slug'],
                    'title': row['title'],
                    'snippet': self._sanitize_snippet(row['snippet']),
                    'score': abs(row['score'])  # BM25 returns negative scores
                })

            return results

        except Exception as e:
            logger.error(f"Search error for query '{query}': {e}")
            return []

    def search_grouped(self, query: str, limit: int = 50) -> dict:
        """Grouped search: shows, episodes, transcripts, patterns, sponsors; each an independent FTS query.

        Patterns and sponsors are always included so the endpoint has one shape regardless of
        caller (Dashboard box, palette, or the Advanced page, which is their only consumer).
        """
        conn = self.get_connection()
        clean_query = query.replace('"', '""').strip()
        empty = {'shows': [], 'episodes': [], 'transcripts': [], 'patterns': [], 'sponsors': []}
        if not clean_query:
            return empty
        fts_query = self._safe_fts_query(clean_query)
        needle = query.strip()
        # Escape LIKE metacharacters so user input cannot widen the match
        escaped = needle.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        like_pattern = f'%{escaped}%'
        like_prefix = f'{escaped}%'

        # Each group is independent: one group's unexpected failure should not blank the others.
        groups = {
            'shows': lambda: self._search_shows(conn, fts_query, like_pattern, like_prefix, limit),
            'episodes': lambda: self._search_episodes(conn, fts_query, like_pattern, like_prefix, limit),
            'transcripts': lambda: self._search_transcripts(conn, fts_query),
            'patterns': lambda: self._search_patterns(conn, fts_query, limit),
            'sponsors': lambda: self._search_sponsors(conn, fts_query, limit),
        }
        results = dict(empty)
        for name, fn in groups.items():
            try:
                results[name] = fn()
            except Exception as e:
                logger.error(f"Grouped search ({name}) failed for query '{query}': {e}")

        return results

    @staticmethod
    def _safe_fts_query(clean_query: str) -> str:
        """Quote every term so punctuation and bare AND/OR/NOT can't be parsed as FTS5 syntax."""
        tokens = clean_query.split()
        quoted = [f'"{t}"' for t in tokens]
        quoted[-1] += '*'
        return f'"{clean_query}"* OR {" AND ".join(quoted)}'

    @staticmethod
    def _merge_fts_and_like(fts_rows, key_fn, row_builder, like_fetch, like_row_builder, limit):
        """FTS rows first, then a LIKE fallback (only run if still under limit) for
        substring matches FTS tokenization misses; deduped by key_fn, capped at limit."""
        results = [row_builder(r) for r in fts_rows]
        seen = {key_fn(r) for r in fts_rows}
        if len(results) < limit:
            for r in like_fetch():
                key = key_fn(r)
                if key not in seen:
                    results.append(like_row_builder(r))
                    seen.add(key)
        return results[:limit]

    def _search_shows(self, conn, fts_query, like_pattern, like_prefix, limit):
        """Shows: FTS over title+description, plus a title LIKE pass for substrings."""
        rows = conn.execute("""
            SELECT si.content_id AS slug, COALESCE(p.title_override, p.title) AS title,
                   snippet(search_index, -1, '<mark>', '</mark>', '...', 64) AS snippet
            FROM search_index si
            JOIN podcasts p ON p.slug = si.content_id
            WHERE si.content_type = 'podcast' AND search_index MATCH '{title body}:(' || ? || ')'
            ORDER BY bm25(search_index)
            LIMIT ?
        """, (fts_query, limit)).fetchall()

        def like_fetch():
            return conn.execute("""
                SELECT slug, COALESCE(title_override, title) AS title
                FROM podcasts
                WHERE COALESCE(title_override, title) LIKE ? ESCAPE '\\'
                ORDER BY (COALESCE(title_override, title) LIKE ? ESCAPE '\\') DESC, title
                LIMIT ?
            """, (like_pattern, like_prefix, limit)).fetchall()

        return self._merge_fts_and_like(
            rows, key_fn=lambda r: r['slug'],
            row_builder=lambda r: {'slug': r['slug'], 'title': r['title'],
                                    'snippet': self._sanitize_snippet(r['snippet'])},
            like_fetch=like_fetch,
            like_row_builder=lambda r: {'slug': r['slug'], 'title': r['title'], 'snippet': None},
            limit=limit)

    def _search_episodes(self, conn, fts_query, like_pattern, like_prefix, limit):
        """Episodes: FTS over title+description, plus a title LIKE pass, deduped by episode."""
        rows = conn.execute("""
            SELECT si.content_id AS episode_id, p.slug AS feed_slug,
                   COALESCE(p.title_override, p.title) AS feed_title,
                   e.title AS title, e.status AS status, e.published_at AS publish_date,
                   snippet(search_index, -1, '<mark>', '</mark>', '...', 64) AS snippet
            FROM search_index si
            JOIN podcasts p ON p.slug = si.podcast_slug
            JOIN episodes e ON e.episode_id = si.content_id AND e.podcast_id = p.id
            WHERE si.content_type = 'episode'
              AND search_index MATCH '{title metadata}:(' || ? || ')'
            ORDER BY bm25(search_index)
            LIMIT ?
        """, (fts_query, limit)).fetchall()

        def like_fetch():
            return conn.execute("""
                SELECT e.episode_id AS episode_id, p.slug AS feed_slug,
                       COALESCE(p.title_override, p.title) AS feed_title,
                       e.title AS title, e.status AS status, e.published_at AS publish_date
                FROM episodes e JOIN podcasts p ON e.podcast_id = p.id
                WHERE e.title LIKE ? ESCAPE '\\'
                ORDER BY (e.title LIKE ? ESCAPE '\\') DESC, e.published_at DESC
                LIMIT ?
            """, (like_pattern, like_prefix, limit)).fetchall()

        def row_builder(r):
            return {'feedSlug': r['feed_slug'], 'feedTitle': r['feed_title'], 'episodeId': r['episode_id'],
                    'title': r['title'], 'status': r['status'], 'publishDate': r['publish_date'],
                    'snippet': self._sanitize_snippet(r['snippet'])}

        def like_row_builder(r):
            return {'feedSlug': r['feed_slug'], 'feedTitle': r['feed_title'], 'episodeId': r['episode_id'],
                    'title': r['title'], 'status': r['status'], 'publishDate': r['publish_date'],
                    'snippet': None}

        return self._merge_fts_and_like(
            rows, key_fn=lambda r: (r['feed_slug'], r['episode_id']),
            row_builder=row_builder, like_fetch=like_fetch,
            like_row_builder=like_row_builder, limit=limit)

    def _search_transcripts(self, conn, fts_query):
        """Transcripts: body-only word matches, capped at 3 episodes; timestamp is always None (no VTT offset)."""
        rows = conn.execute("""
            SELECT si.content_id AS episode_id, si.podcast_slug AS feed_slug, si.title AS title,
                   snippet(search_index, -1, '<mark>', '</mark>', '...', 64) AS snippet
            FROM search_index si
            WHERE si.content_type = 'episode' AND search_index MATCH 'body:(' || ? || ')'
            ORDER BY bm25(search_index)
            LIMIT 3
        """, (fts_query,)).fetchall()
        return [{
            'feedSlug': r['feed_slug'], 'episodeId': r['episode_id'], 'title': r['title'],
            'snippet': self._sanitize_snippet(r['snippet']), 'timestamp': None,
        } for r in rows]

    def _search_patterns(self, conn, fts_query, limit):
        """Patterns: FTS over sponsor name + pattern text. Advanced-page-only group."""
        rows = conn.execute("""
            SELECT si.content_id AS id, si.podcast_slug AS scope, si.title AS sponsor,
                   snippet(search_index, -1, '<mark>', '</mark>', '...', 64) AS snippet
            FROM search_index si
            WHERE si.content_type = 'pattern' AND search_index MATCH '{title body}:(' || ? || ')'
            ORDER BY bm25(search_index)
            LIMIT ?
        """, (fts_query, limit)).fetchall()
        return [{'id': r['id'], 'scope': r['scope'], 'sponsor': r['sponsor'],
                 'snippet': self._sanitize_snippet(r['snippet'])} for r in rows]

    def _search_sponsors(self, conn, fts_query, limit):
        """Sponsors: FTS over name + aliases. Advanced-page-only group."""
        rows = conn.execute("""
            SELECT si.content_id AS id, si.title AS name,
                   snippet(search_index, -1, '<mark>', '</mark>', '...', 64) AS snippet
            FROM search_index si
            WHERE si.content_type = 'sponsor' AND search_index MATCH '{title body}:(' || ? || ')'
            ORDER BY bm25(search_index)
            LIMIT ?
        """, (fts_query, limit)).fetchall()
        return [{'id': r['id'], 'name': r['name'],
                 'snippet': self._sanitize_snippet(r['snippet'])} for r in rows]

    def get_search_index_stats(self) -> dict[str, int]:
        """Get statistics about the search index."""
        conn = self.get_connection()

        stats = {}
        cursor = conn.execute("""
            SELECT content_type, COUNT(*) as count
            FROM search_index
            GROUP BY content_type
        """)
        for row in cursor:
            stats[row['content_type']] = row['count']

        stats['total'] = sum(stats.values())
        return stats
