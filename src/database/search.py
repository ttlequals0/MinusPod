"""Full-text search mixin for MinusPod database."""
import html
import fcntl
import logging
import os
import re
import threading
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Grouped-search snippet delimiters: literal "<mark>" in indexed text must not read as a highlight.
_HL_OPEN = '\x02'
_HL_CLOSE = '\x03'

# All groups search_grouped can compute; also the valid values for the /search groups= param.
SEARCH_GROUP_NAMES = ('shows', 'episodes', 'transcripts', 'patterns', 'sponsors')

# Episodes per indexing statement: two bound params each, plus one MATCH term each.
_INDEX_CHUNK = 500

# Rows per write transaction during a rebuild; bounds the lock hold when
# bodies run to 100k characters.
_REBUILD_TX_ROWS = 50

# One definition for the migration and the rebuild's shadow table.
SEARCH_INDEX_DDL = """CREATE VIRTUAL TABLE IF NOT EXISTS {name} USING fts5(
    content_type,
    content_id,
    podcast_slug,
    title,
    body,
    metadata,
    tokenize='porter unicode61'
)"""
_SHADOW_PREFIX = 'search_index_new'
_SHADOW_NAME_RE = re.compile(r'^search_index_new_[0-9]+_[0-9]+$')


@contextmanager
def _search_rebuild_lock(db_path):
    fd = os.open(f'{db_path}.search-rebuild.lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Search index rebuild already in progress') from exc
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

SEARCH_CHANGE_JOURNAL_DDL = """CREATE TABLE IF NOT EXISTS search_index_changes (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    content_type TEXT NOT NULL,
    content_id TEXT NOT NULL,
    podcast_slug TEXT NOT NULL DEFAULT '',
    changed_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
)"""

SEARCH_CHANGE_TRIGGERS_SQL = """
CREATE TRIGGER IF NOT EXISTS search_change_podcasts_insert AFTER INSERT ON podcasts BEGIN
  INSERT INTO search_index_changes(content_type, content_id, podcast_slug) VALUES ('podcast', NEW.slug, NEW.slug);
END;
CREATE TRIGGER IF NOT EXISTS search_change_podcasts_delete BEFORE DELETE ON podcasts BEGIN
  INSERT INTO search_index_changes(content_type, content_id, podcast_slug) VALUES ('podcast', OLD.slug, OLD.slug);
  INSERT INTO search_index_changes(content_type, content_id, podcast_slug)
    SELECT 'episode', episode_id, OLD.slug FROM episodes WHERE podcast_id = OLD.id;
END;
CREATE TRIGGER IF NOT EXISTS search_change_podcasts_update AFTER UPDATE ON podcasts
WHEN OLD.slug IS NOT NEW.slug OR OLD.title IS NOT NEW.title OR OLD.description IS NOT NEW.description BEGIN
  INSERT INTO search_index_changes(content_type, content_id, podcast_slug) VALUES ('podcast', OLD.slug, OLD.slug);
  INSERT INTO search_index_changes(content_type, content_id, podcast_slug) VALUES ('podcast', NEW.slug, NEW.slug);
  INSERT INTO search_index_changes(content_type, content_id, podcast_slug)
    SELECT 'episode', episode_id, OLD.slug FROM episodes
    WHERE podcast_id = NEW.id AND OLD.slug IS NOT NEW.slug;
  INSERT INTO search_index_changes(content_type, content_id, podcast_slug)
    SELECT 'episode', episode_id, NEW.slug FROM episodes
    WHERE podcast_id = NEW.id AND OLD.slug IS NOT NEW.slug;
END;
CREATE TRIGGER IF NOT EXISTS search_change_episodes_insert AFTER INSERT ON episodes BEGIN
  INSERT INTO search_index_changes(content_type, content_id, podcast_slug)
    SELECT 'episode', NEW.episode_id, slug FROM podcasts WHERE id = NEW.podcast_id;
END;
CREATE TRIGGER IF NOT EXISTS search_change_episodes_delete AFTER DELETE ON episodes BEGIN
  INSERT INTO search_index_changes(content_type, content_id, podcast_slug)
    SELECT 'episode', OLD.episode_id, slug FROM podcasts WHERE id = OLD.podcast_id;
END;
CREATE TRIGGER IF NOT EXISTS search_change_episodes_update AFTER UPDATE ON episodes
WHEN OLD.episode_id IS NOT NEW.episode_id
  OR OLD.podcast_id IS NOT NEW.podcast_id
  OR OLD.title IS NOT NEW.title
  OR OLD.description IS NOT NEW.description BEGIN
  INSERT INTO search_index_changes(content_type, content_id, podcast_slug)
    SELECT 'episode', OLD.episode_id, slug FROM podcasts WHERE id = OLD.podcast_id;
  INSERT INTO search_index_changes(content_type, content_id, podcast_slug)
    SELECT 'episode', NEW.episode_id, slug FROM podcasts WHERE id = NEW.podcast_id;
END;
CREATE TRIGGER IF NOT EXISTS search_change_details_insert AFTER INSERT ON episode_details BEGIN
  INSERT INTO search_index_changes(content_type, content_id, podcast_slug)
    SELECT 'episode', e.episode_id, p.slug FROM episodes e
    JOIN podcasts p ON p.id = e.podcast_id WHERE e.id = NEW.episode_id;
END;
CREATE TRIGGER IF NOT EXISTS search_change_details_delete AFTER DELETE ON episode_details BEGIN
  INSERT INTO search_index_changes(content_type, content_id, podcast_slug)
    SELECT 'episode', e.episode_id, p.slug FROM episodes e
    JOIN podcasts p ON p.id = e.podcast_id WHERE e.id = OLD.episode_id;
END;
CREATE TRIGGER IF NOT EXISTS search_change_details_update AFTER UPDATE ON episode_details
WHEN OLD.episode_id IS NOT NEW.episode_id
  OR OLD.transcript_text IS NOT NEW.transcript_text BEGIN
  INSERT INTO search_index_changes(content_type, content_id, podcast_slug)
    SELECT 'episode', e.episode_id, p.slug FROM episodes e
    JOIN podcasts p ON p.id = e.podcast_id WHERE e.id IN (OLD.episode_id, NEW.episode_id);
END;
CREATE TRIGGER IF NOT EXISTS search_change_patterns_insert AFTER INSERT ON ad_patterns BEGIN
  INSERT INTO search_index_changes(content_type, content_id, podcast_slug)
    VALUES ('pattern', CAST(NEW.id AS TEXT), COALESCE(NEW.scope, 'global'));
END;
CREATE TRIGGER IF NOT EXISTS search_change_patterns_delete AFTER DELETE ON ad_patterns BEGIN
  INSERT INTO search_index_changes(content_type, content_id, podcast_slug)
    VALUES ('pattern', CAST(OLD.id AS TEXT), COALESCE(OLD.scope, 'global'));
END;
CREATE TRIGGER IF NOT EXISTS search_change_patterns_update AFTER UPDATE ON ad_patterns
WHEN OLD.id IS NOT NEW.id OR OLD.text_template IS NOT NEW.text_template
  OR OLD.sponsor_id IS NOT NEW.sponsor_id OR OLD.scope IS NOT NEW.scope
  OR OLD.is_active IS NOT NEW.is_active BEGIN
  INSERT INTO search_index_changes(content_type, content_id, podcast_slug)
    VALUES ('pattern', CAST(OLD.id AS TEXT), COALESCE(OLD.scope, 'global'));
  INSERT INTO search_index_changes(content_type, content_id, podcast_slug)
    VALUES ('pattern', CAST(NEW.id AS TEXT), COALESCE(NEW.scope, 'global'));
END;
CREATE TRIGGER IF NOT EXISTS search_change_sponsors_insert AFTER INSERT ON known_sponsors BEGIN
  INSERT INTO search_index_changes(content_type, content_id, podcast_slug)
    VALUES ('sponsor', CAST(NEW.id AS TEXT), 'global');
END;
CREATE TRIGGER IF NOT EXISTS search_change_sponsors_delete AFTER DELETE ON known_sponsors BEGIN
  INSERT INTO search_index_changes(content_type, content_id, podcast_slug)
    VALUES ('sponsor', CAST(OLD.id AS TEXT), 'global');
END;
CREATE TRIGGER IF NOT EXISTS search_change_sponsors_update AFTER UPDATE ON known_sponsors
WHEN OLD.id IS NOT NEW.id OR OLD.name IS NOT NEW.name
  OR OLD.aliases IS NOT NEW.aliases OR OLD.is_active IS NOT NEW.is_active BEGIN
  INSERT INTO search_index_changes(content_type, content_id, podcast_slug)
    SELECT 'sponsor', CAST(OLD.id AS TEXT), 'global' WHERE OLD.id IS NOT NEW.id;
  INSERT INTO search_index_changes(content_type, content_id, podcast_slug)
    VALUES ('sponsor', CAST(NEW.id AS TEXT), 'global');
  INSERT INTO search_index_changes(content_type, content_id, podcast_slug)
    SELECT 'pattern', CAST(id AS TEXT), COALESCE(scope, 'global')
    FROM ad_patterns WHERE sponsor_id IN (OLD.id, NEW.id);
END;
"""


# search_index column order: content_type, content_id, podcast_slug, title, body, metadata.
_SNIPPET_COL = {'title': 3, 'body': 4, 'metadata': 5}
# Only the three text columns score; weighting content_type would rank by row length.
_BM25 = 'bm25(search_index, 0.0, 0.0, 0.0, 1.0, 1.0, 1.0)'


class SearchMixin:
    """Full-text search (FTS5) methods."""

    def rebuild_search_index(self) -> int:
        """Rebuild search under a process-shared single-writer lock."""
        shadow = f"{_SHADOW_PREFIX}_{os.getpid()}_{threading.get_ident()}"
        with _search_rebuild_lock(self.db_path):
            try:
                return self._rebuild_search_index_locked()
            finally:
                conn = self.get_connection()
                if conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                        (shadow,)).fetchone():
                    conn.execute(f'DROP TABLE IF EXISTS "{shadow}"')
                    conn.commit()

    def _rebuild_search_index_locked(self) -> int:
        """Rebuild the FTS5 search index from scratch.

        Indexes:
        - Episodes: title, description, transcript
        - Podcasts: title, description
        - Patterns: text, sponsor
        - Sponsors: name, aliases

        Returns count of indexed items.
        """
        conn = self.get_connection()
        self._drop_stale_shadows(conn)
        shadow = f"{_SHADOW_PREFIX}_{os.getpid()}_{threading.get_ident()}"
        with self.transaction(immediate=True) as tx:
            active = [row['name'] for row in tx.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name GLOB ?",
                (f'{_SHADOW_PREFIX}_[0-9]*_[0-9]*',),
            ) if _SHADOW_NAME_RE.fullmatch(row['name'])]
            if active:
                raise RuntimeError('Search index rebuild already in progress')
            tx.execute(SEARCH_INDEX_DDL.format(name=shadow))
            start_seq = tx.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM search_index_changes"
            ).fetchone()[0]

        insert = (f"INSERT INTO {shadow} (content_type, content_id, podcast_slug, title, body, metadata) "  # noqa: S608
                  "VALUES (?, ?, ?, ?, ?, ?)")
        try:
            for rows in self._search_source_chunks(conn):
                with self.transaction(immediate=True) as tx:
                    tx.executemany(insert, rows)
            with self.transaction(immediate=True) as tx:
                self._replay_search_changes(tx, shadow, insert, start_seq)
                applied_seq = tx.execute(
                    "SELECT COALESCE(MAX(seq), 0) FROM search_index_changes"
                ).fetchone()[0]
                tx.execute("DELETE FROM search_index_changes WHERE seq <= ?", (applied_seq,))
                tx.execute("DROP TABLE search_index")
                tx.execute(f'ALTER TABLE "{shadow}" RENAME TO search_index')
        except Exception:
            conn.rollback()
            conn.execute(f'DROP TABLE IF EXISTS "{shadow}"')
            conn.commit()
            raise

        count = conn.execute("SELECT COUNT(*) FROM search_index").fetchone()[0]
        logger.info(f"Search index rebuilt with {count} items")
        return count

    @staticmethod
    def _search_source_chunks(conn):
        sources = (
            ("SELECT slug, title, description FROM podcasts",
             lambda r: ('podcast', r['slug'], r['slug'], r['title'],
                        r['description'] or '', '')),
            ("""SELECT e.episode_id, e.title, e.description, p.slug, ed.transcript_text
                FROM episodes e JOIN podcasts p ON e.podcast_id = p.id
                LEFT JOIN episode_details ed ON e.id = ed.episode_id""",
             lambda r: ('episode', r['episode_id'], r['slug'], r['title'],
                        (r['transcript_text'] or '')[:100000], r['description'] or '')),
            ("""SELECT ap.id, ap.text_template, ks.name AS sponsor, ap.scope
                FROM ad_patterns ap LEFT JOIN known_sponsors ks ON ap.sponsor_id = ks.id
                WHERE ap.is_active = 1""",
             lambda r: ('pattern', str(r['id']), r['scope'] or 'global',
                        r['sponsor'] or 'Unknown', r['text_template'] or '', '')),
            ("SELECT id, name, aliases FROM known_sponsors WHERE is_active = 1",
             lambda r: ('sponsor', str(r['id']), 'global', r['name'],
                        r['aliases'] or '', '')),
        )
        for sql, shape in sources:
            cursor = conn.execute(sql)
            while batch := cursor.fetchmany(_REBUILD_TX_ROWS):
                yield [shape(row) for row in batch]

    @staticmethod
    def _replay_search_changes(conn, shadow, insert, start_seq):
        changes = conn.execute(
            "SELECT DISTINCT content_type, content_id, podcast_slug "
            "FROM search_index_changes WHERE seq > ?", (start_seq,)
        ).fetchall()
        for content_type, content_id, podcast_slug in changes:
            conn.execute(
                f"DELETE FROM {shadow} WHERE content_type = ? AND content_id = ? "  # noqa: S608
                "AND podcast_slug = ?", (content_type, content_id, podcast_slug))
            row = SearchMixin._current_search_row(
                conn, content_type, content_id, podcast_slug)
            if row is not None:
                conn.execute(insert, row)

    @staticmethod
    def _current_search_row(conn, content_type, content_id, podcast_slug):
        if content_type == 'podcast':
            row = conn.execute(
                "SELECT 'podcast', slug, slug, title, COALESCE(description, ''), '' "
                "FROM podcasts WHERE slug = ?", (content_id,)).fetchone()
        elif content_type == 'episode':
            row = conn.execute(
                "SELECT 'episode', e.episode_id, p.slug, e.title, "
                "substr(COALESCE(ed.transcript_text, ''), 1, 100000), "
                "COALESCE(e.description, '') FROM episodes e "
                "JOIN podcasts p ON p.id = e.podcast_id "
                "LEFT JOIN episode_details ed ON ed.episode_id = e.id "
                "WHERE e.episode_id = ? AND p.slug = ?",
                (content_id, podcast_slug)).fetchone()
        elif content_type == 'pattern':
            row = conn.execute(
                "SELECT 'pattern', CAST(ap.id AS TEXT), COALESCE(ap.scope, 'global'), "
                "COALESCE(ks.name, 'Unknown'), COALESCE(ap.text_template, ''), '' "
                "FROM ad_patterns ap LEFT JOIN known_sponsors ks ON ks.id = ap.sponsor_id "
                "WHERE ap.id = ? AND ap.is_active = 1 AND COALESCE(ap.scope, 'global') = ?",
                (content_id, podcast_slug)).fetchone()
        elif content_type == 'sponsor':
            row = conn.execute(
                "SELECT 'sponsor', CAST(id AS TEXT), 'global', name, COALESCE(aliases, ''), '' "
                "FROM known_sponsors WHERE id = ? AND is_active = 1", (content_id,)).fetchone()
        else:
            return None
        return tuple(row) if row is not None else None

    def _drop_stale_shadows(self, conn) -> None:
        """Remove shadows while the caller holds the exclusive rebuild lock."""
        names = [r['name'] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name GLOB ? "
            "AND sql LIKE 'CREATE VIRTUAL TABLE%USING fts5%'",
            (f"{_SHADOW_PREFIX}_[0-9]*_[0-9]*",))
            if _SHADOW_NAME_RE.fullmatch(r['name'])]
        dropped = 0
        for name in names:
            conn.execute(f'DROP TABLE IF EXISTS "{name}"')
            dropped += 1
        if dropped:
            conn.commit()
            logger.warning(f"Dropped {dropped} stale search index shadow table(s)")

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
        """Drop these episodes' search_index rows, resolving rowids with one MATCH: FTS5
        pushes no constraint down for (content_id, podcast_slug) IN (VALUES ...)."""
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
            # Chunked too: duplicate index rows for one pair would otherwise let the
            # rowid list outgrow the bound-variable limit a chunk of pairs stays under.
            for start in range(0, len(rowids), _INDEX_CHUNK):
                batch = rowids[start:start + _INDEX_CHUNK]
                conn.execute(
                    "DELETE FROM search_index "  # noqa: S608
                    f"WHERE rowid IN ({','.join('?' * len(batch))})",
                    batch)
        if unmatchable:
            conn.execute(
                "DELETE FROM search_index WHERE content_type = 'episode' "  # noqa: S608
                f"AND (content_id, podcast_slug) IN (VALUES {','.join('(?,?)' for _ in unmatchable)})",
                [v for pair in unmatchable for v in pair])

    def _pick_snippet(self, row, *keys):
        """First of the named snippet columns FTS5 highlighted, escaped before the
        sentinels become tags so a literal <mark> in indexed text stays text."""
        for key in keys:
            value = row[key]
            if value and _HL_OPEN in value:
                return (html.escape(value, quote=False)
                        .replace(_HL_OPEN, '<mark>').replace(_HL_CLOSE, '</mark>'))
        return None

    def search_grouped(self, query: str, limit: int = 50, groups: list[str] | None = None) -> dict:
        """Grouped search: shows, episodes, transcripts, patterns, sponsors, each an independent
        FTS query. All five keys are always present; a name outside groups (default: all), a
        group whose query raised, or a query under two characters comes back empty."""
        conn = self.get_connection()
        empty = {'shows': [], 'episodes': [], 'transcripts': [], 'patterns': [], 'sponsors': []}
        needle = query.strip()
        # A needle this short matches nearly everything and is not worth five FTS
        # queries plus the LIKE passes; the UI holds to the same minimum.
        if len(needle) < 2:
            return empty
        fts_query = self._safe_fts_query(query.replace('"', '""').strip())
        # Escape LIKE metacharacters so user input cannot widen the match
        escaped = needle.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        like_pattern = f'%{escaped}%'
        like_prefix = f'{escaped}%'
        wanted = set(SEARCH_GROUP_NAMES) if groups is None else set(groups)

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
        """FTS rows, then a LIKE pass for substrings FTS tokenization misses, deduped by
        key_fn and capped at limit. like_when_empty runs the pass only on a total miss."""
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

    def _fts_group(self, conn, content_type, cols, fts_query, limit, select=(), join=''):
        """One group's FTS pass over the named columns, with a snippet for each. The
        content_type inside the MATCH makes FTS5 intersect doclists rather than filter."""
        snippets = [
            f"snippet(search_index, {_SNIPPET_COL[col]}, char({ord(_HL_OPEN)}), "
            f"char({ord(_HL_CLOSE)}), '...', 64) AS {col}_snippet" for col in cols
        ]
        target = cols[0] if len(cols) == 1 else '{' + ' '.join(cols) + '}'
        return conn.execute(
            f"SELECT {', '.join(tuple(select) + tuple(snippets))} "  # noqa: S608
            f"FROM search_index si {join} "
            f"WHERE si.content_type = '{content_type}' "
            f"AND search_index MATCH 'content_type:{content_type} AND {target}:(' || ? || ')' "
            f"ORDER BY {_BM25} LIMIT ?",
            (fts_query, limit)
        ).fetchall()

    def _search_shows(self, conn, fts_query, like_pattern, like_prefix, limit):
        """Shows: FTS over title+description, plus a title LIKE pass for substrings."""
        rows = self._fts_group(
            conn, 'podcast', ('title', 'body'), fts_query, limit,
            select=('si.content_id AS slug',
                    'COALESCE(p.title_override, p.title) AS title'),
            join='JOIN podcasts p ON p.slug = si.content_id')

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
        rows = self._fts_group(
            conn, 'episode', ('title', 'metadata'), fts_query, limit,
            select=('si.content_id AS episode_id', 'p.slug AS feed_slug',
                    'COALESCE(p.title_override, p.title) AS feed_title',
                    'e.title AS title', 'e.status AS status',
                    'e.published_at AS publish_date'),
            join='JOIN podcasts p ON p.slug = si.podcast_slug '
                 'JOIN episodes e ON e.episode_id = si.content_id AND e.podcast_id = p.id')

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
                    'snippet': self._pick_snippet(r, 'metadata_snippet', 'title_snippet')}

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
        rows = self._fts_group(
            conn, 'episode', ('body',), fts_query, limit,
            select=('si.content_id AS episode_id', 'si.podcast_slug AS feed_slug',
                    'si.title AS title'))
        return [{
            'feedSlug': r['feed_slug'], 'episodeId': r['episode_id'], 'title': r['title'],
            'snippet': self._pick_snippet(r, 'body_snippet'), 'timestamp': None,
        } for r in rows]

    def _search_patterns(self, conn, fts_query, limit):
        """Patterns: FTS over sponsor name + pattern text. Advanced-page-only group."""
        rows = self._fts_group(
            conn, 'pattern', ('title', 'body'), fts_query, limit,
            select=('si.content_id AS id', 'si.podcast_slug AS scope', 'si.title AS sponsor'))
        return [{'id': r['id'], 'scope': r['scope'], 'sponsor': r['sponsor'],
                 'snippet': self._pick_snippet(r, 'body_snippet', 'title_snippet')} for r in rows]

    def _search_sponsors(self, conn, fts_query, limit):
        """Sponsors: FTS over name + aliases. Advanced-page-only group."""
        rows = self._fts_group(
            conn, 'sponsor', ('title', 'body'), fts_query, limit,
            select=('si.content_id AS id', 'si.title AS name'))
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
