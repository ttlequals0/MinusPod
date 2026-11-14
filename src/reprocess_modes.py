"""Per-mode reprocess clearing rules, shared by the reprocess API and the
pipeline's automatic reruns."""
from utils.constants import EpisodeStatus
from utils.time import utc_now_iso

# What to wipe before requeueing: 'details' the whole episode_details row; 'ad_data' only
# the ad-detection outputs (transcription is skipped); 'none' nothing (a recut reuses
# the saved detections and transcript).
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

# 'details' modes re-transcribe, but their clear waits for the transcribe
# stage so a crash mid-run leaves the prior data intact (#692). The pipeline
# passes force_transcription instead of relying on a pre-cleared row.
FORCE_TRANSCRIBE_MODES = frozenset(
    mode for mode, clear in REPROCESS_MODE_CLEAR.items() if clear == 'details')


def batch_clear_episodes_for_mode(db, slug, episode_ids, mode):
    """Bulk counterpart of clear_episode_for_mode."""
    if REPROCESS_MODE_CLEAR[mode] != 'ad_data':
        return
    db.batch_clear_episode_ad_data(slug, episode_ids)


def clear_episode_for_mode(db, slug, episode_id, mode):
    """Clear ad-detection outputs for the 'ad_data' rule ('llm').

    'details' modes are wiped in the transcribe stage just before the new transcript is saved (#692).
    """
    if REPROCESS_MODE_CLEAR[mode] != 'ad_data':
        return
    db.clear_episode_ad_data(slug, episode_id)


def reset_episode_for_reprocess(db, slug, episode_id, mode):
    """Requeue an episode for a rerun in ``mode`` (user-requested: it leaves the served feed until done).

    Automatic reruns keep the episode published and clear when the run starts instead.
    Sets the mode before anything reads it and marks the row user-requested for the queue gates.
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
        chapters_regen_error=None,
    )
    clear_episode_for_mode(db, slug, episode_id, mode)
