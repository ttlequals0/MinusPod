"""Cue detection telemetry mixin (#350 follow-up).

One row per template cue the matcher surfaced for an episode, recording the
match score and how detection used the cue (snap / pair / none / below_threshold)
plus the user's review verdict. Advisory only -- nothing here changes the cut
list. Two views read the same table: a per-feed advisory (judge a feed's cues
before enabling cue-pair) and a global aggregate (tune thresholds).

Phase 6 adds sub-threshold ``below_threshold`` rows plus per-cue
``edge_distance_s`` and ``unused_reason`` diagnostics. The advisory / aggregate
totals and confirm rate count only above-threshold rows (below_threshold is
survivorship-free telemetry, not a detection the user reviews); a separate
near-miss histogram and unused-reason breakdown surface the new rows.
"""
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)

_VALID_VERDICTS = ('pending', 'confirmed', 'rejected')
# App-layer guard; the DB CHECK was dropped for 'below_threshold'.
_VALID_OUTCOMES = ('snap', 'pair', 'none', 'below_threshold')

# Shared aggregate projection for the per-feed and global telemetry views.
# Static SQL (no user input) -- safe to interpolate into both queries. Totals,
# score stats, and confirm-rate inputs count only above-threshold rows via the
# WHERE clause the callers apply; below_threshold rows are reported separately.
_ADVISORY_AGGREGATE_SQL = """
    COUNT(*) AS total,
    SUM(outcome = 'snap') AS snapped,
    SUM(outcome = 'pair') AS paired,
    SUM(outcome = 'none') AS unused,
    SUM(verdict = 'confirmed') AS confirmed,
    SUM(verdict = 'rejected') AS rejected,
    SUM(verdict = 'pending') AS pending,
    AVG(match_score) AS avg_score,
    MIN(match_score) AS min_score,
    MAX(match_score) AS max_score
"""
# Above-threshold rows only: below_threshold near-misses are advisory telemetry,
# never a detection the totals/confirm-rate should count.
_ABOVE_THRESHOLD = "outcome != 'below_threshold'"


class CueDetectionMixin:
    """CRUD + aggregates for the cue_detections telemetry table."""

    def record_cue_detections(self, podcast_id: int, episode_id: str,
                              records: List[Dict]) -> int:
        """Replace this feed+episode's cue detections with ``records``.

        Delete-then-insert keeps the row set in sync with the latest processing
        run (scores and outcomes change on reprocess). Wrapped in a transaction
        so a mid-loop failure rolls back and the prior run's rows survive.
        Scoped by (podcast_id, episode_id) because episode_id (an RSS GUID) is
        only unique within a feed. Returns rows inserted.
        """
        with self.transaction() as conn:
            conn.execute(
                "DELETE FROM cue_detections WHERE podcast_id = ? AND episode_id = ?",
                (podcast_id, episode_id))
            for r in records:
                outcome = r.get('outcome', 'none')
                if outcome not in _VALID_OUTCOMES:
                    raise ValueError(f"invalid outcome: {outcome}")
                conn.execute(
                    """INSERT INTO cue_detections (
                           podcast_id, episode_id, template_id, label, cue_type,
                           role, source, start_s, end_s, match_score, confidence,
                           outcome, edge_distance_s, unused_reason
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        podcast_id, episode_id, r.get('template_id'), r.get('label'),
                        r.get('cue_type'), r.get('role'),
                        r.get('source', 'template'), r['start_s'], r['end_s'],
                        r.get('match_score'), r.get('confidence'),
                        outcome, r.get('edge_distance_s'), r.get('unused_reason'),
                    ),
                )
        return len(records)

    def list_cue_detections_for_episode(self, podcast_id: int,
                                        episode_id: str) -> List[Dict]:
        """All recorded cue detections for a feed+episode, earliest first."""
        conn = self.get_connection()
        rows = conn.execute(
            """SELECT id, template_id, label, cue_type, role, source,
                      start_s, end_s, match_score, confidence, outcome, verdict,
                      edge_distance_s, unused_reason
               FROM cue_detections WHERE podcast_id = ? AND episode_id = ?
               ORDER BY start_s ASC""",
            (podcast_id, episode_id),
        ).fetchall()
        return [dict(r) for r in rows]

    def set_cue_detection_verdict(self, detection_id: int, verdict: str) -> bool:
        """Set a detection's review verdict. Returns True if a row changed."""
        if verdict not in _VALID_VERDICTS:
            raise ValueError(f"invalid verdict: {verdict}")
        conn = self.get_connection()
        cursor = conn.execute(
            "UPDATE cue_detections SET verdict = ? WHERE id = ?",
            (verdict, detection_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    def cue_feed_advisory(self, podcast_id: int) -> Dict:
        """Per-feed cue health: outcome/verdict counts, score range, confirm rate.

        Counts above-threshold rows only; below_threshold near-misses are
        advisory and excluded from the totals.
        """
        conn = self.get_connection()
        row = conn.execute(
            f"SELECT {_ADVISORY_AGGREGATE_SQL} FROM cue_detections "
            f"WHERE podcast_id = ? AND {_ABOVE_THRESHOLD}",
            (podcast_id,),
        ).fetchone()
        return _advisory_dict(row)

    def cue_aggregate_stats(self) -> Dict:
        """Global cue telemetry: the feed-advisory shape plus histograms.

        Adds a match-score histogram (above-threshold rows), a near-miss
        histogram + total (below_threshold rows), and an unused-reason breakdown
        (outcome='none' rows). Totals/confirm-rate exclude below_threshold.
        """
        conn = self.get_connection()
        totals = conn.execute(
            f"SELECT {_ADVISORY_AGGREGATE_SQL} FROM cue_detections "
            f"WHERE {_ABOVE_THRESHOLD}"
        ).fetchone()
        buckets = conn.execute(
            f"""SELECT CAST(match_score * 10 AS INT) AS bucket, COUNT(*) AS n
               FROM cue_detections WHERE match_score IS NOT NULL AND {_ABOVE_THRESHOLD}
               GROUP BY bucket ORDER BY bucket"""
        ).fetchall()
        near_miss_buckets = conn.execute(
            """SELECT CAST(match_score * 10 AS INT) AS bucket, COUNT(*) AS n
               FROM cue_detections
               WHERE match_score IS NOT NULL AND outcome = 'below_threshold'
               GROUP BY bucket ORDER BY bucket"""
        ).fetchall()
        near_miss_total = conn.execute(
            "SELECT COUNT(*) FROM cue_detections WHERE outcome = 'below_threshold'"
        ).fetchone()[0]
        reason_rows = conn.execute(
            """SELECT unused_reason, COUNT(*) AS n FROM cue_detections
               WHERE outcome = 'none' AND unused_reason IS NOT NULL
               GROUP BY unused_reason"""
        ).fetchall()
        out = _advisory_dict(totals)
        out['scoreHistogram'] = _histogram(buckets)
        out['nearMissHistogram'] = _histogram(near_miss_buckets)
        out['nearMissTotal'] = near_miss_total
        out['unusedReasons'] = {r['unused_reason']: r['n'] for r in reason_rows}
        return out


def _histogram(buckets) -> List[Dict]:
    """Shape CAST(score*10) buckets into [{scoreFrom, count}] rows."""
    return [
        {'scoreFrom': round(b['bucket'] / 10, 1), 'count': b['n']}
        for b in buckets
    ]


def _advisory_dict(row) -> Dict:
    """Shape a COUNT/SUM aggregate row into the advisory JSON payload.

    A non-GROUP-BY COUNT/SUM always returns exactly one row, so ``row`` is never
    None; SUM/AVG/MIN/MAX still yield NULL on an empty table, hence the ``or 0``
    and ``is not None`` guards.
    """
    confirmed = row['confirmed'] or 0
    rejected = row['rejected'] or 0
    reviewed = confirmed + rejected
    return {
        'total': row['total'] or 0,
        'snapped': row['snapped'] or 0,
        'paired': row['paired'] or 0,
        'unused': row['unused'] or 0,
        'confirmed': confirmed,
        'rejected': rejected,
        'pending': row['pending'] or 0,
        'avgScore': round(row['avg_score'], 3) if row['avg_score'] is not None else None,
        'minScore': round(row['min_score'], 3) if row['min_score'] is not None else None,
        'maxScore': round(row['max_score'], 3) if row['max_score'] is not None else None,
        'confirmRate': round(confirmed / reviewed, 3) if reviewed else None,
    }
