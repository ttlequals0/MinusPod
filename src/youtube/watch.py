"""SponsorBlock gate: schedule YouTube episodes before they enter the queue."""
from datetime import datetime, timedelta, timezone

from youtube import settings as yt_settings


def schedule_new_episode(db, slug, episode_id, podcast) -> bool:
    """First sighting of a video (no SponsorBlock state yet): wait out the processing delay before the first SponsorBlock check."""
    resolved = yt_settings.resolve_feed(db, podcast)
    due = datetime.now(timezone.utc) + timedelta(minutes=resolved.process_delay_minutes)
    return db.schedule_sponsorblock_wait(slug, episode_id, due.isoformat())
