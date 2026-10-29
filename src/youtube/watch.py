"""SponsorBlock gate: schedule YouTube episodes before they enter the queue."""
from datetime import datetime, timedelta, timezone

from youtube import settings as yt_settings


def schedule_new_episode(db, slug, episode_id, podcast) -> bool:
    """First sighting of a video: wait out the processing delay before the first SponsorBlock check."""
    resolved = yt_settings.resolve_feed(db, podcast)
    due = datetime.now(timezone.utc) + timedelta(minutes=resolved.process_delay_minutes)
    return db.update_episode_sponsorblock(slug, episode_id, sb_state='waiting', sb_attempts=0,
                                          sb_next_check_at=due.isoformat())
