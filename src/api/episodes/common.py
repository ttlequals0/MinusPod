"""Shared episode route helpers: job state, marker parsing and constants."""

from utils.markers import parse_ad_markers
from database.podcasts import is_youtube_feed
from utils.json_fields import load_json_list
from youtube import settings as yt_settings, sponsorblock
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


def rating_json(episode):
    """The episode's quality rating object, or None when unrated."""
    if episode.get('quality_rating') is None:
        return None
    return {'value': episode['quality_rating'], 'notes': episode.get('quality_notes'),
            'ratedAt': episode.get('quality_rated_at')}


def _markers_from_row(episode):
    """Parsed ad markers from an episode row; None when absent or unreadable."""
    return parse_ad_markers(episode.get('ad_markers_json'))


_SB_VERIFIED_STATES = ('done', 'exhausted', 'llm')


def sponsorblock_block_reason(db, podcast, episode) -> str | None:
    if not is_youtube_feed(podcast):
        return 'not_youtube'
    if episode.get('status') != EpisodeStatus.PROCESSED:
        return 'not_completed'
    if load_json_list(episode.get('sb_segment_uuids')):
        return 'community_marks'
    if not episode.get('youtube_video_id'):
        return 'no_video_id'
    if episode.get('sb_state') not in _SB_VERIFIED_STATES:
        return 'marks_unverified'
    if not db.get_setting_bool('youtube_sb_submit_enabled', False):
        return 'disabled'
    if not yt_settings.sb_user_id(db):
        return 'no_user_id'
    if sponsorblock.is_unreachable(db):
        return 'service_down'
    return None
