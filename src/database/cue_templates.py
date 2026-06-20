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
import logging
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
        """List without the blobs, for UI listings."""
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
