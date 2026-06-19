"""Cue detection telemetry mixin (#350 follow-up).

One row per template cue the matcher surfaced for an episode, recording the
match score and how detection used the cue (snap / pair / none) plus the user's
review verdict. Advisory only -- nothing here changes the cut list. Two views
read the same table: a per-feed advisory (judge a feed's cues before enabling
cue-pair) and a global aggregate (tune thresholds).
"""
import logging
from typing import Dict, List

logger = logging.getLogger(__name__)

_VALID_VERDICTS = ('pending', 'confirmed', 'rejected')

# Shared aggregate projection for the per-feed and global telemetry views.
# Static SQL (no user input) -- safe to interpolate into both queries.
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
                conn.execute(
                    """INSERT INTO cue_detections (
                           podcast_id, episode_id, template_id, label, cue_type,
                           role, source, start_s, end_s, match_score, confidence,
                           outcome
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        podcast_id, episode_id, r.get('template_id'), r.get('label'),
                        r.get('cue_type'), r.get('role'),
                        r.get('source', 'template'), r['start_s'], r['end_s'],
                        r.get('match_score'), r.get('confidence'),
                        r.get('outcome', 'none'),
                    ),
                )
        return len(records)

    def list_cue_detections_for_episode(self, podcast_id: int,
                                        episode_id: str) -> List[Dict]:
        """All recorded cue detections for a feed+episode, earliest first."""
        conn = self.get_connection()
        rows = conn.execute(
            """SELECT id, template_id, label, cue_type, role, source,
                      start_s, end_s, match_score, confidence, outcome, verdict
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
        """Per-feed cue health: outcome/verdict counts, score range, confirm rate."""
        conn = self.get_connection()
        row = conn.execute(
            f"SELECT {_ADVISORY_AGGREGATE_SQL} FROM cue_detections WHERE podcast_id = ?",
            (podcast_id,),
        ).fetchone()
        return _advisory_dict(row)

    def cue_aggregate_stats(self) -> Dict:
        """Global cue telemetry: the feed-advisory shape plus a score histogram."""
        conn = self.get_connection()
        totals = conn.execute(
            f"SELECT {_ADVISORY_AGGREGATE_SQL} FROM cue_detections"
        ).fetchone()
        buckets = conn.execute(
            """SELECT CAST(match_score * 10 AS INT) AS bucket, COUNT(*) AS n
               FROM cue_detections WHERE match_score IS NOT NULL
               GROUP BY bucket ORDER BY bucket"""
        ).fetchall()
        out = _advisory_dict(totals)
        out['scoreHistogram'] = [
            {'scoreFrom': round(b['bucket'] / 10, 1), 'count': b['n']}
            for b in buckets
        ]
        return out


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
