"""Full-text search mixin for MinusPod database."""
import logging

import nh3

logger = logging.getLogger(__name__)

# Grouped-search snippet delimiters: literal "<mark>" in indexed text must not read as a highlight.
_HL_OPEN = '\x02'
_HL_CLOSE = '\x03'

# All groups search_grouped can compute; also the valid values for the /search groups= param.
SEARCH_GROUP_NAMES = ('shows', 'episodes', 'transcripts', 'patterns', 'sponsors')

# Episodes per indexing statement: two bound params each, plus one MATCH term each.
_INDEX_CHUNK = 500


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
        """Batch (re)index episodes as a DELETE+INSERT per chunk. pairs is
        [(episode_id, slug), ...]; if conn is passed, the caller owns the transaction."""
        pairs = list(dict.fromkeys(pairs))
        if not pairs:
            return 0
        own_conn = conn is None
        if own_conn:
            conn = self.get_connection()
        try:
            indexed = 0
            for start in range(0, len(pairs), _INDEX_CHUNK):
                indexed += self._index_episode_chunk(conn, pairs[start:start + _INDEX_CHUNK])
            if own_conn:
                conn.commit()
            return indexed
        except Exception as e:
            if own_conn:
                conn.rollback()
            logger.error(f"Batch index failed for {len(pairs)} episode(s): {e}")
            return 0

    def _index_episode_chunk(self, conn, pairs: list[tuple[str, str]]) -> int:
        """Reindex one chunk of pairs, small enough to stay under SQLite's variable limit."""
        self._delete_indexed_episodes(conn, pairs)
        values_sql = ','.join('(?,?)' for _ in pairs)
        rows = conn.execute(
            "SELECT e.episode_id, e.title, e.description, p.slug, ed.transcript_text "  # noqa: S608
            "FROM episodes e "
            "JOIN podcasts p ON e.podcast_id = p.id "
            "LEFT JOIN episode_details ed ON e.id = ed.episode_id "
            f"WHERE (e.episode_id, p.slug) IN (VALUES {values_sql})",
            [v for pair in pairs for v in pair]
        ).fetchall()
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
        return len(insert_values)

    def _delete_indexed_episodes(self, conn, pairs: list[tuple[str, str]]) -> None:
        """Drop these episodes' search_index rows, resolving rowids with one MATCH.

        FTS5 pushes no constraint down for `(content_id, podcast_slug) IN (VALUES ...)`,
        so that predicate visits every stored row; a MATCH on content_id uses the index.
        """
        # unicode61 tokenizes on alphanumerics, so an id without one has no term to MATCH.
        matchable, unmatchable = [], []
        for pair in pairs:
            (matchable if any(c.isalnum() for c in pair[0]) else unmatchable).append(pair)
        if matchable:
            terms = ' OR '.join('content_id:"' + eid.replace('"', '""') + '"'
                                for eid, _ in matchable)
            hits = conn.execute(
                "SELECT rowid, content_id, podcast_slug FROM search_index "
                "WHERE search_index MATCH ?",
                (f'content_type:episode AND ({terms})',)
            ).fetchall()
            # A phrase can match a longer id, and two podcasts can share one, so verify both.
            wanted = set(matchable)
            rowids = [h['rowid'] for h in hits
                      if (h['content_id'], h['podcast_slug']) in wanted]
            if rowids:
                conn.execute(
                    "DELETE FROM search_index "  # noqa: S608
                    f"WHERE rowid IN ({','.join('?' * len(rowids))})",
                    rowids)
        if unmatchable:
            conn.execute(
                "DELETE FROM search_index WHERE content_type = 'episode' "  # noqa: S608
                f"AND (content_id, podcast_slug) IN (VALUES {','.join('(?,?)' for _ in unmatchable)})",
                [v for pair in unmatchable for v in pair])

    def _pick_snippet(self, row, *keys):
        """First of the named snippet columns FTS5 actually highlighted, sanitized."""
        for key in keys:
            value = row[key]
            if value and _HL_OPEN in value:
                return (self._sanitize_snippet(value)
                        .replace(_HL_OPEN, '<mark>').replace(_HL_CLOSE, '</mark>'))
        return None

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

    def search_grouped(self, query: str, limit: int = 50, groups: list[str] | None = None) -> dict:
        """Grouped search: shows, episodes, transcripts, patterns, sponsors, each an independent
        FTS query. All five keys are always present; a name outside groups (default: all) or a
        group whose query raised comes back empty."""
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
        wanted = set(SEARCH_GROUP_NAMES) if groups is None else set(groups)

        # Each group's MATCH repeats content_type so FTS5 intersects doclists instead of
        # scanning every posting for the term and filtering by content_type afterwards.
        # Each group is independent: one group's unexpected failure should not blank the others.
        group_fns = {
            'shows': lambda: self._search_shows(conn, fts_query, like_pattern, like_prefix, limit),
            'episodes': lambda: self._search_episodes(conn, fts_query, like_pattern, like_prefix, limit),
            'transcripts': lambda: self._search_transcripts(conn, fts_query, limit),
            'patterns': lambda: self._search_patterns(conn, fts_query, limit),
            'sponsors': lambda: self._search_sponsors(conn, fts_query, limit),
        }
        results = dict(empty)
        for name, fn in group_fns.items():
            if name not in wanted:
                continue
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
        # One token makes the phrase branch identical to the term branch; skip the duplicate OR.
        if len(tokens) == 1:
            return quoted[0]
        return f'"{clean_query}"* OR {" AND ".join(quoted)}'

    @staticmethod
    def _merge_fts_and_like(fts_rows, key_fn, row_builder, like_fetch, like_row_builder,
                            limit, like_when_empty=False):
        """FTS rows first, then a LIKE fallback for substring matches FTS tokenization
        misses; deduped by key_fn, capped at limit. like_when_empty holds the fallback
        back unless FTS found nothing, so a leading-wildcard scan of a large table does
        not run on nearly every search."""
        results = [row_builder(r) for r in fts_rows]
        seen = {key_fn(r) for r in fts_rows}
        run_like = not results if like_when_empty else len(results) < limit
        if run_like:
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
                   snippet(search_index, 3, char(2), char(3), '...', 64) AS title_snippet,
                   snippet(search_index, 4, char(2), char(3), '...', 64) AS body_snippet
            FROM search_index si
            JOIN podcasts p ON p.slug = si.content_id
            WHERE si.content_type = 'podcast'
              AND search_index MATCH 'content_type:podcast AND {title body}:(' || ? || ')'
            ORDER BY bm25(search_index, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
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
                                    'snippet': self._pick_snippet(r, 'body_snippet', 'title_snippet')},
            like_fetch=like_fetch,
            like_row_builder=lambda r: {'slug': r['slug'], 'title': r['title'], 'snippet': None},
            limit=limit)

    def _search_episodes(self, conn, fts_query, like_pattern, like_prefix, limit):
        """Episodes: FTS over title+description, plus a title LIKE pass, deduped by episode."""
        rows = conn.execute("""
            SELECT si.content_id AS episode_id, p.slug AS feed_slug,
                   COALESCE(p.title_override, p.title) AS feed_title,
                   e.title AS title, e.status AS status, e.published_at AS publish_date,
                   snippet(search_index, 3, char(2), char(3), '...', 64) AS title_snippet,
                   snippet(search_index, 5, char(2), char(3), '...', 64) AS meta_snippet
            FROM search_index si
            JOIN podcasts p ON p.slug = si.podcast_slug
            JOIN episodes e ON e.episode_id = si.content_id AND e.podcast_id = p.id
            WHERE si.content_type = 'episode'
              AND search_index MATCH 'content_type:episode AND {title metadata}:(' || ? || ')'
            ORDER BY bm25(search_index, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
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
                    'snippet': self._pick_snippet(r, 'meta_snippet', 'title_snippet')}

        def like_row_builder(r):
            return {'feedSlug': r['feed_slug'], 'feedTitle': r['feed_title'], 'episodeId': r['episode_id'],
                    'title': r['title'], 'status': r['status'], 'publishDate': r['publish_date'],
                    'snippet': None}

        # Episodes is the big table here, so its LIKE pass only runs on a total miss.
        return self._merge_fts_and_like(
            rows, key_fn=lambda r: (r['feed_slug'], r['episode_id']),
            row_builder=row_builder, like_fetch=like_fetch,
            like_row_builder=like_row_builder, limit=limit, like_when_empty=True)

    def _search_transcripts(self, conn, fts_query, limit):
        """Transcripts: body-only word matches. search_index holds one row per episode,
        so no episode can flood the group; timestamp is always None (no VTT offset)."""
        rows = conn.execute("""
            SELECT si.content_id AS episode_id, si.podcast_slug AS feed_slug, si.title AS title,
                   snippet(search_index, 4, char(2), char(3), '...', 64) AS body_snippet
            FROM search_index si
            WHERE si.content_type = 'episode' AND search_index MATCH 'content_type:episode AND body:(' || ? || ')'
            ORDER BY bm25(search_index, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
            LIMIT ?
        """, (fts_query, limit)).fetchall()
        return [{
            'feedSlug': r['feed_slug'], 'episodeId': r['episode_id'], 'title': r['title'],
            'snippet': self._pick_snippet(r, 'body_snippet'), 'timestamp': None,
        } for r in rows]

    def _search_patterns(self, conn, fts_query, limit):
        """Patterns: FTS over sponsor name + pattern text. Advanced-page-only group."""
        rows = conn.execute("""
            SELECT si.content_id AS id, si.podcast_slug AS scope, si.title AS sponsor,
                   snippet(search_index, 3, char(2), char(3), '...', 64) AS title_snippet,
                   snippet(search_index, 4, char(2), char(3), '...', 64) AS body_snippet
            FROM search_index si
            WHERE si.content_type = 'pattern'
              AND search_index MATCH 'content_type:pattern AND {title body}:(' || ? || ')'
            ORDER BY bm25(search_index, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
            LIMIT ?
        """, (fts_query, limit)).fetchall()
        return [{'id': r['id'], 'scope': r['scope'], 'sponsor': r['sponsor'],
                 'snippet': self._pick_snippet(r, 'body_snippet', 'title_snippet')} for r in rows]

    def _search_sponsors(self, conn, fts_query, limit):
        """Sponsors: FTS over name + aliases. Advanced-page-only group."""
        rows = conn.execute("""
            SELECT si.content_id AS id, si.title AS name,
                   snippet(search_index, 3, char(2), char(3), '...', 64) AS title_snippet,
                   snippet(search_index, 4, char(2), char(3), '...', 64) AS body_snippet
            FROM search_index si
            WHERE si.content_type = 'sponsor'
              AND search_index MATCH 'content_type:sponsor AND {title body}:(' || ? || ')'
            ORDER BY bm25(search_index, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
            LIMIT ?
        """, (fts_query, limit)).fetchall()
        return [{'id': r['id'], 'name': r['name'],
                 'snippet': self._pick_snippet(r, 'body_snippet', 'title_snippet')} for r in rows]

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
