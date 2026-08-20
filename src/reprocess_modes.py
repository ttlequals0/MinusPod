"""Per-mode reprocess clearing rules, shared by the reprocess API and the
pipeline's automatic reruns."""

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
