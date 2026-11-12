"""Audio fingerprint match history mixin for MinusPod database."""
import json
import logging

from config import is_keep_like, is_pending_review
from utils.markers import MERGED_MEMBER_SPANS, finite_number, marker_stages
from utils.pattern_scope import pattern_passes_scope

logger = logging.getLogger(__name__)

SUMMARY_ID_CHUNK = 900

# A rejection covers a match only when they overlap by more than half the shorter span.
_OVERLAP_SQL = "(MIN(match_end, :end) - MAX(match_start, :start))"
_MOSTLY_OVERLAPS_SQL = (
    f"{_OVERLAP_SQL} * 2 > MIN(match_end - match_start, :end - :start)")

_ORIGINAL_RETAINED_SQL = """
    SELECT 1 FROM podcasts sp JOIN episodes se ON se.podcast_id = sp.id
    WHERE sp.slug = {slug} AND se.episode_id = {episode} AND se.original_file IS NOT NULL"""


def _is_detected_ad(marker: dict) -> bool:
    """Whether the marker was held, kept or cut, as the episode API buckets it."""
    if is_pending_review(marker) or is_keep_like(marker.get('action_applied')):
        return True
    return (marker.get('validation') or {}).get('decision') != 'REJECT' and marker.get('was_cut', True)


def _fingerprint_members(marker: dict) -> list[dict]:
    """Fingerprint members of one marker: its recorded members, else the marker itself."""
    spans = marker.get(MERGED_MEMBER_SPANS)
    if isinstance(spans, list):
        return [m for m in spans if isinstance(m, dict) and m.get('stage') == 'fingerprint']
    return [marker] if marker.get('detection_stage') == 'fingerprint' else []


def _member_bounds(member: dict) -> tuple[float, float] | None:
    """Measured match span of a member, falling back to its own span."""
    for lo_key, hi_key in (('fingerprint_match_start', 'fingerprint_match_end'), ('start', 'end')):
        lo, hi = finite_number(member.get(lo_key)), finite_number(member.get(hi_key))
        if lo is not None and hi is not None and hi > lo:
            return lo, hi
    return None


def _mostly_overlaps(lo_a: float, hi_a: float, lo_b: float, hi_b: float) -> bool:
    """Whether two spans overlap by more than half of the shorter one."""
    return (min(hi_a, hi_b) - max(lo_a, lo_b)) * 2 > min(hi_a - lo_a, hi_b - lo_b)


class FingerprintMatchMixin:
    """Per-pattern record of where a fingerprint matched, and which matches were rejected."""

    @staticmethod
    def _insert_fingerprint_match(conn, pattern_id, episode_id, podcast_slug, match_start, match_end,
                                  similarity, coverage, in_scope, audio_only) -> int:
        """Insert one match row on conn without committing."""
        cursor = conn.execute(
            """INSERT INTO audio_fingerprint_matches
                 (pattern_id, episode_id, podcast_slug, match_start, match_end,
                  similarity, coverage, in_scope, audio_only)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pattern_id, episode_id, podcast_slug, match_start, match_end,
             similarity, coverage, int(in_scope),
             None if audio_only is None else int(audio_only))
        )
        return cursor.lastrowid

    def record_fingerprint_match(self, pattern_id: int, episode_id: str, podcast_slug: str,
                                 match_start: float, match_end: float,
                                 similarity: float | None, coverage: float | None,
                                 in_scope: bool, audio_only: bool | None) -> int:
        """Insert one match row. Returns its ID."""
        conn = self.get_connection()
        row_id = self._insert_fingerprint_match(
            conn, pattern_id, episode_id, podcast_slug, match_start, match_end,
            similarity, coverage, in_scope, audio_only)
        conn.commit()
        return row_id

    def record_fingerprint_matches_from_markers(self, podcast_slug: str, episode_id: str,
                                                markers: list[dict]) -> int:
        """Replace an episode's match rows from its final markers, keeping rejected rows."""
        planned = []
        for marker in markers:
            if not _is_detected_ad(marker):
                continue
            audio_only = marker_stages(marker) == ['fingerprint']
            for member in _fingerprint_members(marker):
                pattern_id = member.get('pattern_id')
                bounds = _member_bounds(member)
                if pattern_id is None or bounds is None:
                    continue
                planned.append((pattern_id, bounds, member, audio_only))
        scope = self._episode_scope(podcast_slug) if planned else None
        patterns = self._patterns_for_scope({p[0] for p in planned})
        with self.transaction(immediate=True) as conn:
            rejected = conn.execute(
                """SELECT pattern_id, match_start, match_end FROM audio_fingerprint_matches
                   WHERE podcast_slug = ? AND episode_id = ? AND rejected = 1""",
                (podcast_slug, episode_id)
            ).fetchall()
            conn.execute(
                "DELETE FROM audio_fingerprint_matches "
                "WHERE podcast_slug = ? AND episode_id = ? AND rejected = 0",
                (podcast_slug, episode_id))
            written, seen = 0, set()
            for pattern_id, (lo, hi), member, audio_only in planned:
                pattern = patterns.get(pattern_id)
                if pattern is None or (pattern_id, lo, hi) in seen:
                    continue
                if any(r['pattern_id'] == pattern_id and _mostly_overlaps(r['match_start'], r['match_end'], lo, hi)
                       for r in rejected):
                    continue
                seen.add((pattern_id, lo, hi))
                in_scope = pattern_passes_scope(
                    pattern['scope'], pattern['network_id'], pattern['podcast_id'], pattern['source'],
                    pattern['sponsor_tags'], **scope)
                self._insert_fingerprint_match(
                    conn, pattern_id, episode_id, podcast_slug, lo, hi,
                    finite_number(member.get('fingerprint_similarity')),
                    finite_number(member.get('fingerprint_coverage')), in_scope, audio_only)
                written += 1
        return written

    def _episode_scope(self, podcast_slug: str) -> dict:
        """The scope identifiers the matchers use for this feed."""
        row = self.get_connection().execute(
            "SELECT tags FROM podcasts WHERE slug = ?", (podcast_slug,)).fetchone()
        try:
            tags = json.loads(row['tags']) if row and row['tags'] else []
        except (TypeError, ValueError):
            tags = []
        return {'podcast_id': podcast_slug,
                'network_id': self.get_effective_network_id(podcast_slug),
                'podcast_tag_set': set(tags) if isinstance(tags, list) else set()}

    def _patterns_for_scope(self, pattern_ids: set) -> dict:
        """Scope fields and sponsor tags of the given patterns that still exist."""
        if not pattern_ids:
            return {}
        tags_map = self.get_sponsor_tags_map()
        marks = ','.join('?' * len(pattern_ids))
        rows = self.get_connection().execute(
            f"SELECT id, scope, network_id, podcast_id, source, sponsor_id "  # noqa: S608
            f"FROM ad_patterns WHERE id IN ({marks})", sorted(pattern_ids)).fetchall()
        return {r['id']: {'scope': r['scope'], 'network_id': r['network_id'],
                          'podcast_id': r['podcast_id'], 'source': r['source'],
                          'sponsor_tags': set(tags_map.get(r['sponsor_id'], []))}
                for r in rows}

    def delete_fingerprint_matches_for_episode(self, podcast_slug: str, episode_id: str,
                                               keep_rejected: bool = False) -> int:
        """Delete an episode's match rows, optionally sparing rejected ones."""
        sql = ("DELETE FROM audio_fingerprint_matches "
               "WHERE podcast_slug = ? AND episode_id = ?")
        if keep_rejected:
            sql += " AND rejected = 0"
        conn = self.get_connection()
        cursor = conn.execute(sql, (podcast_slug, episode_id))
        conn.commit()
        return cursor.rowcount

    def delete_fingerprint_matches_for_pattern(self, pattern_id: int) -> int:
        """Delete every match row of a pattern."""
        conn = self.get_connection()
        cursor = conn.execute(
            "DELETE FROM audio_fingerprint_matches WHERE pattern_id = ?", (pattern_id,)
        )
        conn.commit()
        return cursor.rowcount

    def move_fingerprint_matches(self, from_pattern_id: int, to_pattern_id: int) -> int:
        """Re-point match rows from one pattern to another (pattern merge)."""
        conn = self.get_connection()
        cursor = conn.execute(
            "UPDATE audio_fingerprint_matches SET pattern_id = ? WHERE pattern_id = ?",
            (to_pattern_id, from_pattern_id)
        )
        conn.commit()
        return cursor.rowcount

    def get_fingerprint_matches(self, pattern_id: int, limit: int, offset: int) -> list[dict]:
        """A page of a pattern's matches, newest first."""
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT * FROM audio_fingerprint_matches WHERE pattern_id = ?
               ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?""",
            (pattern_id, limit, offset)
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_fingerprint_matches_with_episodes(self, pattern_id: int, limit: int, offset: int) -> list[dict]:
        """A page of matches, newest first, with episode_title and episode_original_file when the episode exists."""
        cursor = self.get_connection().execute(
            """SELECT m.*, e.title AS episode_title, e.original_file AS episode_original_file
               FROM audio_fingerprint_matches m
               LEFT JOIN podcasts p ON p.slug = m.podcast_slug
               LEFT JOIN episodes e ON e.podcast_id = p.id AND e.episode_id = m.episode_id
               WHERE m.pattern_id = ?
               ORDER BY m.created_at DESC, m.id DESC LIMIT ? OFFSET ?""",
            (pattern_id, limit, offset)
        )
        return [dict(row) for row in cursor.fetchall()]

    def get_retained_fingerprint_matches(self, pattern_id: int, limit: int) -> list[dict]:
        """Newest non-rejected matches whose episode still has original_file set."""
        cursor = self.get_connection().execute(
            """SELECT m.* FROM audio_fingerprint_matches m
               JOIN podcasts p ON p.slug = m.podcast_slug
               JOIN episodes e ON e.podcast_id = p.id AND e.episode_id = m.episode_id
               WHERE m.pattern_id = ? AND m.rejected = 0 AND e.original_file IS NOT NULL
               ORDER BY m.created_at DESC, m.id DESC LIMIT ?""",
            (pattern_id, limit)
        )
        return [dict(row) for row in cursor.fetchall()]

    def count_fingerprint_matches(self, pattern_id: int) -> int:
        """Number of match rows for a pattern."""
        conn = self.get_connection()
        return conn.execute(
            "SELECT COUNT(*) FROM audio_fingerprint_matches WHERE pattern_id = ?",
            (pattern_id,)
        ).fetchone()[0]

    def get_fingerprint_match(self, match_id: int) -> dict | None:
        """One match row by ID."""
        conn = self.get_connection()
        row = conn.execute(
            "SELECT * FROM audio_fingerprint_matches WHERE id = ?", (match_id,)
        ).fetchone()
        return dict(row) if row else None

    def _set_match_rejected(self, rejected: bool, pattern_id: int, podcast_slug: str,
                            episode_id: str, start: float, end: float) -> bool:
        """Set the rejected flag on the best overlapping match; unrejecting considers rejected rows only."""
        with self.transaction(immediate=True) as conn:
            row = conn.execute(
                f"""SELECT id FROM audio_fingerprint_matches
                    WHERE pattern_id = :pid AND podcast_slug = :slug AND episode_id = :ep
                      AND {_MOSTLY_OVERLAPS_SQL}
                      {'' if rejected else 'AND rejected = 1'}
                    ORDER BY {_OVERLAP_SQL} DESC, id DESC
                    LIMIT 1""",  # noqa: S608
                {'pid': pattern_id, 'slug': podcast_slug, 'ep': episode_id,
                 'start': start, 'end': end}
            ).fetchone()
            if row is None:
                return False
            if rejected:
                conn.execute(
                    """UPDATE audio_fingerprint_matches SET rejected = 1,
                         rejected_at = COALESCE(rejected_at, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
                       WHERE id = ?""",
                    (row['id'],))
            else:
                conn.execute(
                    "UPDATE audio_fingerprint_matches SET rejected = 0, rejected_at = NULL WHERE id = ?",
                    (row['id'],))
        return True

    def mark_fingerprint_match_rejected(self, pattern_id: int, podcast_slug: str,
                                        episode_id: str, start: float, end: float) -> bool:
        """Reject the match overlapping [start, end] most. True if one overlaps, even if already rejected."""
        return self._set_match_rejected(True, pattern_id, podcast_slug, episode_id, start, end)

    def unmark_fingerprint_match_rejected(self, pattern_id: int, podcast_slug: str,
                                          episode_id: str, start: float, end: float) -> bool:
        """Clear the rejection of the rejected match overlapping [start, end] most. True if one was cleared."""
        return self._set_match_rejected(False, pattern_id, podcast_slug, episode_id, start, end)

    def get_audio_summaries(self, pattern_ids: list[int] | None = None) -> dict[int, dict]:
        """Per-pattern {state, available, matchCount, meanSimilarity}, all patterns when ids is None."""
        if pattern_ids is None:
            return self._audio_summaries_chunk(None)
        summaries = {}
        ids = list(pattern_ids)
        for i in range(0, len(ids), SUMMARY_ID_CHUNK):
            summaries.update(self._audio_summaries_chunk(ids[i:i + SUMMARY_ID_CHUNK]))
        return summaries

    def _audio_summaries_chunk(self, ids: list[int] | None) -> dict[int, dict]:
        source_retained = _ORIGINAL_RETAINED_SQL.format(
            slug='af.source_podcast_slug', episode='af.source_episode_id')
        match_retained = _ORIGINAL_RETAINED_SQL.format(slug='m.podcast_slug', episode='m.episode_id')
        where, params = '', []
        if ids is not None:
            if not ids:
                return {}
            where = f"WHERE ap.id IN ({','.join('?' * len(ids))})"
            params = list(ids)
        rows = self.get_connection().execute(
            f"""SELECT ap.id AS pattern_id, af.id AS fingerprint_id, af.enabled,
                       COALESCE(ms.match_count, 0) AS match_count, ms.mean_similarity,
                       CASE WHEN af.id IS NULL THEN 0 ELSE
                         EXISTS({source_retained})
                         OR EXISTS(SELECT 1 FROM audio_fingerprint_matches m
                                   WHERE m.pattern_id = ap.id AND m.rejected = 0
                                     AND EXISTS({match_retained}))
                       END AS available
                FROM ad_patterns ap
                LEFT JOIN audio_fingerprints af ON af.pattern_id = ap.id
                LEFT JOIN (SELECT pattern_id, SUM(1 - rejected) AS match_count,
                                  AVG(CASE WHEN rejected = 0 THEN similarity END) AS mean_similarity
                           FROM audio_fingerprint_matches GROUP BY pattern_id) ms
                       ON ms.pattern_id = ap.id
                {where}""",  # noqa: S608
            params
        ).fetchall()
        return {r['pattern_id']: {
            'state': 'none' if r['fingerprint_id'] is None else ('active' if r['enabled'] else 'off'),
            'available': bool(r['available']),
            'matchCount': r['match_count'],
            'meanSimilarity': r['mean_similarity'],
        } for r in rows}

    def get_audio_match_summary(self, pattern_id: int) -> dict:
        """Match, out-of-scope and mean figures skip rejected rows, which are counted apart."""
        conn = self.get_connection()
        row = conn.execute(
            """SELECT COALESCE(SUM(1 - rejected), 0) AS match_count,
                      COALESCE(SUM(rejected), 0) AS rejected_count,
                      COALESCE(SUM(CASE WHEN rejected = 0 THEN 1 - in_scope ELSE 0 END), 0) AS out_of_scope_count,
                      AVG(CASE WHEN rejected = 0 THEN similarity END) AS mean_similarity,
                      MAX(CASE WHEN rejected = 0 THEN created_at END) AS last_matched_at
               FROM audio_fingerprint_matches WHERE pattern_id = ?""",
            (pattern_id,)
        ).fetchone()
        return dict(row)
