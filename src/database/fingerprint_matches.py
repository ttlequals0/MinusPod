"""Audio fingerprint match history mixin for MinusPod database."""
import json
import logging

from config import is_keep_like, is_pending_review
from database.fingerprint_cleanup import delete_matches_for_episode
from database.maintenance import _chunked
from database.stats import _date_range_clauses
from utils.json_fields import load_json_list
from utils.markers import MERGED_MEMBER_SPANS, finite_number, marker_stages, parse_ad_markers
from utils.pattern_scope import pattern_passes_scope
from utils.time import ISO_FORMAT, overlap_seconds, parse_iso_utc, utc_now_iso

logger = logging.getLogger(__name__)

SUMMARY_ID_CHUNK = 900
BACKFILL_GATE = 'backfill_fingerprint_matches_v1'
# A stored span is taken as a fingerprint's origin only when it is this close to the fingerprint's length.
SOURCE_LENGTH_RATIO = (0.5, 2.0)
OUT_OF_SCOPE_LOG_CAP = 50

# A rejection covers a match only when they overlap by more than half the shorter span.
# _mostly_overlaps is the Python twin of _MOSTLY_OVERLAPS_SQL: the two must agree.
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


def fingerprint_member_pattern_ids(marker: dict) -> list[int]:
    """Distinct pattern ids of a marker's fingerprint members, in order."""
    ids = (m.get('pattern_id') for m in _fingerprint_members(marker))
    return list(dict.fromkeys(pid for pid in ids if pid is not None))


def _member_bounds(member: dict) -> tuple[float, float] | None:
    """Measured match span of a member, falling back to its own span."""
    for lo_key, hi_key in (('fingerprint_match_start', 'fingerprint_match_end'), ('start', 'end')):
        lo, hi = finite_number(member.get(lo_key)), finite_number(member.get(hi_key))
        if lo is not None and hi is not None and hi > lo:
            return lo, hi
    return None


def _mostly_overlaps(lo_a: float, hi_a: float, lo_b: float, hi_b: float) -> bool:
    """Whether two spans overlap by more than half of the shorter one."""
    return overlap_seconds(lo_a, hi_a, lo_b, hi_b) * 2 > min(hi_a - lo_a, hi_b - lo_b)


def _iso_or(value, fallback):
    """A timestamp normalized to the stored UTC form, else the fallback."""
    moment = parse_iso_utc(value)
    return moment.strftime(ISO_FORMAT) if moment else fallback


def _planned_members(marker: dict) -> list[tuple]:
    """(pattern_id, (lo, hi), member, audio_only) for each usable fingerprint member of a marker."""
    audio_only = marker_stages(marker) == ['fingerprint']
    planned = []
    for member in _fingerprint_members(marker):
        pattern_id = member.get('pattern_id')
        bounds = _member_bounds(member)
        if pattern_id is not None and bounds is not None:
            planned.append((pattern_id, bounds, member, audio_only))
    return planned


def plan_fingerprint_matches(markers: list[dict]) -> list[tuple]:
    """(pattern_id, (lo, hi), member, audio_only) for each fingerprint member of a detected ad."""
    return [p for marker in markers if _is_detected_ad(marker) for p in _planned_members(marker)]


def plan_demoted_fingerprint_matches(markers: list[dict]) -> list[tuple]:
    """The same tuples for markers that are no longer detected ads, such as a rejected held marker."""
    return [p for marker in markers if not _is_detected_ad(marker) for p in _planned_members(marker)]


def fingerprint_match_rows(planned: list[tuple], patterns: dict, scope: dict | None,
                           rejected=()) -> list[tuple]:
    """(pattern_id, lo, hi, similarity, coverage, in_scope, audio_only) to store, one per distinct span."""
    rows, seen = [], set()
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
        rows.append((pattern_id, lo, hi, finite_number(member.get('fingerprint_similarity')),
                     finite_number(member.get('fingerprint_coverage')), in_scope, audio_only))
    return rows


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
        planned = plan_fingerprint_matches(markers)
        scope = self._episode_scope(podcast_slug) if planned else None
        patterns = self._patterns_for_scope({p[0] for p in planned})
        if not fingerprint_match_rows(planned, patterns, scope) and self.get_connection().execute(
                "SELECT 1 FROM audio_fingerprint_matches WHERE podcast_slug = ? AND episode_id = ?",
                (podcast_slug, episode_id)).fetchone() is None:
            return 0
        with self.transaction(immediate=True) as conn:
            rejected = conn.execute(
                """SELECT pattern_id, match_start, match_end FROM audio_fingerprint_matches
                   WHERE podcast_slug = ? AND episode_id = ? AND rejected = 1""",
                (podcast_slug, episode_id)
            ).fetchall()
            delete_matches_for_episode(conn, podcast_slug, episode_id, keep_rejected=True)
            rows = fingerprint_match_rows(planned, patterns, scope, rejected)
            for pattern_id, lo, hi, similarity, coverage, in_scope, audio_only in rows:
                self._insert_fingerprint_match(
                    conn, pattern_id, episode_id, podcast_slug, lo, hi,
                    similarity, coverage, in_scope, audio_only)
        return len(rows)

    def _episode_scope(self, podcast_slug: str) -> dict:
        """The scope identifiers the matchers use for this feed."""
        row = self.get_connection().execute(
            "SELECT tags FROM podcasts WHERE slug = ?", (podcast_slug,)).fetchone()
        return {'podcast_id': podcast_slug,
                'network_id': self.get_effective_network_id(podcast_slug),
                'podcast_tag_set': set(load_json_list(row['tags'] if row else None))}

    def _patterns_for_scope(self, pattern_ids: set) -> dict:
        """Scope fields and sponsor tags of the given patterns that still exist."""
        if not pattern_ids:
            return {}
        tags_map = self.get_sponsor_tags_map()
        ids = sorted(pattern_ids)
        found = {}
        for chunk in _chunked(ids, SUMMARY_ID_CHUNK):
            rows = self.get_connection().execute(
                f"SELECT id, scope, network_id, podcast_id, source, sponsor_id "  # noqa: S608
                f"FROM ad_patterns WHERE id IN ({','.join('?' * len(chunk))})", chunk).fetchall()
            for r in rows:
                found[r['id']] = {'scope': r['scope'], 'network_id': r['network_id'],
                                  'podcast_id': r['podcast_id'], 'source': r['source'],
                                  'sponsor_tags': set(tags_map.get(r['sponsor_id'], []))}
        return found

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

    def unreject_fingerprint_matches_in_span(self, podcast_slug: str, episode_id: str,
                                             start: float, end: float) -> int:
        """Clear the rejection of every rejected match of any pattern that mostly overlaps [start, end]."""
        with self.transaction(immediate=True) as conn:
            return conn.execute(
                f"""UPDATE audio_fingerprint_matches SET rejected = 0, rejected_at = NULL
                    WHERE podcast_slug = :slug AND episode_id = :ep AND rejected = 1
                      AND {_MOSTLY_OVERLAPS_SQL}""",  # noqa: S608
                {'slug': podcast_slug, 'ep': episode_id, 'start': start, 'end': end}).rowcount

    def get_audio_summaries(self, pattern_ids: list[int]) -> dict[int, dict]:
        """Per-pattern {state, available, matchCount, meanSimilarity} for the given ids."""
        summaries = {}
        for chunk in _chunked(list(pattern_ids), SUMMARY_ID_CHUNK):
            summaries.update(self._audio_summaries_chunk(chunk))
        return summaries

    def _audio_summaries_chunk(self, ids: list[int]) -> dict[int, dict]:
        source_retained = _ORIGINAL_RETAINED_SQL.format(
            slug='af.source_podcast_slug', episode='af.source_episode_id')
        match_retained = _ORIGINAL_RETAINED_SQL.format(slug='m.podcast_slug', episode='m.episode_id')
        marks = ','.join('?' * len(ids))
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
                           FROM audio_fingerprint_matches WHERE pattern_id IN ({marks})
                           GROUP BY pattern_id) ms
                       ON ms.pattern_id = ap.id
                WHERE ap.id IN ({marks})""",  # noqa: S608
            [*ids, *ids]
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

    def get_audio_matching_stats(self, *, from_date=None, to_date=None, podcast_slug=None) -> dict:
        """Match history counts for the period and the active patterns with audio, per feed when given."""
        conn = self.get_connection()
        if podcast_slug and conn.execute(
                "SELECT 1 FROM podcasts WHERE slug = ?", (podcast_slug,)).fetchone() is None:
            return {'patternsWithAudio': 0, 'patternsTotal': 0, 'audioMatches': 0,
                    'foundByAudioOnly': 0, 'notThisAd': 0}
        feed_sql, feed_params = ('', [])
        if podcast_slug:
            feed_sql, feed_params = ' AND podcast_slug = ?', [podcast_slug]
        created, created_params = _date_range_clauses('created_at', from_date, to_date)
        created_sql = ''.join(f' AND {clause}' for clause in created)
        rejected, rejected_params = _date_range_clauses('rejected_at', from_date, to_date)
        rejected_sql = ''.join(f' AND {clause}' for clause in rejected)
        matches = conn.execute(
            f"""SELECT COUNT(*) AS matches, COALESCE(SUM(audio_only = 1), 0) AS audio_only
                FROM audio_fingerprint_matches
                WHERE rejected = 0{feed_sql}{created_sql}""",  # noqa: S608
            feed_params + created_params).fetchone()
        not_this_ad = conn.execute(
            f"""SELECT COUNT(*) FROM audio_fingerprint_matches
                WHERE rejected = 1{feed_sql}{rejected_sql}""",  # noqa: S608
            feed_params + rejected_params).fetchone()[0]
        total, with_audio = self._count_patterns_with_audio(podcast_slug)
        return {'patternsWithAudio': with_audio, 'patternsTotal': total,
                'audioMatches': matches['matches'], 'foundByAudioOnly': matches['audio_only'],
                'notThisAd': not_this_ad}

    def _count_patterns_with_audio(self, podcast_slug: str | None) -> tuple[int, int]:
        """(active patterns, those with an enabled fingerprint), limited to a feed's scope when given."""
        rows = self.get_connection().execute(
            """SELECT ap.id, ap.scope, ap.network_id, ap.podcast_id, ap.source, ap.sponsor_id,
                      EXISTS(SELECT 1 FROM audio_fingerprints af
                             WHERE af.pattern_id = ap.id AND af.enabled = 1) AS has_audio
               FROM ad_patterns ap WHERE ap.is_active = 1""").fetchall()
        if podcast_slug:
            scope = self._episode_scope(podcast_slug)
            tags_map = self.get_sponsor_tags_map()
            rows = [r for r in rows if pattern_passes_scope(
                r['scope'], r['network_id'], r['podcast_id'], r['source'],
                set(tags_map.get(r['sponsor_id'], [])), **scope)]
        return len(rows), sum(r['has_audio'] for r in rows)

    def backfill_fingerprint_matches_from_markers(self) -> tuple[int, list[int]]:
        """Write match history from stored markers and set the migration mark in one transaction.

        Returns the row count and the ids of patterns that got out-of-scope rows.
        """
        inserts = self._read_stored_match_history()
        with self.transaction(immediate=True) as conn:
            done = {(r[0], r[1]) for r in conn.execute(
                "SELECT DISTINCT podcast_slug, episode_id FROM audio_fingerprint_matches")}
            written = [row for row in inserts if (row[2], row[1]) not in done]
            conn.executemany(
                """INSERT INTO audio_fingerprint_matches
                     (pattern_id, episode_id, podcast_slug, match_start, match_end, similarity,
                      coverage, in_scope, audio_only, rejected, rejected_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", written)
            conn.executemany(
                """UPDATE audio_fingerprints SET source_podcast_slug = ?, source_episode_id = ?,
                     source_start = ?, source_end = ?
                   WHERE pattern_id = ? AND source_episode_id IS NULL""",
                [(*source, pattern_id) for pattern_id, source in self._origin_sources(written).items()])
            conn.execute("INSERT OR IGNORE INTO schema_migrations (name) VALUES (?)", (BACKFILL_GATE,))
        return len(written), sorted({row[0] for row in written if not row[7]})

    def _read_stored_match_history(self) -> list[tuple]:
        """Rows to insert from every stored marker list. The caller must not hold an open transaction."""
        conn = self.get_connection()
        now = utc_now_iso()
        rejections = self._stored_rejections(now)
        planned_episodes = []
        # Only a marker list that names the fingerprint stage can hold a fingerprint member.
        cursor = conn.execute(
            """SELECT e.id AS row_id, e.episode_id, p.slug, e.processed_at, d.ad_markers_json
               FROM episode_details d
               JOIN episodes e ON e.id = d.episode_id
               JOIN podcasts p ON p.id = e.podcast_id
               WHERE d.ad_markers_json LIKE '%"fingerprint"%'
               ORDER BY e.id""")
        for row in cursor:
            markers = parse_ad_markers(row['ad_markers_json'])
            if markers is None:
                logger.warning("Skipped unreadable stored markers of episode row %s "
                               "in the fingerprint match backfill", row['row_id'])
                continue
            planned = []
            for marker in markers:
                planned += self._planned_with_rejections(marker, row['slug'], row['episode_id'], rejections)
            if planned:
                planned_episodes.append((row['slug'], row['episode_id'],
                                         _iso_or(row['processed_at'], now), planned))
        patterns = self._patterns_for_scope({p[0] for _, _, _, planned in planned_episodes for p in planned})
        scopes, inserts = {}, []
        for slug, episode_id, created_at, planned in planned_episodes:
            if slug not in scopes:
                scopes[slug] = self._episode_scope(slug)
            for pattern_id, lo, hi, similarity, coverage, in_scope, audio_only in fingerprint_match_rows(
                    planned, patterns, scopes[slug]):
                rejected_at = next((at for rlo, rhi, at in rejections.get((pattern_id, slug, episode_id), ())
                                    if _mostly_overlaps(rlo, rhi, lo, hi)), None)
                inserts.append((pattern_id, episode_id, slug, lo, hi, similarity, coverage, int(in_scope),
                                int(audio_only), int(rejected_at is not None), rejected_at, created_at))
        return inserts

    @staticmethod
    def _planned_with_rejections(marker: dict, slug: str, episode_id: str, rejections: dict) -> list[tuple]:
        """A marker's planned members; a rejection of any member's pattern covers all of them.

        A demoted marker keeps only members that majority-overlap a rejection. Adds the shared spans to
        rejections so the row loop marks each member rejected.
        """
        planned = plan_fingerprint_matches([marker]) or plan_demoted_fingerprint_matches([marker])
        demoted = not plan_fingerprint_matches([marker])
        group = {p[0] for p in planned} | {marker.get('pattern_id')}
        spans = [span for pid in group for span in rejections.get((pid, slug, episode_id), ())]
        if not spans:
            return [] if demoted else planned
        for pid in group:
            if pid is not None:
                rejections[(pid, slug, episode_id)] = list(dict.fromkeys(
                    [*rejections.get((pid, slug, episode_id), ()), *spans]))
        if demoted:
            return [p for p in planned if any(_mostly_overlaps(lo, hi, *p[1]) for lo, hi, _ in spans)]
        return planned

    def _stored_rejections(self, fallback_at: str) -> dict:
        """{(pattern_id, slug, episode_id): [(start, end, rejected_at)]} from false-positive corrections, oldest first.

        A correction with an unreadable timestamp is stamped fallback_at.
        """
        found = {}
        rows = self.get_connection().execute(
            """SELECT c.pattern_id, p.slug, c.episode_id, c.original_bounds, c.created_at
               FROM pattern_corrections c JOIN podcasts p ON p.id = c.podcast_id
               WHERE c.correction_type = 'false_positive' AND c.pattern_id IS NOT NULL
                 AND c.original_bounds IS NOT NULL
               ORDER BY c.created_at, c.id""")
        for row in rows:
            try:
                bounds = json.loads(row['original_bounds'])
            except (TypeError, ValueError):
                continue
            lo = finite_number(bounds.get('start')) if isinstance(bounds, dict) else None
            hi = finite_number(bounds.get('end')) if isinstance(bounds, dict) else None
            if lo is None or hi is None or hi <= lo:
                continue
            found.setdefault((row['pattern_id'], row['slug'], row['episode_id']), []).append(
                (lo, hi, _iso_or(row['created_at'], fallback_at)))
        return found

    def _origin_sources(self, rows: list[tuple]) -> dict:
        """{pattern_id: (slug, episode_id, start, end)} for fingerprints whose origin episode holds a match."""
        origins = self.get_connection().execute(
            """SELECT af.pattern_id, af.duration, ap.created_from_episode_id AS episode_id,
                      ap.podcast_id
               FROM audio_fingerprints af JOIN ad_patterns ap ON ap.id = af.pattern_id
               WHERE af.source_episode_id IS NULL AND ap.created_from_episode_id IS NOT NULL
                 AND ap.created_from_episode_id != ''""").fetchall()
        by_pattern = {}
        for row in rows:
            if not row[9]:
                by_pattern.setdefault(row[0], []).append(row)
        sources = {}
        for origin in origins:
            low, high = SOURCE_LENGTH_RATIO
            candidates = sorted(
                (r for r in by_pattern.get(origin['pattern_id'], ())
                 if r[1] == origin['episode_id']
                 and (not origin['podcast_id'] or r[2] == origin['podcast_id'])
                 and origin['duration']
                 and low <= (r[4] - r[3]) / origin['duration'] <= high),
                key=lambda r: (r[11], r[3]))
            # Without an owning feed, an episode id found in several feeds does not name one origin.
            if candidates and (origin['podcast_id'] or len({r[2] for r in candidates}) == 1):
                best = candidates[0]
                sources[origin['pattern_id']] = (best[2], best[1], best[3], best[4])
        return sources
