"""Ad patterns and corrections mixin for MinusPod database."""
import json
import logging
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)


class PatternMixin:
    """Ad pattern and correction management methods."""

    def get_ad_patterns(self, scope: str = None, podcast_id: str = None,
                        network_id: str = None, active_only: bool = True,
                        source: str = None) -> List[Dict]:
        """Get ad patterns with optional filtering. Includes podcast_name when available."""
        conn = self.get_connection()

        # Join with podcasts for podcast name + known_sponsors for sponsor name
        # (podcast_id stores slugs since v0.1.194; sponsor moved to FK in v2.2.0)
        query = """
            SELECT ap.*, ks.name AS sponsor,
                   p.title as podcast_name, p.slug as podcast_slug
            FROM ad_patterns ap
            LEFT JOIN podcasts p ON ap.podcast_id = p.slug
            LEFT JOIN known_sponsors ks ON ap.sponsor_id = ks.id
            WHERE 1=1
        """
        params = []

        if active_only:
            query += " AND ap.is_active = 1"
        if scope:
            query += " AND ap.scope = ?"
            params.append(scope)
        if podcast_id:
            query += " AND ap.podcast_id = ?"
            params.append(podcast_id)
        if network_id:
            query += " AND ap.network_id = ?"
            params.append(network_id)
        if source:
            query += " AND ap.source = ?"
            params.append(source)

        query += " ORDER BY ap.created_at DESC"

        cursor = conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def find_patterns_by_community_ids(self, community_ids: List[str]) -> Dict[str, Dict]:
        """Batch lookup: return {community_id: pattern_row} for the given ids.

        Avoids the N+1 pattern when applying a manifest.
        """
        if not community_ids:
            return {}
        conn = self.get_connection()
        placeholders = ','.join('?' * len(community_ids))
        cursor = conn.execute(
            f"SELECT * FROM ad_patterns WHERE community_id IN ({placeholders})",
            community_ids,
        )
        return {row['community_id']: dict(row) for row in cursor.fetchall()}

    def active_pattern_exists_for_sponsor(self, sponsor: str) -> bool:
        """Return True if any active ad_patterns row exists for this sponsor (case-insensitive)."""
        if not sponsor:
            return False
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT 1 FROM ad_patterns WHERE is_active = 1 "
            "AND lower(sponsor) = lower(?) LIMIT 1",
            (sponsor,)
        )
        return cursor.fetchone() is not None

    def get_active_pattern_sponsors(self) -> set:
        """Return a lowercase set of sponsor names with active patterns.

        Preloaded once per detection pass so Gate B sponsor-is-known checks
        avoid N+1 queries over the loop.
        """
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT DISTINCT LOWER(ks.name) AS s
               FROM ad_patterns ap
               JOIN known_sponsors ks ON ap.sponsor_id = ks.id
               WHERE ap.is_active = 1"""
        )
        return {row['s'] for row in cursor.fetchall() if row['s']}

    def get_ad_pattern_by_id(self, pattern_id: int) -> Optional[Dict]:
        """Get a single ad pattern by ID with podcast info."""
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT ap.*, ks.name AS sponsor,
                      p.title as podcast_name, p.slug as podcast_slug
               FROM ad_patterns ap
               LEFT JOIN podcasts p ON ap.podcast_id = p.slug
               LEFT JOIN known_sponsors ks ON ap.sponsor_id = ks.id
               WHERE ap.id = ?""",
            (pattern_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def find_pattern_by_text(self, text_template: str, podcast_id: str = None) -> Optional[Dict]:
        """Find an existing pattern with the same text_template (for deduplication)."""
        conn = self.get_connection()
        if podcast_id:
            cursor = conn.execute(
                """SELECT ap.*, ks.name AS sponsor
                   FROM ad_patterns ap
                   LEFT JOIN known_sponsors ks ON ap.sponsor_id = ks.id
                   WHERE ap.text_template = ? AND ap.podcast_id = ?""",
                (text_template, podcast_id)
            )
        else:
            cursor = conn.execute(
                """SELECT ap.*, ks.name AS sponsor
                   FROM ad_patterns ap
                   LEFT JOIN known_sponsors ks ON ap.sponsor_id = ks.id
                   WHERE ap.text_template = ? AND ap.podcast_id IS NULL""",
                (text_template,)
            )
        row = cursor.fetchone()
        return dict(row) if row else None

    def create_ad_pattern(self, scope: str, text_template: str = None,
                          sponsor_id: int = None, podcast_id: str = None,
                          network_id: str = None, dai_platform: str = None,
                          intro_variants: List[str] = None,
                          outro_variants: List[str] = None,
                          created_from_episode_id: str = None,
                          duration: float = None,
                          created_by: str = 'auto',
                          source: str = 'local',
                          community_id: str = None,
                          version: int = 1,
                          submitted_app_version: str = None,
                          protected_from_sync: int = 0) -> int:
        """Create a new ad pattern. Returns pattern ID."""
        conn = self.get_connection()
        cursor = conn.execute(
            """INSERT INTO ad_patterns
               (scope, text_template, sponsor_id, podcast_id, network_id, dai_platform,
                intro_variants, outro_variants, created_from_episode_id,
                avg_duration, duration_samples, created_by,
                source, community_id, version, submitted_app_version, protected_from_sync)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (scope, text_template, sponsor_id, podcast_id, network_id, dai_platform,
             json.dumps(intro_variants or []), json.dumps(outro_variants or []),
             created_from_episode_id,
             duration, 1 if duration is not None else 0,
             created_by,
             source, community_id, version, submitted_app_version, protected_from_sync)
        )
        conn.commit()
        return cursor.lastrowid

    def update_ad_pattern(self, pattern_id: int, **kwargs) -> bool:
        """Update an ad pattern."""
        conn = self.get_connection()

        fields = []
        values = []
        for key, value in kwargs.items():
            if key in ('scope', 'text_template', 'sponsor_id', 'podcast_id', 'network_id',
                       'dai_platform', 'confirmation_count', 'false_positive_count',
                       'last_matched_at', 'is_active', 'disabled_at', 'disabled_reason',
                       'avg_duration', 'duration_samples', 'created_by',
                       'source', 'community_id', 'version', 'submitted_app_version',
                       'protected_from_sync'):
                fields.append(f"{key} = ?")
                values.append(value)
            elif key in ('intro_variants', 'outro_variants'):
                fields.append(f"{key} = ?")
                values.append(json.dumps(value) if isinstance(value, list) else value)

        if not fields:
            return False

        values.append(pattern_id)
        conn.execute(
            f"UPDATE ad_patterns SET {', '.join(fields)} WHERE id = ?",
            values
        )
        conn.commit()
        return True

    def find_pattern_by_community_id(self, community_id: str) -> Optional[Dict]:
        """Find a pattern by its community_id. Returns dict or None."""
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT * FROM ad_patterns WHERE community_id = ?",
            (community_id,)
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_patterns_by_source(self, source: str, active_only: bool = True) -> List[Dict]:
        """List ad patterns filtered by source ('local'|'community'|'imported')."""
        conn = self.get_connection()
        query = "SELECT * FROM ad_patterns WHERE source = ?"
        params: List = [source]
        if active_only:
            query += " AND is_active = 1"
        query += " ORDER BY id"
        cursor = conn.execute(query, params)
        return [dict(row) for row in cursor.fetchall()]

    def set_pattern_protected(self, pattern_id: int, protected: bool) -> bool:
        """Toggle the protected_from_sync flag on a pattern."""
        conn = self.get_connection()
        cursor = conn.execute(
            "UPDATE ad_patterns SET protected_from_sync = ? WHERE id = ?",
            (1 if protected else 0, pattern_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    def bulk_delete_patterns(self, ids: List[int]) -> int:
        """Hard-delete patterns by id. Returns rows deleted."""
        if not ids:
            return 0
        conn = self.get_connection()
        placeholders = ','.join('?' * len(ids))
        cursor = conn.execute(
            f"DELETE FROM ad_patterns WHERE id IN ({placeholders})",
            ids,
        )
        conn.commit()
        return cursor.rowcount

    def bulk_disable_patterns(self, ids: List[int]) -> int:
        """Set is_active=0 on patterns by id. Returns rows changed."""
        if not ids:
            return 0
        conn = self.get_connection()
        placeholders = ','.join('?' * len(ids))
        cursor = conn.execute(
            f"UPDATE ad_patterns SET is_active = 0, "
            f"disabled_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), "
            f"disabled_reason = COALESCE(disabled_reason, 'bulk-disable') "
            f"WHERE id IN ({placeholders})",
            ids,
        )
        conn.commit()
        return cursor.rowcount

    def get_sponsor_tags_map(self) -> Dict[int, List[str]]:
        """Return {sponsor_id: [tags]} for all active sponsors. Used by matcher."""
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT id, tags FROM known_sponsors WHERE is_active = 1"
        )
        result: Dict[int, List[str]] = {}
        for row in cursor.fetchall():
            try:
                tags = json.loads(row['tags'] or '[]')
            except (ValueError, TypeError):
                tags = []
            result[row['id']] = tags if isinstance(tags, list) else []
        return result

    def increment_pattern_match(self, pattern_id: int):
        """Increment pattern confirmation count and update last_matched_at."""
        conn = self.get_connection()
        conn.execute(
            """UPDATE ad_patterns SET
               confirmation_count = confirmation_count + 1,
               last_matched_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
               WHERE id = ?""",
            (pattern_id,)
        )
        conn.commit()

    def update_pattern_duration(self, pattern_id: int, observed_duration: float) -> bool:
        """Update pattern avg_duration as a running average."""
        conn = self.get_connection()
        conn.execute(
            """UPDATE ad_patterns SET
               avg_duration = CASE
                   WHEN duration_samples = 0 OR avg_duration IS NULL THEN ?
                   ELSE ((avg_duration * duration_samples) + ?) / (duration_samples + 1)
               END,
               duration_samples = duration_samples + 1
               WHERE id = ?""",
            (observed_duration, observed_duration, pattern_id)
        )
        conn.commit()
        return True

    def increment_pattern_false_positive(self, pattern_id: int):
        """Increment pattern false positive count."""
        conn = self.get_connection()
        conn.execute(
            "UPDATE ad_patterns SET false_positive_count = false_positive_count + 1 WHERE id = ?",
            (pattern_id,)
        )
        conn.commit()

    def delete_ad_pattern(self, pattern_id: int) -> bool:
        """Delete an ad pattern. Returns True if deleted."""
        conn = self.get_connection()
        cursor = conn.execute(
            "DELETE FROM ad_patterns WHERE id = ?", (pattern_id,)
        )
        conn.commit()
        return cursor.rowcount > 0

    def delete_all_community_patterns(self) -> int:
        """Hard-delete every pattern with source='community'. Returns the
        rowcount. `protected_from_sync` is ignored; the flag guards sync
        reconciliation, not an explicit operator purge.

        Also deletes audio_fingerprints rows that point at the doomed
        patterns; the schema does not declare an ON DELETE CASCADE on
        `pattern_id`, so we clean up explicitly the way
        `cleanup_service._purge_pattern` does for the single-row case.
        """
        conn = self.get_connection()
        conn.execute(
            "DELETE FROM audio_fingerprints WHERE pattern_id IN ("
            "SELECT id FROM ad_patterns WHERE source = 'community'"
            ")"
        )
        cursor = conn.execute(
            "DELETE FROM ad_patterns WHERE source = 'community'"
        )
        conn.commit()
        return cursor.rowcount

    # ========== Pattern Corrections Methods ==========

    def create_pattern_correction(self, correction_type: str, pattern_id: int = None,
                                   episode_id: str = None, podcast_title: str = None,
                                   episode_title: str = None, original_bounds: Dict = None,
                                   corrected_bounds: Dict = None, text_snippet: str = None,
                                   sponsor_id: int = None) -> int:
        """Create a pattern correction record. Returns correction ID."""
        conn = self.get_connection()
        cursor = conn.execute(
            """INSERT INTO pattern_corrections
               (pattern_id, episode_id, podcast_title, episode_title, correction_type,
                original_bounds, corrected_bounds, text_snippet, sponsor_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pattern_id, episode_id, podcast_title, episode_title, correction_type,
             json.dumps(original_bounds) if original_bounds else None,
             json.dumps(corrected_bounds) if corrected_bounds else None,
             text_snippet, sponsor_id)
        )
        conn.commit()
        return cursor.lastrowid

    def delete_conflicting_corrections(self, episode_id: str, correction_type: str,
                                        bounds_start: float, bounds_end: float) -> int:
        """Delete corrections that conflict with a new correction being submitted.

        When user confirms an ad, delete false_positive corrections for same bounds.
        When user rejects an ad, delete confirm corrections for same bounds.

        Returns number of deleted rows.
        """
        # Determine the conflicting type
        if correction_type == 'confirm':
            conflicting_type = 'false_positive'
        elif correction_type == 'false_positive':
            conflicting_type = 'confirm'
        else:
            return 0  # adjust doesn't conflict with either

        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT id, original_bounds FROM pattern_corrections
               WHERE episode_id = ? AND correction_type = ?""",
            (episode_id, conflicting_type)
        )

        deleted = 0
        for row in cursor.fetchall():
            if row['original_bounds']:
                try:
                    parsed = json.loads(row['original_bounds'])
                    fp_start = float(parsed.get('start', 0))
                    fp_end = float(parsed.get('end', 0))
                    # Check overlap (same 50% threshold as validator)
                    overlap_start = max(bounds_start, fp_start)
                    overlap_end = min(bounds_end, fp_end)
                    overlap = max(0, overlap_end - overlap_start)
                    segment_duration = bounds_end - bounds_start
                    if segment_duration > 0 and overlap / segment_duration >= 0.5:
                        conn.execute("DELETE FROM pattern_corrections WHERE id = ?", (row['id'],))
                        deleted += 1
                except (json.JSONDecodeError, KeyError, ValueError):
                    pass

        if deleted:
            conn.commit()
        return deleted

    def get_pattern_corrections(self, pattern_id: int = None, limit: int = 100) -> List[Dict]:
        """Get pattern corrections, optionally filtered by pattern_id."""
        conn = self.get_connection()

        if pattern_id:
            cursor = conn.execute(
                """SELECT * FROM pattern_corrections
                   WHERE pattern_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (pattern_id, limit)
            )
        else:
            cursor = conn.execute(
                "SELECT * FROM pattern_corrections ORDER BY created_at DESC LIMIT ?",
                (limit,)
            )

        return [dict(row) for row in cursor.fetchall()]

    def get_episode_corrections(self, episode_id: str) -> List[Dict]:
        """Get all corrections for a specific episode."""
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT id, correction_type, original_bounds, corrected_bounds, created_at
               FROM pattern_corrections
               WHERE episode_id = ?
               ORDER BY created_at DESC""",
            (episode_id,)
        )
        results = []
        for row in cursor.fetchall():
            item = dict(row)
            if item.get('original_bounds'):
                item['original_bounds'] = json.loads(item['original_bounds'])
            if item.get('corrected_bounds'):
                item['corrected_bounds'] = json.loads(item['corrected_bounds'])
            results.append(item)
        return results

    def get_false_positive_corrections(self, episode_id: str) -> List[Dict]:
        """Get false_positive corrections for an episode with parsed bounds.

        Returns list of dicts with 'start' and 'end' keys for easy overlap checking.
        """
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT original_bounds FROM pattern_corrections
               WHERE episode_id = ? AND correction_type = 'false_positive'""",
            (episode_id,)
        )
        results = []
        for row in cursor.fetchall():
            bounds = row['original_bounds']
            if bounds:
                try:
                    parsed = json.loads(bounds)
                    if 'start' in parsed and 'end' in parsed:
                        results.append({
                            'start': float(parsed['start']),
                            'end': float(parsed['end'])
                        })
                except (json.JSONDecodeError, KeyError, ValueError):
                    pass
        return results

    def get_confirmed_corrections(self, episode_id: str) -> List[Dict]:
        """Get confirmed corrections for an episode with parsed bounds.

        Returns list of dicts with 'start' and 'end' keys for easy overlap checking.
        """
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT original_bounds FROM pattern_corrections
               WHERE episode_id = ? AND correction_type = 'confirm'""",
            (episode_id,)
        )
        results = []
        for row in cursor.fetchall():
            bounds = row['original_bounds']
            if bounds:
                try:
                    parsed = json.loads(bounds)
                    if 'start' in parsed and 'end' in parsed:
                        results.append({
                            'start': float(parsed['start']),
                            'end': float(parsed['end'])
                        })
                except (json.JSONDecodeError, KeyError, ValueError):
                    pass
        return results

    def get_podcast_false_positive_texts(self, podcast_slug: str, limit: int = 100) -> List[Dict]:
        """Get all false positive texts for a podcast for cross-episode matching.

        Returns list of dicts with:
        - text: The rejected segment text
        - episode_id: Which episode it came from
        - start, end: Original time bounds
        """
        conn = self.get_connection()
        cursor = conn.execute('''
            SELECT pc.text_snippet, pc.episode_id, pc.original_bounds, pc.created_at
            FROM pattern_corrections pc
            JOIN episodes e ON pc.episode_id = e.episode_id
            JOIN podcasts p ON e.podcast_id = p.id
            WHERE p.slug = ?
            AND pc.correction_type = 'false_positive'
            AND pc.text_snippet IS NOT NULL
            AND length(pc.text_snippet) >= 50
            ORDER BY pc.created_at DESC
            LIMIT ?
        ''', (podcast_slug, limit))

        results = []
        for row in cursor.fetchall():
            bounds = {}
            if row['original_bounds']:
                try:
                    bounds = json.loads(row['original_bounds'])
                except (json.JSONDecodeError, ValueError):
                    pass
            results.append({
                'text': row['text_snippet'],
                'episode_id': row['episode_id'],
                'start': bounds.get('start'),
                'end': bounds.get('end'),
                'created_at': row['created_at']
            })
        return results
