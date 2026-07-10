"""Offline queue re-drive for deferred episodes (#482).

When the LLM provider or Whisper endpoint is unreachable, episodes defer
instead of failing (see _handle_processing_failure). This tick, run from the
background queue processor's ~5-minute maintenance block, owns the rest of
the lifecycle: expire deferrals past the TTL, probe the services deferred
episodes are waiting on, and re-queue them when a service is reachable again.

The tick keeps running for existing deferred episodes even when the toggle is
later disabled -- the toggle gates only NEW deferrals, so nothing strands.
"""
import logging

import llm_client
import transcriber
from webhook_service import fire_event, EVENT_EPISODE_FAILED

logger = logging.getLogger('podcast.refresh')

TTL_HOURS_DEFAULT = 48
TTL_HOURS_MIN = 1
TTL_HOURS_MAX = 720

_SERVICE_PROBES = {
    'llm': lambda: llm_client.check_llm_connectivity(),
    'whisper': lambda: transcriber.check_whisper_connectivity(),
}


def is_offline_queue_enabled(db) -> bool:
    """Offline queue toggle; off by default."""
    try:
        return db.get_setting_bool('offline_queue_enabled', default=False)
    except Exception:
        return False


def get_offline_queue_ttl_hours(db) -> int:
    """Configured TTL in hours, clamped to [1, 720]; default 48."""
    try:
        ttl = int(db.get_setting('offline_queue_ttl_hours') or TTL_HOURS_DEFAULT)
    except (TypeError, ValueError):
        ttl = TTL_HOURS_DEFAULT
    return max(TTL_HOURS_MIN, min(ttl, TTL_HOURS_MAX))


def offline_queue_tick(db) -> None:
    """One maintenance pass: expire by TTL, probe, re-queue."""
    deferred = db.get_deferred_episodes()
    if not deferred:
        # Installs without deferred episodes (including everyone with the
        # feature off) pay one COUNT-style query and nothing else.
        return

    expired = db.expire_deferred_episodes(get_offline_queue_ttl_hours(db))
    for episode in expired:
        try:
            # Keep the audit trail consistent with every other permanent
            # failure: the history views are built from processing_history.
            db.record_processing_history(
                podcast_id=episode['podcast_id'],
                podcast_slug=episode['podcast_slug'],
                podcast_title=episode.get('podcast_title'),
                episode_id=episode['episode_id'],
                episode_title=episode.get('title'),
                status='failed',
                error_message=episode.get('error_message'),
            )
        except Exception as hist_err:
            logger.warning(
                f"Offline queue: history record failed for "
                f"{episode['podcast_slug']}:{episode['episode_id']}: {hist_err}")
        try:
            fire_event(
                event=EVENT_EPISODE_FAILED,
                episode_id=episode['episode_id'],
                slug=episode['podcast_slug'],
                episode_title=episode.get('title'),
                # No processing ran for a TTL expiry; the fields are required
                # by the payload, not meaningful here.
                processing_time=0.0,
                llm_cost=0.0,
                error_message=episode.get('error_message'),
                podcast_name=episode.get('podcast_title'),
            )
        except Exception as wh_err:
            logger.warning(
                f"Offline queue: webhook fire failed for "
                f"{episode['podcast_slug']}:{episode['episode_id']}: {wh_err}")

    expired_ids = {e['id'] for e in expired}
    waiting_services = {
        (e.get('deferred_service') or 'llm')
        for e in deferred if e['id'] not in expired_ids
    }
    reachable = {
        service for service in waiting_services
        if _SERVICE_PROBES.get(service, lambda: False)()
    }
    requeued = db.requeue_deferred_episodes(reachable) if reachable else 0

    if expired or requeued:
        logger.info(
            f"Offline queue tick: {len(expired)} expired past TTL, "
            f"{requeued} re-queued (reachable: {sorted(reachable) or 'none'})")
