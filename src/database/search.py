"""Full-text search mixin for MinusPod database."""
import logging
from typing import Optional, Dict, List

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

        # Index episodes with transcripts
        cursor = conn.execute("""
            SELECT e.episode_id, e.title, e.description, p.slug, ed.transcript_text
            FROM episodes e
            JOIN podcasts p ON e.podcast_id = p.id
            LEFT JOIN episode_details ed ON e.id = ed.episode_id
            WHERE e.status = 'processed'
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
        conn = self.get_connection()
        try:
            row = conn.execute("""
                SELECT e.episode_id, e.title, e.description, p.slug, ed.transcript_text
                FROM episodes e
                JOIN podcasts p ON e.podcast_id = p.id
                LEFT JOIN episode_details ed ON e.id = ed.episode_id
                WHERE e.episode_id = ? AND p.slug = ?
            """, (episode_id, slug)).fetchone()
            if not row:
                return False
            conn.execute(
                "DELETE FROM search_index WHERE content_type = 'episode' AND content_id = ?",
                (episode_id,))
            transcript = (row['transcript_text'] or '')[:100000]
            conn.execute("""
                INSERT INTO search_index (content_type, content_id, podcast_slug, title, body, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, ('episode', row['episode_id'], row['slug'], row['title'],
                  transcript, row['description'] or ''))
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to index episode {episode_id}: {e}")
            return False

    @staticmethod
    def _sanitize_snippet(snippet):
        """Sanitize FTS5 snippet HTML, allowing only <mark> highlight tags."""
        if not snippet:
            return snippet
        return nh3.clean(snippet, tags={"mark"}, attributes={})

    def search(self, query: str, content_type: Optional[str] = None, limit: int = 50) -> List[Dict]:
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

    def get_search_index_stats(self) -> Dict[str, int]:
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
