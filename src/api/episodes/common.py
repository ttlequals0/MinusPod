"""Shared episode route helpers: job state, marker parsing and constants."""

from utils.markers import parse_ad_markers
from utils.constants import EpisodeStatus


def _job_state(status, owned_state):
    """Authoritative jobState for one episode. ``owned_state`` (from
    get_episode_job_states: 'processing'/'queued'/None) plus the stored status;
    an owned run outranks a lingering queue row, so a just-claimed run never
    reads as idle or queued.
    """
    if owned_state == 'processing' or status == EpisodeStatus.PROCESSING:
        return 'processing'
    if owned_state:
        return owned_state
    return 'idle'


def _episode_job_state(db, slug, episode_id, status):
    """Authoritative jobState for a single episode, looked up fresh."""
    states = db.get_episode_job_states([episode_id])
    return _job_state(status, states.get((slug, episode_id)))


def _markers_from_row(episode):
    """Parsed ad markers from an episode row; None when absent or unreadable."""
    return parse_ad_markers(episode.get('ad_markers_json'))
