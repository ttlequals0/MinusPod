"""Processing failure handling and requeue after a hold."""

import logging
import time
from utils.time import utc_now_iso

from config import MAX_EPISODE_RETRIES

from llm_client import (
    is_rate_limit_error,
    is_auth_error, ProviderAccountChangedError, ProviderRateLimitedError,
    get_episode_token_totals,
)

from database.queue import compute_queue_priority
from offline_queue import is_offline_queue_enabled, record_probe_state
from rate_limit_hold import (
    HOLD_REASON_ACCOUNT_CHANGED, get_active_hold, hold_message,
    hold_queue_for_provider_limit, resolve_hold_scope,
)

from utils.circuit_breaker import CircuitBreakerOpen
import run_context
from utils.constants import (
    EpisodeStatus, REPROCESS_SOURCE_JIT,
)

from utils.errors import (
    AudioNotReadyError, ModelLoadError, ServiceUnavailableError,
)

from webhook_service import (
    fire_event, EVENT_EPISODE_FAILED,
    fire_service_offline_event,
)

from main_app.pipeline import common, finalize, admission
from main_app.pipeline.shared import db

audio_logger = logging.getLogger('podcast.audio')


def _requeue_episode_after_hold(db, slug, episode_id, episode_title,
                                episode_data, hold_until, error, message=None):
    """Send a held episode back to PENDING, keeping its queue position.
    Shared by real-429, manual-cap, and account-change defers (#696, #747)."""
    db.upsert_episode(
        slug, episode_id,
        status=EpisodeStatus.PENDING.value,
        error_message=message or hold_message(hold_until, error),
    )
    # Release the claimed queue row in place so the episode keeps its
    # priority and position. A run started outside the queue processor
    # has no row, so it gets one at the boost its request would carry.
    if not db.reopen_claimed_queue_row(slug, episode_id):
        # episode_data predates the run; a JIT play may have had no row
        # then, so read the row the run wrote.
        row = db.get_episode(slug, episode_id) or episode_data or {}
        podcast = db.get_podcast_by_slug(slug) or {}
        # Only Play and Reprocess start outside the queue, so this is user
        # intent: the mark clears the drainer's auto-process gate. Never
        # over an existing stamp, which would relabel someone's reprocess.
        if not row.get('reprocess_requested_at'):
            db.upsert_episode(slug, episode_id,
                              reprocess_requested_at=utc_now_iso(),
                              reprocess_source=REPROCESS_SOURCE_JIT)
        db.upsert_episode_for_processing(
            slug, episode_id, row.get('original_url'),
            title=episode_title, published_at=row.get('published_at'),
            description=row.get('description'),
            priority=compute_queue_priority(
                podcast.get('queue_priority'), row.get('published_at'),
                manual=True),
        )


def _handle_processing_failure(slug, episode_id, episode_title, podcast_name,
                                episode_data, error, start_time, run_stats=None):
    """Handle processing failure: GPU cleanup, retry logic, error recording."""
    common._require_publication_owner(slug, episode_id)
    processing_time = time.time() - start_time

    try:
        from transcriber.local import _unload_local_models
        from utils.gpu import clear_gpu_memory
        clear_gpu_memory()
        _unload_local_models()
        audio_logger.info(f"[{slug}:{episode_id}] Cleaned up GPU memory after failure")
    except Exception as cleanup_err:
        audio_logger.warning(f"[{slug}:{episode_id}] Failed to clean up GPU memory: {cleanup_err}")

    common._publish_status('fail_job', slug, episode_id)

    # Provider account change: this run's routes were frozen against an
    # account the operator has since replaced, so its endpoint and the slot's
    # current key no longer belong together. Requeue and drop the frozen
    # routes; the next attempt resolves against the new configuration.
    ctx = run_context.current()
    account_changed = isinstance(error, ProviderAccountChangedError) or bool(
        admission._stale_snapshot_phases(ctx.route_snapshot if ctx else None))
    if account_changed:
        admission.clear_route_snapshot(ctx.run_id if ctx else None)
        _requeue_episode_after_hold(
            db, slug, episode_id, episode_title, episode_data, None, error,
            message=f"Requeued ({HOLD_REASON_ACCOUNT_CHANGED}): {error}")
        audio_logger.warning(
            f"[{slug}:{episode_id}] Provider account changed mid-run; requeued "
            f"to re-resolve routes")
        return

    # Rate-limit hold (#696): a 429 with a reset sends the episode back to
    # the queue and pauses new starts until the reset. Runs before the
    # offline-queue branch: throttling is not an outage. retry_count untouched.
    if isinstance(error, ProviderRateLimitedError):
        provider_key, credential_slot = resolve_hold_scope(error)
        if getattr(error, 'manual', False):
            # Manual RPM/RPD/TPM cap: the pre-call backstop already recorded
            # the hold, so read it directly and defer regardless of the #696
            # toggle (a real 429 still routes through the toggle-gated path).
            hold_until = get_active_hold(db, provider_key, credential_slot)[0]
        else:
            hold_until = hold_queue_for_provider_limit(
                db, error, slug=slug, episode_id=episode_id, podcast_name=podcast_name,
                provider_key=provider_key,
                credential_slot=credential_slot)
        if hold_until:
            _requeue_episode_after_hold(
                db, slug, episode_id, episode_title, episode_data, hold_until, error)
            return

    # Offline queue (#482): endpoint-down failures defer instead of failing.
    # Only typed exceptions qualify -- never string matching -- so genuine
    # errors keep today's retry/permanent path. retry_count is untouched so a
    # deferred episode cannot drift toward permanently_failed while the
    # service is down. No history row or webhook: nothing "failed" yet.
    if (isinstance(error, (ServiceUnavailableError, CircuitBreakerOpen))
            and is_offline_queue_enabled(db)):
        service = getattr(error, 'service', 'llm')
        # deferred_at marks when the episode FIRST entered the offline queue
        # and survives re-drive cycles, so the TTL bounds total time in the
        # deferred lifecycle. Stamping fresh on every re-deferral would let a
        # flapping endpoint (probe up, calls failing) reset the clock forever.
        first_deferred_at = (episode_data or {}).get('deferred_at') or utc_now_iso()
        db.upsert_episode(
            slug, episode_id,
            status=EpisodeStatus.DEFERRED.value,
            error_message=f"Deferred ({service} endpoint unreachable): {error}",
            deferred_at=first_deferred_at,
            deferred_service=service,
        )
        audio_logger.warning(
            f"[{slug}:{episode_id}] Offline queue: deferred until the "
            f"{service} endpoint is reachable again"
        )
        # Seed the outage verdict so the next tick's True probe reads as a
        # recovery and fires Service Reachable.
        record_probe_state(db, service, False)
        fire_service_offline_event(
            service=service, error_message=error, slug=slug,
            episode_id=episode_id, podcast_name=podcast_name)
        return

    audio_logger.error(f"[{slug}:{episode_id}] Failed: {error} ({processing_time:.1f}s)")

    transient = common.is_transient_error(error)
    current_retry = (episode_data.get('retry_count', 0) or 0) if episode_data else 0

    # 429 retries don't burn retry_count (#238); the held-429 type counts too,
    # so with the hold disabled it rides the legacy rate-limited path.
    rate_limited = is_rate_limit_error(error)

    # Auth outages are operator-fixable and can outlast any retry ladder, so
    # they must not burn retry_count or trip permanently_failed, regardless
    # of how is_transient_error classifies the wrapped error text.
    auth_outage = is_auth_error(error)

    if auth_outage:
        new_retry_count = current_retry
        new_status = EpisodeStatus.FAILED.value
        audio_logger.warning(
            f"[{slug}:{episode_id}] Auth-class LLM failure; retry budget "
            f"unchanged ({current_retry}/{MAX_EPISODE_RETRIES})"
        )
    elif transient:
        if rate_limited:
            new_retry_count = current_retry
            new_status = EpisodeStatus.FAILED.value
            audio_logger.info(
                f"[{slug}:{episode_id}] Rate-limited, will retry without incrementing "
                f"retry_count (currently {current_retry}/{MAX_EPISODE_RETRIES})"
            )
        else:
            new_retry_count = current_retry + 1
            if new_retry_count >= MAX_EPISODE_RETRIES:
                new_status = EpisodeStatus.PERMANENTLY_FAILED.value
                audio_logger.warning(f"[{slug}:{episode_id}] Max retries reached ({MAX_EPISODE_RETRIES}), marking as permanently failed")
            else:
                new_status = EpisodeStatus.FAILED.value
                cause = ('Transcription model failed to load' if isinstance(error, ModelLoadError)
                         else 'Transient error')
                audio_logger.info(f"[{slug}:{episode_id}] {cause}, will retry (attempt {new_retry_count}/{MAX_EPISODE_RETRIES})")
    else:
        new_status = EpisodeStatus.PERMANENTLY_FAILED.value
        new_retry_count = current_retry
        audio_logger.warning(f"[{slug}:{episode_id}] Permanent error, not retrying: {type(error).__name__}: {error}")

    db.upsert_episode(slug, episode_id, status=new_status,
        retry_count=new_retry_count, error_message=str(error))

    token_totals = get_episode_token_totals()
    audio_logger.info(f"[{slug}:{episode_id}] Token totals: in={token_totals['input_tokens']} out={token_totals['output_tokens']} cost=${token_totals['cost']:.6f}")

    # A retryable failure to even fetch the enclosure processed nothing, so it
    # is not a run: the row would only inflate the reprocess number the real
    # run is stamped with. The episode row still carries the error and retry.
    if isinstance(error, AudioNotReadyError) and new_status == EpisodeStatus.FAILED.value:
        audio_logger.info(
            f"[{slug}:{episode_id}] Audio was never fetched; not recording a "
            f"processing run for this attempt")
    else:
        try:
            # Partial stats: whatever the run gathered before failing.
            finalize._record_history_row(
                db, slug, episode_id, episode_title, podcast_name,
                status='failed', processing_time=processing_time,
                ads_detected=0, token_totals=token_totals,
                error_message=str(error), run_stats=run_stats)
        except Exception as hist_err:
            audio_logger.warning(f"[{slug}:{episode_id}] Failed to record history: {hist_err}")

    if new_status == EpisodeStatus.PERMANENTLY_FAILED:
        try:
            fire_event(
                event=EVENT_EPISODE_FAILED,
                episode_id=episode_id, slug=slug, episode_title=episode_title,
                processing_time=processing_time, llm_cost=token_totals['cost'],
                error_message=str(error),
                podcast_name=podcast_name,
            )
        except Exception as wh_err:
            audio_logger.warning(f"[{slug}:{episode_id}] Webhook fire failed: {wh_err}")
