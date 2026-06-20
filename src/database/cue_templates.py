"""Audio cue template mixin (#350).

Per-feed user-defined ding/stinger templates. Each template stores the raw
captured PCM (source of truth, little-endian int16 mono) plus a derived MFCC
matrix (float32 little-endian, row-major, shape ``(n_frames, n_coeffs)``)
which the cue template matcher slides across each episode to find recurrences.
The MFCC is the matcher's hot path; the PCM is kept so a template can be
re-derived if MFCC params ever change and exported as a lossless WAV.

Scope mirrors the ad_patterns model but with only two tiers: ``podcast`` (the
default, applies to one feed) and ``network`` (applies to every feed sharing a
``network_id``). There is no ``global`` tier -- a cue template only matches a
show that uses the exact same sound, so a global stinger has no meaning.
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from config import audio_cue_type_label

logger = logging.getLogger(__name__)


class CueTemplateMixin:
    """Audio cue template CRUD and per-feed scope resolution."""

    def create_cue_template(
        self,
        podcast_id: int,
        cue_type: str,
        source_episode_id: Optional[str],
        source_offset_s: float,
        duration_s: float,
        sample_rate: int,
        n_coeffs: int,
        mfcc_blob: bytes,
        pcm_blob: Optional[bytes] = None,
        pcm_sample_rate: Optional[int] = None,
        scope: str = 'podcast',
        network_id: Optional[str] = None,
        created_by: str = 'user',
    ) -> int:
        """Insert a cue template. Returns the new row id.

        The human-readable ``label`` is derived from ``cue_type`` (not stored
        freeform) so the phrase fed to the LLM prompt is always one of the
        fixed type names.
        """
        label = audio_cue_type_label(cue_type)
        conn = self.get_connection()
        cursor = conn.execute(
            """INSERT INTO audio_cue_templates (
                   podcast_id, label, cue_type, source_episode_id, source_offset_s,
                   duration_s, sample_rate, n_coeffs, mfcc_blob,
                   pcm_blob, pcm_sample_rate, scope, network_id,
                   enabled, created_by
               )
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (
                podcast_id, label, cue_type, source_episode_id, source_offset_s,
                duration_s, sample_rate, n_coeffs, mfcc_blob,
                pcm_blob, pcm_sample_rate, scope, network_id, created_by,
            ),
        )
        conn.commit()
        return cursor.lastrowid

    def get_cue_template(self, template_id: int) -> Optional[Dict]:
        """Return one template by id, including its blobs."""
        conn = self.get_connection()
        row = conn.execute(
            "SELECT * FROM audio_cue_templates WHERE id = ?", (template_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_active_cue_templates_for_feed(self, podcast_id: int) -> List[Dict]:
        """Enabled templates that apply to a feed, most-specific-first.

        Resolves both tiers: podcast-scope templates owned by this feed, and
        network-scope templates whose ``network_id`` matches the feed's
        network. Podcast-scope rows sort ahead of network-scope ones so the
        matcher and snap see the feed's own cues first.

        The network match uses the feed's effective network -- a non-empty
        ``network_id_override`` when set, else the auto-detected ``network_id``.
        This lets a manually-assigned network (same creator, no auto-detected
        network) link feeds for promotion. ``NULLIF(..., '')`` guards a blank
        override stored as an empty string rather than NULL, which COALESCE
        alone would not fall through.
        """
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT * FROM audio_cue_templates
               WHERE enabled = 1 AND (
                   (scope = 'podcast' AND podcast_id = ?)
                   OR (scope = 'network' AND network_id IS NOT NULL
                       AND network_id = (
                           SELECT COALESCE(NULLIF(network_id_override, ''), network_id)
                           FROM podcasts WHERE id = ?
                       ))
               )
               ORDER BY (scope = 'podcast') DESC, created_at DESC""",
            (podcast_id, podcast_id),
        )
        return [dict(row) for row in cursor.fetchall()]

    def list_cue_templates_metadata(self, podcast_id: int) -> List[Dict]:
        """List a feed's own templates without the blobs, for UI listings."""
        conn = self.get_connection()
        cursor = conn.execute(
            "SELECT id, podcast_id, label, cue_type, source_episode_id, source_offset_s, "
            "duration_s, sample_rate, n_coeffs, scope, network_id, "
            "enabled, created_at, created_by "
            "FROM audio_cue_templates WHERE podcast_id = ? "
            "ORDER BY created_at DESC",
            (podcast_id,),
        )
        return [dict(row) for row in cursor.fetchall()]

    def list_cue_templates_for_feed_ui(self, podcast_id: int) -> List[Dict]:
        """A feed's own templates plus network templates shared from siblings.

        A template promoted to network scope on one feed applies to every feed
        on the same network, so it should be visible in each feed's panel. This
        unions the feed's own rows (any scope) with network-scope rows owned by
        OTHER feeds whose ``network_id`` matches this feed's effective network
        (the override when set, else the auto-detected one). Own rows sort
        first. The caller marks the sibling rows as not-owned (read-only).
        """
        conn = self.get_connection()
        cursor = conn.execute(
            """SELECT id, podcast_id, label, cue_type, source_episode_id, source_offset_s,
                      duration_s, sample_rate, n_coeffs, scope, network_id,
                      enabled, created_at, created_by
               FROM audio_cue_templates
               WHERE podcast_id = :pid
                  OR (scope = 'network' AND network_id IS NOT NULL AND network_id != ''
                      AND podcast_id != :pid
                      AND network_id = (
                          SELECT COALESCE(NULLIF(network_id_override, ''), network_id)
                          FROM podcasts WHERE id = :pid))
               ORDER BY (podcast_id = :pid) DESC, created_at DESC""",
            {'pid': podcast_id},
        )
        return [dict(row) for row in cursor.fetchall()]

    def update_cue_template(
        self,
        template_id: int,
        cue_type: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> bool:
        """Patch cue_type and/or enabled. Returns True if a row was updated.

        Changing ``cue_type`` also resets the derived ``label`` so the stored
        phrase stays in sync with the type.
        """
        sets = []
        args: list = []
        if cue_type is not None:
            sets.append("cue_type = ?")
            args.append(cue_type)
            sets.append("label = ?")
            args.append(audio_cue_type_label(cue_type))
        if enabled is not None:
            sets.append("enabled = ?")
            args.append(1 if enabled else 0)
        if not sets:
            return False
        args.append(template_id)
        conn = self.get_connection()
        cursor = conn.execute(
            f"UPDATE audio_cue_templates SET {', '.join(sets)} WHERE id = ?",
            tuple(args),
        )
        conn.commit()
        return cursor.rowcount > 0

    def delete_cue_template(self, template_id: int) -> bool:
        """Remove a template. Returns True if a row was deleted."""
        conn = self.get_connection()
        cursor = conn.execute(
            "DELETE FROM audio_cue_templates WHERE id = ?", (template_id,),
        )
        conn.commit()
        return cursor.rowcount > 0

    def promote_cue_template(
        self, template_id: int, scope: str, network_id: Optional[str] = None,
    ) -> bool:
        """Set a template's scope (podcast or network) and network_id.

        Promoting to 'network' makes the cue apply to every feed sharing
        network_id; demoting to 'podcast' clears network_id. Returns True if a
        row was updated.
        """
        conn = self.get_connection()
        cursor = conn.execute(
            "UPDATE audio_cue_templates SET scope = ?, network_id = ? WHERE id = ?",
            (scope, network_id if scope == 'network' else None, template_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Cached recurring-sound scan (the on-demand "find cue candidates" run).
    # The scan is slow (full decode), so it runs in a background thread and the
    # API polls this row instead of holding the request open past the proxy
    # timeout.

    def get_cue_candidate_scan(self, podcast_id: int, episode_id: str) -> Optional[Dict]:
        """Return the cached scan row for a feed/episode, or None."""
        conn = self.get_connection()
        row = conn.execute(
            "SELECT status, candidates_json, error, updated_at FROM cue_candidate_scans "
            "WHERE podcast_id = ? AND episode_id = ?",
            (podcast_id, episode_id),
        ).fetchone()
        return dict(row) if row else None

    def claim_cue_candidate_scan(
        self, podcast_id: int, episode_id: str, stale_seconds: float,
        force: bool = False,
    ) -> str:
        """Decide whether the caller should run the scan now.

        Returns one of:
          'ready'    -- a cached result exists; read it with get_cue_candidate_scan
          'scanning' -- a fresh scan is already running; poll again shortly
          'error'    -- the last scan failed and is still fresh; show the error
          'started'  -- the caller claimed the slot and must run the scan

        A stale 'scanning' (crashed worker) or stale 'error' is reclaimable so
        the scan recovers. ``force`` reclaims a 'ready'/'error' row for an
        explicit rescan, but never interrupts a live scan.

        The claim is a single conditional UPSERT so two concurrent requests for
        the same episode cannot both start a scan: only the statement that
        actually writes the 'scanning' row (insert, or an update whose WHERE
        matched) reports 'started'; the other re-reads and reports the live
        state.
        """
        conn = self.get_connection()
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)) \
            .strftime('%Y-%m-%dT%H:%M:%SZ')
        before = conn.total_changes
        conn.execute(
            """INSERT INTO cue_candidate_scans
                   (podcast_id, episode_id, status, candidates_json, error, updated_at)
               VALUES (:pid, :eid, 'scanning', NULL, NULL, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
               ON CONFLICT(podcast_id, episode_id) DO UPDATE SET
                   status='scanning', candidates_json=NULL, error=NULL,
                   updated_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
               WHERE (cue_candidate_scans.status = 'scanning' AND cue_candidate_scans.updated_at <= :cutoff)
                  OR (cue_candidate_scans.status = 'error' AND (:force OR cue_candidate_scans.updated_at <= :cutoff))
                  OR (cue_candidate_scans.status = 'ready' AND :force)""",
            {'pid': podcast_id, 'eid': episode_id, 'cutoff': cutoff, 'force': 1 if force else 0},
        )
        conn.commit()
        if conn.total_changes > before:
            return 'started'
        # Did not claim: report the live state of the row that blocked us.
        row = conn.execute(
            "SELECT status FROM cue_candidate_scans WHERE podcast_id = ? AND episode_id = ?",
            (podcast_id, episode_id),
        ).fetchone()
        return row['status'] if row else 'scanning'

    def save_cue_candidate_scan_result(
        self, podcast_id: int, episode_id: str, candidates: List[Dict],
    ) -> None:
        """Persist a completed scan's candidates and mark it ready."""
        conn = self.get_connection()
        conn.execute(
            """UPDATE cue_candidate_scans SET status='ready', candidates_json=?, error=NULL,
                   updated_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
               WHERE podcast_id=? AND episode_id=?""",
            (json.dumps(candidates), podcast_id, episode_id),
        )
        conn.commit()

    def save_cue_candidate_scan_error(
        self, podcast_id: int, episode_id: str, error: str,
    ) -> None:
        """Mark a scan as failed with a short error message."""
        conn = self.get_connection()
        conn.execute(
            """UPDATE cue_candidate_scans SET status='error', error=?,
                   updated_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
               WHERE podcast_id=? AND episode_id=?""",
            (str(error)[:500], podcast_id, episode_id),
        )
        conn.commit()
