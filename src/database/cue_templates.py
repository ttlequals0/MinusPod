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

# Sentinel for update_cue_template: distinguishes "caller did not pass
# score_threshold" from "caller explicitly wants to clear it to NULL".
_UNSET = object()


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

    def get_cue_template_meta(self, template_id: int) -> Optional[Dict]:
        """Return one template's metadata WITHOUT its mfcc/pcm blobs.

        The optimize-window route polls every 3s; its pre-claim ownership and
        source-episode checks need only scalar columns, so this avoids dragging
        the multi-MB blobs on every poll.
        """
        conn = self.get_connection()
        row = conn.execute(
            "SELECT id, podcast_id, label, cue_type, source_episode_id, "
            "source_offset_s, duration_s, sample_rate, n_coeffs, scope, "
            "network_id, enabled, score_threshold, created_at, created_by "
            "FROM audio_cue_templates WHERE id = ?", (template_id,),
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
            "enabled, score_threshold, created_at, created_by, "
            "(pcm_blob IS NOT NULL) AS has_audio "
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
                      enabled, score_threshold, created_at, created_by,
                      (pcm_blob IS NOT NULL) AS has_audio
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
        score_threshold=_UNSET,
    ) -> bool:
        """Patch cue_type, enabled, and/or score_threshold. Returns True if updated.

        Changing ``cue_type`` also resets the derived ``label``.
        Pass ``score_threshold=None`` to clear the column to NULL.
        Omit ``score_threshold`` (or use the default sentinel) to leave it unchanged.
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
        if score_threshold is not _UNSET:
            sets.append("score_threshold = ?")
            args.append(score_threshold)
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
    # Cached background scans. A slow full-decode scan runs in a background
    # thread and the API polls a cached row instead of holding the request open
    # past the proxy timeout. Every scan family uses the same claim/poll/save
    # state machine over an identically-shaped table, so the machine lives in
    # one place. Families differ only by table name, payload column, and their
    # primary key: most are keyed by (podcast_id, episode_id); the window
    # optimizer is keyed by template_id alone (no podcast_id column). The key
    # column and the podcast-scoping flag are internal constants (never user
    # input), so they are safe to interpolate into the SQL alongside the table
    # and payload column.

    def _scan_where(self, key_col: str, has_podcast: bool) -> str:
        """WHERE clause for the family's primary key (bound params)."""
        return ('podcast_id = ? AND ' if has_podcast else '') + f'{key_col} = ?'

    def _get_scan(self, table: str, payload_col: str,
                  podcast_id: int, episode_id: str,
                  key_col: str = 'episode_id',
                  has_podcast: bool = True) -> Optional[Dict]:
        """Return the cached scan row for a family's key, or None.

        ``podcast_id``/``episode_id`` are the (scope, key) pair for the row;
        ``episode_id`` binds ``key_col`` (an episode id, set hash, or template
        id) and ``podcast_id`` is ignored when ``has_podcast`` is False.
        """
        conn = self.get_connection()
        args = (podcast_id, episode_id) if has_podcast else (episode_id,)
        row = conn.execute(
            f"SELECT status, {payload_col}, error, updated_at FROM {table} "
            f"WHERE {self._scan_where(key_col, has_podcast)}",
            args,
        ).fetchone()
        return dict(row) if row else None

    def _claim_scan(
        self, table: str, payload_col: str, podcast_id: int, episode_id: str,
        stale_seconds: float, force: bool = False,
        key_col: str = 'episode_id', has_podcast: bool = True,
    ) -> str:
        """Decide whether the caller should run the scan now.

        Returns one of:
          'ready'    -- a cached result exists; read it with the get wrapper
          'scanning' -- a fresh scan is already running; poll again shortly
          'error'    -- the last scan failed and is still fresh; show the error
          'started'  -- the caller claimed the slot and must run the scan

        A stale 'scanning' (crashed worker) or stale 'error' is reclaimable so
        the scan recovers. ``force`` reclaims a 'ready'/'error' row for an
        explicit rescan, but never interrupts a live scan.

        The claim is a single conditional UPSERT so two concurrent requests for
        the same key cannot both start a scan: only the statement that actually
        writes the 'scanning' row (insert, or an update whose WHERE matched)
        reports 'started'; the other re-reads and reports the live state.

        ``key_col``/``has_podcast`` select the family's primary key: the two
        (podcast_id, episode_id) families keep the default; single-key families
        pass their own key_col and, when keyed by template id alone,
        ``has_podcast=False`` to drop the podcast_id column entirely.
        """
        conn = self.get_connection()
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=stale_seconds)) \
            .strftime('%Y-%m-%dT%H:%M:%SZ')
        cols = ('podcast_id, ' if has_podcast else '') + key_col
        vals = (':pid, ' if has_podcast else '') + ':eid'
        conflict = ('podcast_id, ' if has_podcast else '') + key_col
        before = conn.total_changes
        # claim_epoch is bumped on every successful claim (insert starts at 1,
        # a reclaim increments the prior value). The worker captures this token
        # and the save guards on it, so a stale worker cannot clobber a newer
        # claim.
        conn.execute(
            f"""INSERT INTO {table}
                   ({cols}, status, {payload_col}, error, updated_at, claim_epoch)
               VALUES ({vals}, 'scanning', NULL, NULL, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), 1)
               ON CONFLICT({conflict}) DO UPDATE SET
                   status='scanning', {payload_col}=NULL, error=NULL,
                   updated_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                   claim_epoch={table}.claim_epoch + 1
               WHERE ({table}.status = 'scanning' AND {table}.updated_at <= :cutoff)
                  OR ({table}.status = 'error' AND (:force OR {table}.updated_at <= :cutoff))
                  OR ({table}.status = 'ready' AND :force)""",
            {'pid': podcast_id, 'eid': episode_id, 'cutoff': cutoff, 'force': 1 if force else 0},
        )
        conn.commit()
        if conn.total_changes > before:
            return 'started'
        # Did not claim: report the live state of the row that blocked us.
        args = (podcast_id, episode_id) if has_podcast else (episode_id,)
        row = conn.execute(
            f"SELECT status FROM {table} WHERE {self._scan_where(key_col, has_podcast)}",
            args,
        ).fetchone()
        return row['status'] if row else 'scanning'

    def _get_scan_claim_epoch(
        self, table: str, podcast_id: int, episode_id: str,
        key_col: str = 'episode_id', has_podcast: bool = True,
    ) -> Optional[int]:
        """Current claim_epoch for a family's key, or None when no row exists.

        The route reads this right after a 'started' claim to capture the token
        it hands the worker; the worker's save then guards on the same value.
        """
        conn = self.get_connection()
        args = (podcast_id, episode_id) if has_podcast else (episode_id,)
        row = conn.execute(
            f"SELECT claim_epoch FROM {table} "
            f"WHERE {self._scan_where(key_col, has_podcast)}",
            args,
        ).fetchone()
        return row['claim_epoch'] if row else None

    def _save_scan_result(
        self, table: str, payload_col: str, podcast_id: int, episode_id: str,
        payload, key_col: str = 'episode_id', has_podcast: bool = True,
        claim_epoch: Optional[int] = None,
    ) -> None:
        """Persist a completed scan's payload and mark it ready.

        When ``claim_epoch`` is given the write is guarded on it, so a stale
        worker whose claim was superseded by a fresh one no-ops instead of
        overwriting the newer result.
        """
        conn = self.get_connection()
        guard = '' if claim_epoch is None else ' AND claim_epoch = ?'
        key_args = (podcast_id, episode_id) if has_podcast else (episode_id,)
        args = (json.dumps(payload),) + key_args + (
            () if claim_epoch is None else (claim_epoch,))
        conn.execute(
            f"""UPDATE {table} SET status='ready', {payload_col}=?, error=NULL,
                   updated_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
               WHERE {self._scan_where(key_col, has_podcast)}{guard}""",
            args,
        )
        conn.commit()

    def _save_scan_error(
        self, table: str, podcast_id: int, episode_id: str, error: str,
        key_col: str = 'episode_id', has_podcast: bool = True,
        claim_epoch: Optional[int] = None,
    ) -> None:
        """Mark a scan as failed with a short error message.

        Guarded on ``claim_epoch`` when supplied (see _save_scan_result).
        """
        conn = self.get_connection()
        guard = '' if claim_epoch is None else ' AND claim_epoch = ?'
        key_args = (podcast_id, episode_id) if has_podcast else (episode_id,)
        args = (str(error)[:500],) + key_args + (
            () if claim_epoch is None else (claim_epoch,))
        conn.execute(
            f"""UPDATE {table} SET status='error', error=?,
                   updated_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
               WHERE {self._scan_where(key_col, has_podcast)}{guard}""",
            args,
        )
        conn.commit()

    # Cached recurring-sound scan (the on-demand "find cue candidates" run).

    def get_cue_candidate_scan(self, podcast_id: int, episode_id: str) -> Optional[Dict]:
        return self._get_scan('cue_candidate_scans', 'candidates_json', podcast_id, episode_id)

    def claim_cue_candidate_scan(
        self, podcast_id: int, episode_id: str, stale_seconds: float,
        force: bool = False,
    ) -> str:
        return self._claim_scan(
            'cue_candidate_scans', 'candidates_json', podcast_id, episode_id,
            stale_seconds, force)

    def get_cue_candidate_scan_claim_epoch(
        self, podcast_id: int, episode_id: str,
    ) -> Optional[int]:
        return self._get_scan_claim_epoch(
            'cue_candidate_scans', podcast_id, episode_id)

    def save_cue_candidate_scan_result(
        self, podcast_id: int, episode_id: str, candidates: List[Dict],
        claim_epoch: Optional[int] = None,
    ) -> None:
        self._save_scan_result(
            'cue_candidate_scans', 'candidates_json', podcast_id, episode_id,
            candidates, claim_epoch=claim_epoch)

    def save_cue_candidate_scan_error(
        self, podcast_id: int, episode_id: str, error: str,
        claim_epoch: Optional[int] = None,
    ) -> None:
        self._save_scan_error(
            'cue_candidate_scans', podcast_id, episode_id, error,
            claim_epoch=claim_epoch)

    # Cached threshold-suggest scan (#350 follow-up); stores a suggestion dict.

    def get_cue_threshold_scan(self, podcast_id: int, episode_id: str) -> Optional[Dict]:
        return self._get_scan('cue_threshold_scans', 'result_json', podcast_id, episode_id)

    def claim_cue_threshold_scan(
        self, podcast_id: int, episode_id: str, stale_seconds: float,
        force: bool = False,
    ) -> str:
        return self._claim_scan(
            'cue_threshold_scans', 'result_json', podcast_id, episode_id,
            stale_seconds, force)

    def get_cue_threshold_scan_claim_epoch(
        self, podcast_id: int, episode_id: str,
    ) -> Optional[int]:
        return self._get_scan_claim_epoch(
            'cue_threshold_scans', podcast_id, episode_id)

    def save_cue_threshold_scan_result(
        self, podcast_id: int, episode_id: str, result: Dict,
        claim_epoch: Optional[int] = None,
    ) -> None:
        self._save_scan_result(
            'cue_threshold_scans', 'result_json', podcast_id, episode_id,
            result, claim_epoch=claim_epoch)

    def save_cue_threshold_scan_error(
        self, podcast_id: int, episode_id: str, error: str,
        claim_epoch: Optional[int] = None,
    ) -> None:
        self._save_scan_error(
            'cue_threshold_scans', podcast_id, episode_id, error,
            claim_epoch=claim_epoch)

    # Feed-wide candidate dismissals (2.44.0): "not a cue" feedback the scan
    # worker matches against by fingerprint.

    def create_cue_candidate_dismissal(self, podcast_id: int, source_episode_id: str,
                                       start_s: float, end_s: float,
                                       label: Optional[str],
                                       fingerprint_json: str) -> int:
        conn = self.get_connection()
        cursor = conn.execute(
            """INSERT INTO cue_candidate_dismissals
                   (podcast_id, source_episode_id, start_s, end_s, label, fingerprint)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (podcast_id, source_episode_id, start_s, end_s, label, fingerprint_json))
        conn.commit()
        return cursor.lastrowid

    def list_cue_candidate_dismissals(self, podcast_id: int) -> List[Dict]:
        conn = self.get_connection()
        rows = conn.execute(
            """SELECT id, podcast_id, source_episode_id, start_s, end_s, label,
                      fingerprint, created_at
               FROM cue_candidate_dismissals WHERE podcast_id = ?
               ORDER BY created_at DESC, id DESC""",
            (podcast_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_cue_candidate_dismissal(self, dismissal_id: int) -> Optional[Dict]:
        conn = self.get_connection()
        row = conn.execute(
            """SELECT id, podcast_id, source_episode_id, start_s, end_s, label,
                      fingerprint, created_at
               FROM cue_candidate_dismissals WHERE id = ?""",
            (dismissal_id,)).fetchone()
        return dict(row) if row else None

    def delete_cue_candidate_dismissal(self, dismissal_id: int) -> bool:
        conn = self.get_connection()
        cursor = conn.execute(
            "DELETE FROM cue_candidate_dismissals WHERE id = ?", (dismissal_id,))
        conn.commit()
        return cursor.rowcount > 0

    def list_cue_candidate_dismissal_ids(self, podcast_id: int) -> set:
        """Just the ids, for read-time stamp reconciliation (skips the
        fingerprint blobs)."""
        conn = self.get_connection()
        rows = conn.execute(
            "SELECT id FROM cue_candidate_dismissals WHERE podcast_id = ?",
            (podcast_id,)).fetchall()
        return {r['id'] for r in rows}

    def list_cue_candidate_dismissals_decoded(self, podcast_id: int) -> List[Dict]:
        """Dismissals with fingerprint decoded to raw ints, for the scan
        worker. Rows whose fingerprint does not decode to a non-empty list
        are skipped with a warning -- corrupt feedback must never fail a scan."""
        out = []
        for d in self.list_cue_candidate_dismissals(podcast_id):
            try:
                ints = json.loads(d['fingerprint'])
            except (ValueError, TypeError):
                ints = None
            if isinstance(ints, list) and ints:
                out.append({'id': d['id'], 'raw_ints': ints})
            else:
                logger.warning('dismissal %s: unreadable fingerprint; skipped', d['id'])
        return out

    # Cross-episode body scan family (D1b, #350).  Keyed by
    # (podcast_id, episode_set_hash) -- a sha256 hex of the sorted episode-id
    # list -- so the cache is shared across any identical episode set regardless
    # of request order.  Same two-key shape as the pre-existing families, only
    # the key column is named differently, so it routes through the generics
    # with key_col='episode_set_hash'.

    def get_cue_cross_episode_scan(
        self, podcast_id: int, episode_set_hash: str,
    ) -> Optional[Dict]:
        return self._get_scan(
            'cue_cross_episode_scans', 'result_json', podcast_id,
            episode_set_hash, key_col='episode_set_hash')

    def claim_cue_cross_episode_scan(
        self, podcast_id: int, episode_set_hash: str, stale_seconds: float,
        force: bool = False,
    ) -> str:
        return self._claim_scan(
            'cue_cross_episode_scans', 'result_json', podcast_id,
            episode_set_hash, stale_seconds, force, key_col='episode_set_hash')

    def get_cue_cross_episode_scan_claim_epoch(
        self, podcast_id: int, episode_set_hash: str,
    ) -> Optional[int]:
        return self._get_scan_claim_epoch(
            'cue_cross_episode_scans', podcast_id, episode_set_hash,
            key_col='episode_set_hash')

    def save_cue_cross_episode_scan_result(
        self, podcast_id: int, episode_set_hash: str, payload: Dict,
        claim_epoch: Optional[int] = None,
    ) -> None:
        self._save_scan_result(
            'cue_cross_episode_scans', 'result_json', podcast_id,
            episode_set_hash, payload, key_col='episode_set_hash',
            claim_epoch=claim_epoch)

    def save_cue_cross_episode_scan_error(
        self, podcast_id: int, episode_set_hash: str, error: str,
        claim_epoch: Optional[int] = None,
    ) -> None:
        self._save_scan_error(
            'cue_cross_episode_scans', podcast_id, episode_set_hash, error,
            key_col='episode_set_hash', claim_epoch=claim_epoch)

    # Window optimizer scan family (D2a, #350).  Keyed by template_id alone
    # (the optimizer is per-template, not per-episode-set), so it routes through
    # the generics with has_podcast=False -- there is no podcast_id column.

    def get_cue_window_optimize_scan(self, template_id: int) -> Optional[Dict]:
        return self._get_scan(
            'cue_window_optimize_scans', 'result_json', None, template_id,
            key_col='template_id', has_podcast=False)

    def claim_cue_window_optimize_scan(
        self, template_id: int, stale_seconds: float, force: bool = False,
    ) -> str:
        return self._claim_scan(
            'cue_window_optimize_scans', 'result_json', None, template_id,
            stale_seconds, force, key_col='template_id', has_podcast=False)

    def get_cue_window_optimize_scan_claim_epoch(
        self, template_id: int,
    ) -> Optional[int]:
        return self._get_scan_claim_epoch(
            'cue_window_optimize_scans', None, template_id,
            key_col='template_id', has_podcast=False)

    def save_cue_window_optimize_scan_result(
        self, template_id: int, payload: Dict,
        claim_epoch: Optional[int] = None,
    ) -> None:
        self._save_scan_result(
            'cue_window_optimize_scans', 'result_json', None, template_id,
            payload, key_col='template_id', has_podcast=False,
            claim_epoch=claim_epoch)

    def save_cue_window_optimize_scan_error(
        self, template_id: int, error: str,
        claim_epoch: Optional[int] = None,
    ) -> None:
        self._save_scan_error(
            'cue_window_optimize_scans', None, template_id, error,
            key_col='template_id', has_podcast=False, claim_epoch=claim_epoch)

    def update_cue_template_window(
        self,
        template_id: int,
        source_offset_s: float,
        duration_s: float,
        mfcc_blob: bytes,
        pcm_blob: bytes,
        sample_rate: int,
    ) -> bool:
        """Update the window geometry and re-derived blobs together.

        Called by the PATCH route when sourceOffsetS or durationS changes so
        the stored blobs always reflect the current window. Any cached
        window-optimizer result is invalidated in the same transaction: its
        proposal and baseline describe the pre-move geometry.
        Returns True if a row was updated.
        """
        conn = self.get_connection()
        cursor = conn.execute(
            """UPDATE audio_cue_templates
               SET source_offset_s=?, duration_s=?, mfcc_blob=?, pcm_blob=?,
                   sample_rate=?, pcm_sample_rate=?
               WHERE id=?""",
            (source_offset_s, duration_s, mfcc_blob, pcm_blob,
             sample_rate, sample_rate, template_id),
        )
        conn.execute(
            "DELETE FROM cue_window_optimize_scans WHERE template_id=?",
            (template_id,),
        )
        conn.commit()
        return cursor.rowcount > 0
