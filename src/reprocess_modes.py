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


def clear_episode_for_mode(db, slug, episode_id, mode):
    """Clear cached detection data before a reprocess, per the mode's rule."""
    clear = REPROCESS_MODE_CLEAR[mode]
    if clear == 'none':
        return
    if clear == 'ad_data':
        db.clear_episode_ad_data(slug, episode_id)
    else:
        db.clear_episode_details(slug, episode_id)


def reset_episode_for_reprocess(db, slug, episode_id, mode):
    """Put an episode back in the queue's hands for a rerun in ``mode``.

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
