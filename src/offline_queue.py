"""Re-drive deferred episodes against their current effective routes."""
import logging

import failover
from config import (
    DEFER_SERVICE_LLM, coerce_bool_setting,
)
from utils.time import utc_now_iso
from webhook_service import fire_event, fire_service_reachable_event, EVENT_EPISODE_FAILED

logger = logging.getLogger('podcast.refresh')

TTL_HOURS_DEFAULT = 48
TTL_HOURS_MIN = 1
TTL_HOURS_MAX = 720

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


def notify_expired_episodes(db, expired) -> None:
    """History + webhook for TTL-expired deferrals, matching the
    permanent-failure audit trail."""
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


def probe_state_keys(service: str) -> tuple[str, str]:
    """Settings keys holding the last probe verdict and time for `service`."""
    return f'offline_probe_{service}_reachable', f'offline_probe_{service}_at'


def record_probe_state(db, service: str, reachable: bool) -> None:
    """Persist one probe verdict so the status API can say what is down
    without re-probing on the read path."""
    reachable_key, at_key = probe_state_keys(service)
    try:
        db.set_setting(reachable_key, 'true' if reachable else 'false',
                       is_default=False)
        db.set_setting(at_key, utc_now_iso(), is_default=False)
    except Exception as e:
        logger.debug(f"Could not record probe state for {service}: {e}")


def get_probe_state(db, service: str) -> tuple[bool | None, str | None]:
    """Last probe verdict and time for `service`. Reachability is None until
    the tick has probed once, which means "not checked yet", not "up"."""
    reachable_key, at_key = probe_state_keys(service)
    raw = db.get_setting(reachable_key)
    return (None if raw is None else coerce_bool_setting(raw)), db.get_setting(at_key)


def offline_queue_tick(db, required_targets_resolver) -> None:
    """One maintenance pass: expire by TTL, probe, re-queue."""
    deferred = db.get_deferred_episodes()
    if not deferred:
        # Installs without deferred episodes (including everyone with the
        # feature off) pay one COUNT-style query and nothing else.
        return

    expired = db.expire_deferred_episodes(get_offline_queue_ttl_hours(db))
    notify_expired_episodes(db, expired)

    expired_ids = {e['id'] for e in expired}
    waiting_services = {
        (e.get('deferred_service') or DEFER_SERVICE_LLM)
        for e in deferred if e['id'] not in expired_ids
    }
    remaining = [e for e in deferred if e['id'] not in expired_ids]
    requeued = 0
    if remaining:
        requeued = _requeue_resolved_episodes(
            db, remaining, waiting_services, required_targets_resolver(db))
    if expired or requeued:
        logger.info("Offline queue tick: %s expired past TTL, %s re-queued",
                    len(expired), requeued)


def _resolved_targets(episodes, resolve_targets):
    targets_by_id = {}
    targets = set()
    for episode in episodes:
        service = episode.get('deferred_service') or DEFER_SERVICE_LLM
        try:
            required = resolve_targets(service, episode)
            required = list(dict.fromkeys(required)) if required is not None else None
        except Exception as exc:
            logger.warning("Offline queue route resolution failed: %s", exc)
            required = None
        targets_by_id[episode['id']] = required
        targets.update(required or ())
    return targets_by_id, targets


def _requeue_resolved_episodes(db, episodes, services, resolve_targets) -> int:
    targets_by_id, targets = _resolved_targets(episodes, resolve_targets)
    probed_targets = set(targets)

    if probed_targets:
        failover.ensure_fresh_probes(sorted(probed_targets))

    # Probing can trigger or cancel failover, changing the route for this tick.
    targets_by_id, targets = _resolved_targets(episodes, resolve_targets)
    additional_targets = targets - probed_targets
    if additional_targets:
        failover.ensure_fresh_probes(sorted(additional_targets))
        targets_by_id, targets = _resolved_targets(episodes, resolve_targets)
    probe_results = {target: failover.current_probe_state(target)['reachable'] is True
                     for target in targets}
    eligible = {service: set() for service in services}
    probe_required = {service: [] for service in services}
    for episode in episodes:
        service = episode.get('deferred_service') or DEFER_SERVICE_LLM
        required = targets_by_id[episode['id']]
        if required is None:
            continue
        if all(probe_results.get(target, False) for target in required):
            eligible[service].add(episode['id'])
        if required:
            probe_required[service].append(required)

    recovered = set()
    for service in services:
        required = probe_required[service]
        if not required:
            continue
        was_reachable, _ = get_probe_state(db, service)
        is_reachable = all(all(probe_results.get(target, False) for target in group)
                           for group in required)
        record_probe_state(db, service, is_reachable)
        if was_reachable is False and is_reachable:
            recovered.add(service)

    requeued = 0
    for service in sorted(services):
        ids = eligible[service]
        if not ids:
            continue
        count = db.requeue_deferred_episodes({service}, episode_ids=ids)
        requeued += count
        if service in recovered:
            fire_service_reachable_event(service=service, requeued=count)
    return requeued
