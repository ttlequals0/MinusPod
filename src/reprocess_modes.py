"""Per-mode reprocess clearing rules, shared by the reprocess API and the
pipeline's automatic reruns."""
from utils.constants import EpisodeStatus
from utils.time import utc_now_iso

# What to wipe before requeueing. 'details' wipes the whole episode_details
# row; 'ad_data' keeps the saved transcript (clears just the ad-detection
# outputs) so transcription is skipped; 'none' keeps everything, since a recut
# re-cuts the retained original from the saved detections and re-times the
# saved transcript.
REPROCESS_MODE_CLEAR = {
    'reprocess': 'details',
    'full': 'details',
    'llm': 'ad_data',
    'recut': 'none',
}


# Modes that reuse the saved transcript to skip re-transcription and cannot
# run without one (issue #349).
REPROCESS_MODE_NEEDS_TRANSCRIPT = {
    'reprocess': False,
    'full': False,
    'llm': True,
    'recut': False,
}

# 'details' modes re-transcribe, but their clear is deferred to the
# transcribe stage (immediately before the fresh save) so a crash mid-run
# leaves the prior transcript and detection data intact (#692). The pipeline
# passes force_transcription for these modes instead of relying on a
# pre-cleared row.
FORCE_TRANSCRIBE_MODES = frozenset(
    mode for mode, clear in REPROCESS_MODE_CLEAR.items() if clear == 'details')


def batch_clear_episodes_for_mode(db, slug, episode_ids, mode):
    """Bulk counterpart of clear_episode_for_mode."""
    if REPROCESS_MODE_CLEAR[mode] != 'ad_data':
        return
    db.batch_clear_episode_ad_data(slug, episode_ids)


def clear_episode_for_mode(db, slug, episode_id, mode):
    """Clear ad-detection outputs for the mode's rule ('llm').

    'details' modes are not cleared here: their wipe happens in the
    transcribe stage, just before the fresh transcript is saved (#692);
    'none' keeps everything.
    """
    if REPROCESS_MODE_CLEAR[mode] != 'ad_data':
        return
    db.clear_episode_ad_data(slug, episode_id)


def reset_episode_for_reprocess(db, slug, episode_id, mode):
    """Put an episode back in the queue's hands for a rerun in ``mode``.

    For user-requested reprocesses: the episode leaves the served feed until
    the rerun finishes. Automatic reruns keep the episode published and clear
    when the run starts instead.

    Sets the reprocess mode before anything reads it, marks the row
    user-requested so the queue gates honor it, then clears the cached
    detection data the mode does not keep.
    """
    db.upsert_episode(
        slug, episode_id,
        status=EpisodeStatus.PENDING.value,
        reprocess_mode=mode,
        reprocess_requested_at=utc_now_iso(),
        retry_count=0,
        error_message=None,
        deferred_at=None,
        deferred_service=None,
    )
    clear_episode_for_mode(db, slug, episode_id, mode)
