"""Episode processing orchestrator: process_episode and the background start path."""

import json
import logging
import os
import shutil
import threading
import time
from concurrent.futures import Future
from contextlib import nullcontext
from ad_validator import user_trimmed_keep_ranges
from audio_processor import AudioProcessor
from cancel import (
    ProcessingCancelled, ProcessingOwnershipLost, _check_cancel,
    _cancel_events, _cancel_events_lock,
)

from processing_queue import ProcessingQueue, is_processing_paused
from utils.markers import (reject_barriers)
from utils.pattern_catalog import pattern_catalog_scope
from whisper_pool import get_pool, is_background_follower
from config import (
    PROCESSING_MODE_PASSTHROUGH,
    PROCESSING_MODE_SKIP_DETECTION,
    PROCESSING_MODE_CUE_ONLY,
    CUE_ONLY_SAFETY_HOLD_NEW,
    CUE_ONLY_PROVEN_EPISODES,
    count_not_cut, is_pending_review, is_keep_like,
    resolve_processing_mode,
    resolve_skip_second_pass,
    resolve_skip_transcription,
    resolve_cue_only_safety,
    cue_only_missing_roles_for_feed,
    resolve_max_ad_duration_override,
    resolve_ad_detection_exclude_start_seconds,
    resolve_cue_gated_approval,
    coerce_bool_setting,
)

from database.podcasts import is_local_feed
from llm_client import (
    start_episode_token_tracking, get_effective_provider,
)

from rate_limit_hold import (
    enforce_provider_rate_limit, is_queue_paused,
)

from positional_prior import format_prior_hint, load_positional_prior
from text_recurrence import find_recurring_spans
import failover
import run_context
from reprocess_modes import (
    FORCE_TRANSCRIBE_MODES, clear_episode_for_mode,
)

from utils.constants import (
    CANCELED_ERROR_MESSAGE, EpisodeStatus, REPROCESS_SOURCE_POLICY,
)

from utils.episode_paths import episode_relative_path, published_episode_version
from utils.gpu import get_available_memory_gb

audio_logger = logging.getLogger('podcast.audio')

from main_app.episode_context import EpisodeContext
from main_app.pipeline import common, transcribe, signals, detect, review, cuts, keep_rules, verify, publish, finalize, admission, failure, recut
# Stage modules resolve every singleton through this module (pipeline.shared).
from main_app import (db, storage, transcriber, ad_detector, audio_processor,
                      status_service, shutdown_event)
# Patch surface: tests rebind processing.<name>; pipeline.shared proxies read it here.
from main_app import audio_analyzer, sponsor_service, pattern_service  # noqa: F401


def _cleanup_cancelled_processing_output(slug, episode_id, run_id, queue,
                                         episode_at_start, keep_original):
    """Return whether the run remains owned and its replacement is published."""
    if not queue.owns(run_id, allow_cancel_requested=True):
        return False, False

    current_episode = db.get_episode(slug, episode_id)
    if current_episode is None:
        return False, False

    rows = (episode_at_start or {}, current_episode)
    versions = []
    for row in rows:
        if row.get('processed_file'):
            version = published_episode_version(row)
            if version is None:
                audio_logger.warning(
                    f"[{slug}:{episode_id}] Skipping cancelled-output cleanup: "
                    "processed-file metadata is invalid"
                )
                return queue.owns(run_id, allow_cancel_requested=True), False
            versions.append(version)
        else:
            versions.append(None)

    previous_version, current_version = versions
    if previous_version is not None and not current_episode.get('processed_file'):
        audio_logger.warning(
            f"[{slug}:{episode_id}] Skipping cancelled-output cleanup: "
            "published-file metadata disappeared during the run"
        )
        return queue.owns(run_id, allow_cancel_requested=True), False

    replacement_version = transcribe._next_processed_version(episode_at_start or {})
    replacement_rel = episode_relative_path(episode_id, replacement_version)
    if (current_version == replacement_version
            and current_episode.get('processed_file') == replacement_rel):
        return True, True

    if (current_version is not None
            and current_version not in {previous_version, replacement_version}):
        audio_logger.warning(
            f"[{slug}:{episode_id}] Skipping cancelled-output cleanup: "
            "published version changed during the run"
        )
        return queue.owns(run_id, allow_cancel_requested=True), False

    if previous_version is None and current_version is None:
        rows_never_published = all(
            not row.get('processed_file')
            and not row.get('processed_at')
            and row.get('status') != EpisodeStatus.PROCESSED.value
            and (row.get('processed_version') is None
                 or (type(row.get('processed_version')) is int
                     and row.get('processed_version') == 0))
            for row in rows
        )
        if not rows_never_published:
            audio_logger.warning(
                f"[{slug}:{episode_id}] Skipping cancelled-output cleanup: "
                "publication metadata is incomplete"
            )
            return queue.owns(run_id, allow_cancel_requested=True), False
        storage.delete_processed_file(
            slug, episode_id, keep_original=keep_original,
            can_delete=lambda: queue.owns(
                run_id, allow_cancel_requested=True),
        )
        return queue.owns(run_id, allow_cancel_requested=True), False

    replacement_path = storage.get_episode_path(
        slug, episode_id, version=replacement_version)
    if replacement_path.exists():
        if not queue.owns(run_id, allow_cancel_requested=True):
            return False, False
        replacement_path.unlink()
        audio_logger.info(
            f"[{slug}:{episode_id}] Removed cancelled replacement audio: "
            f"{replacement_path.name}"
        )
    return True, False


def _process_episode_background(slug, episode_id, original_url, title, podcast_name, description, artwork_url, published_at=None, cancel_event=None, run_id=None):
    """Background thread wrapper for process_episode with queue management."""
    ctx = run_context.begin(slug, episode_id, run_id=run_id)
    ctx.llm_cancel_check = lambda: _check_cancel(cancel_event, slug, episode_id, run_id)
    ctx.llm_cancel_exceptions = (ProcessingCancelled, ProcessingOwnershipLost)
    queue = ProcessingQueue()
    start_time = time.time()
    # The run log is bracketed here, not inside process_episode: the fallback
    # failure handler below writes a history row too, and it needs a live
    # recorder to finalize onto it (#660).
    recorder = finalize._start_run_log(slug, episode_id)
    episode_at_start = None
    try:
        episode_at_start = db.get_episode(slug, episode_id)
        # Both detection passes read the active pattern catalog once per run.
        with pattern_catalog_scope():
            process_episode(slug, episode_id, original_url, title, podcast_name,
                            description, artwork_url, published_at,
                            cancel_event=cancel_event, run_id=run_id)
    except ProcessingCancelled:
        # Clear any transaction the aborted run left open so the status
        # reset below writes on a clean connection (issue #566).
        db.clear_leaked_transaction(audio_logger, 'episode processing (cancel)')
        if queue.owns(run_id, allow_cancel_requested=True):
            audio_logger.info(f"[{slug}:{episode_id}] Cancelled - cleaning up partial files")
            try:
                podcast_row = db.get_podcast_by_slug(slug)
                still_owned, replacement_published = (
                    _cleanup_cancelled_processing_output(
                        slug, episode_id, run_id, queue, episode_at_start,
                        keep_original=is_local_feed(podcast_row)))
            except Exception as cleanup_err:
                audio_logger.warning(f"[{slug}:{episode_id}] Failed to clean up partial file: {cleanup_err}")
                still_owned = queue.owns(run_id, allow_cancel_requested=True)
                replacement_published = False
            try:
                if still_owned and queue.owns(run_id, allow_cancel_requested=True):
                    if not replacement_published:
                        db.upsert_episode(
                            slug, episode_id, status=EpisodeStatus.PENDING.value,
                            error_message=CANCELED_ERROR_MESSAGE)
                    status_service.complete_job(slug, episode_id, run_id=run_id)
            except Exception as db_err:
                audio_logger.warning(f"[{slug}:{episode_id}] Failed to reset status after cancel: {db_err}")
    except ProcessingOwnershipLost as exc:
        audio_logger.error("[%s:%s] Stopping without publishing: %s", slug, episode_id, exc)
    except Exception as e:
        # This outer handler only fires if process_episode's own error handling
        # raises (e.g., DB unreachable during _handle_processing_failure).
        # It's a best-effort retry of failure bookkeeping.
        audio_logger.error(f"[{slug}:{episode_id}] Background processing failed: {e}")
        # Roll back first: if the failure leaked a transaction, the
        # bookkeeping below would otherwise fail on the same connection.
        db.clear_leaked_transaction(audio_logger, 'episode processing (failure)')
        try:
            episode_data = db.get_episode(slug, episode_id)
            failure._handle_processing_failure(slug, episode_id, title, podcast_name,
                                       episode_data, e, start_time)
        except Exception as handler_err:
            audio_logger.error(f"[{slug}:{episode_id}] Failed to handle failure: {handler_err}")
    finally:
        finalize._end_run_log(recorder)
        # Backstop for swallowed write failures anywhere in the run: this
        # thread's connection must not leave here with an open transaction.
        db.clear_leaked_transaction(audio_logger, 'episode processing')
        cancelled = cancel_event is not None and cancel_event.is_set()
        queue.release(run_id, terminal_state='interrupted' if cancelled else 'finished')
        with _cancel_events_lock:
            _cancel_events.pop(run_id, None)
        run_context.end(ctx)


def start_background_processing(slug, episode_id, original_url, title, podcast_name, description, artwork_url, published_at=None):
    """
    Start processing in background thread.

    Returns:
        Tuple of (started: bool, reason: str)
        - (True, "started") if processing was started
        - (False, "already_processing") if this episode is already being processed
        - (False, "queue_only") if this process is a background follower, not the leader
        - (False, "queue_busy:slug:episode_id") if another episode is processing
        - (False, "rate_limit_paused") while a rate-limit hold is active
    """
    queue = ProcessingQueue()

    if shutdown_event.is_set():
        return False, "shutdown_draining"
    if is_processing_paused(db):
        return False, "processing_paused"

    # Only the leader's dispatcher refreshes on a loop, so a non-leader worker
    # would otherwise hold whatever settings it saw at boot. TTL-throttled.
    get_pool().refresh()

    # Check if already processing this episode
    if queue.is_processing(slug, episode_id):
        return False, "already_processing"

    # Only the background leader runs episodes, so every per-run state and
    # every GPU model lives in one process. Callers enqueue on any refusal.
    if is_background_follower():
        return False, "queue_only"

    # Rate-limit hold (#696): the one choke point every start goes through,
    # so a Play or Reprocess waits in the queue like the rest. Provider- and
    # slot-scoped: checked against every (provider, credential_slot) this
    # run's phases would use, so a run needing only healthy accounts is
    # admitted even while a different provider or a different account on
    # the same provider is held, and a run needing a held account is
    # refused before it can spend a call on a healthy one it cannot finish
    # with. Mode-scoped too: a run that makes no LLM call is never refused.
    required_providers = admission._required_providers_for_admission(slug, episode_id)
    if required_providers is None:
        if is_queue_paused(db):
            return False, "rate_limit_paused"
    else:
        # Manual RPM/RPD caps (#747): record a hold for any required account
        # already at its limit, so the is_queue_paused check below refuses it.
        for provider, slot in required_providers:
            enforce_provider_rate_limit(db, provider, slot)
        if any(is_queue_paused(db, provider, slot) for provider, slot in required_providers):
            return False, "rate_limit_paused"

    # Check if queue is busy with another episode
    run_id = queue.acquire(slug, episode_id, limit=get_pool().max_episodes, timeout=0)
    if not run_id:
        current = queue.get_current()
        if current:
            return False, f"queue_busy:{current[0][0]}:{current[0][1]}"
        return False, "queue_busy"

    # Update StatusService IMMEDIATELY after lock acquired (prevents race condition)
    # This ensures the new episode is tracked before any other episode can start
    status_service.start_job(slug, episode_id, title, podcast_name, run_id=run_id)

    # Create cancel event for cooperative cancellation
    cancel_event = threading.Event()
    with _cancel_events_lock:
        _cancel_events[run_id] = cancel_event

    # Start background thread
    processing_thread = threading.Thread(
        target=_process_episode_background,
        args=(slug, episode_id, original_url, title, podcast_name, description, artwork_url, published_at, cancel_event, run_id),
        daemon=True
    )
    try:
        processing_thread.start()
    except Exception as e:
        # Nothing will run, so nothing will release the slot in its finally.
        # Hand back "queue_busy" and the caller enqueues instead.
        audio_logger.error(f"[{slug}:{episode_id}] Could not start processing thread: {e}")
        with _cancel_events_lock:
            _cancel_events.pop(run_id, None)
        status_service.fail_job(slug, episode_id, run_id=run_id)
        queue.release(run_id, terminal_state='interrupted')
        return False, "queue_busy"

    return True, "started"


def process_episode(slug: str, episode_id: str, episode_url: str,
                   episode_title: str = "Unknown", podcast_name: str = "Unknown",
                   episode_description: str = None, episode_artwork_url: str = None,
                   episode_published_at: str = None, cancel_event: threading.Event = None,
                   run_id: str = None):
    """Process a single episode through the full ad removal pipeline.

    Pipeline stages:
    1. Download audio and transcribe (or load existing transcript)
    2. Audio analysis (volume + transition detection)
    3. First-pass ad detection via Claude
    4. Boundary refinement, roll detection, validation
    4b. Optional ad reviewer (opt-in; off by default)
    5. Audio processing (FFMPEG cut)
    6. Verification pass (second-pass detection on processed audio,
       with the same optional reviewer applied to its output)
    7. Generate Podcasting 2.0 assets (VTT transcript, chapters)
    8. Finalize (update DB, record history, refresh RSS)
    """
    start_time = time.time()
    start_episode_token_tracking()
    ctx = run_context.current()
    if run_id:
        if ctx is not None:
            ctx.run_id = run_id
        _check_cancel(cancel_event, slug, episode_id, run_id)

    route_snapshot = admission._resolve_or_load_route_snapshot(run_id)
    admission._assert_route_snapshot_current(route_snapshot)
    if ctx is not None and route_snapshot is not None:
        ctx.set_route_snapshot(route_snapshot)

    episode_data = db.get_episode(slug, episode_id)
    reprocess_mode = episode_data.get('reprocess_mode') if episode_data else None
    # Only 'full' skips the learned-pattern DB. 'reprocess', 'llm' (#349) and the
    # default first run all keep patterns; any new mode keeps them unless added here.
    skip_patterns = reprocess_mode == 'full'

    if reprocess_mode:
        audio_logger.info(f"[{slug}:{episode_id}] Reprocess mode: {reprocess_mode} (skip_patterns={skip_patterns})")

    # Recut (issue #422): re-cut the retained original from the current ad
    # detections, no download/transcribe/detect/LLM. Branches before the full
    # pipeline; preconditions are enforced by the reprocess API.
    if reprocess_mode == 'recut':
        return recut._recut_episode(slug, episode_id, episode_title, podcast_name,
                              episode_description, start_time, cancel_event)

    podcast_settings = db.get_podcast_by_slug(slug)
    podcast_description = (podcast_settings or {}).get('description') or None
    detection_notes = (podcast_settings or {}).get('detection_notes')
    if detection_notes:
        audio_logger.info(f"[{slug}:{episode_id}] Including detection notes ({len(detection_notes)} chars)")

    # Effective mode: a per-episode pass-through override (#746) wins over
    # the feed mode; otherwise the feed mode's own precedence (passthrough >
    # skip-detection > keep-content > cue_only > standard) applies. The
    # branches below check the resolved mode, never the raw columns.
    processing_mode = resolve_processing_mode(podcast_settings, episode_data)

    # Pass-through (#521): the feed opted out of processing entirely.
    # Full and AI reprocesses also land here while the toggle is on; the
    # per-episode Recut action branches earlier and still works on
    # episodes that have retained originals and markers.
    if processing_mode == PROCESSING_MODE_PASSTHROUGH:
        return recut._passthrough_episode(slug, episode_id, episode_url,
                                     episode_title, podcast_name,
                                     episode_description, episode_artwork_url,
                                     episode_published_at, start_time,
                                     episode_data, cancel_event=cancel_event,
                                     podcast=podcast_settings)

    # Skip ad detection (#538): episodes still get transcription, chapters,
    # and a transcript, but the detection stages and the cut are skipped.
    skip_detection = processing_mode == PROCESSING_MODE_SKIP_DETECTION

    # Skip verification (#599): pass 1 still runs and cuts, the second LLM
    # sweep over its output does not.
    skip_second_pass = resolve_skip_second_pass(podcast_settings, db=db)

    # Cue-only preset: skip the LLM and pass 2; skip transcription too if
    # the feed also opts into that.
    cue_only = processing_mode == PROCESSING_MODE_CUE_ONLY
    skip_transcription_active = cue_only and resolve_skip_transcription(podcast_settings)

    try:
        active_phases = admission._active_phases_for_admission(
            slug, episode_id, snapshot=route_snapshot)
        targets = failover.run_probe_targets(
            active_phases,
            whisper_required=not skip_transcription_active and reprocess_mode != 'llm',
        )
        failover.ensure_fresh_probes(targets)
    except Exception as exc:
        audio_logger.debug(f"pre-run failover probe skipped: {exc}")

    # Per-run pipeline stats (#519), recorded as JSON with the history row
    # and renamed to API casing in api/episodes/core.py. Defined before the try
    # so the failure handler can persist whatever was gathered up to the
    # point of failure.
    run_stats = {'mode': reprocess_mode or 'auto'}
    if skip_detection:
        run_stats['detection_skipped'] = True
    elif skip_second_pass:
        # Not recorded alongside detection_skipped, which already implies it.
        run_stats['verification_skipped'] = True
    if cue_only:
        run_stats['cue_only'] = True
        run_stats['verification_skipped'] = True
    if skip_transcription_active:
        run_stats['transcription_skipped'] = True

    provider_reservations: dict[str, str] = {}
    provider_attempted = False
    markers_published = False

    def _mark_markers_published():
        nonlocal markers_published
        markers_published = True

    def _reserve_provider():
        if provider_reservations:
            return
        pairs = admission._required_providers_for_admission(slug, episode_id)
        if pairs is not None and not pairs:
            return  # No phase reaches an LLM, so there is no spend to reserve.
        provider_keys = (sorted({pk for pk, _slot in pairs}) if pairs
                         else [get_effective_provider()])
        acquired: dict[str, str] = {}
        for provider_key in provider_keys:
            grant = db.reserve_provider_spend(
                provider_key, None, run_id=run_id)
            if not grant['allowed']:
                for rid in acquired.values():
                    db.release_provider_spend(rid)
                raise RuntimeError(
                    f"Provider admission denied for {provider_key}: "
                    f"{grant['reason']}")
            if grant['reservation_id'] is not None:
                acquired[provider_key] = grant['reservation_id']
        provider_reservations.update(acquired)

    def _reconcile_provider_actuals():
        for provider_key, rid in provider_reservations.items():
            # Unknown-cost calls make the known-only subtotal an understatement;
            # settle at the reservation estimate (None) rather than release the
            # difference back as if the run spent less than it did.
            if db.run_provider_spend_is_incomplete(run_id, provider_key):
                db.reconcile_provider_spend(rid, None)
            else:
                db.reconcile_provider_spend(
                    rid, db.get_run_provider_spend(run_id, provider_key))

    def _settle_provider_reservations():
        for rid in provider_reservations.values():
            if provider_attempted:
                db.reconcile_provider_spend(rid, None)
            else:
                db.release_provider_spend(rid)

    def _fire_post_completion_actions():
        # Closes over this run's fixed identifiers. Both hooks decline runs
        # that are not theirs, so both success exits call this unguarded.
        # episode_data is the pre-run snapshot, so the degraded guard sees
        # the row as it stood before this run.
        finalize._maybe_enqueue_degraded_redetect(
            slug, episode_id, episode_url, episode_title, podcast_name,
            episode_description, episode_published_at, episode_data, run_stats)
        # After finalize: the yield heuristic reads the durations this run
        # just persisted.
        finalize._maybe_fire_low_ad_yield_action(
            slug, episode_id, episode_url, episode_title, podcast_name,
            episode_description, episode_published_at, episode_data, run_stats,
            podcast_row=podcast_settings)

    try:
        audio_logger.info(f"[{slug}:{episode_id}] Starting: \"{episode_title}\"")
        mem_info = get_available_memory_gb()
        if mem_info is not None:
            mem_val, mem_desc = mem_info
            audio_logger.info(f"[{slug}:{episode_id}] Available memory: {mem_val:.1f} GB ({mem_desc})")
        min_cut_confidence = common.get_min_cut_confidence()
        audio_logger.info(f"[{slug}:{episode_id}] Confidence threshold: {min_cut_confidence:.0%}")

        common._publish_status('start_job', slug, episode_id, episode_title, podcast_name)

        upsert_kwargs = dict(
            original_url=episode_url, title=episode_title,
            description=episode_description, artwork_url=episode_artwork_url,
            status=EpisodeStatus.PROCESSING.value
        )
        if episode_published_at:
            upsert_kwargs['published_at'] = episode_published_at
        db.upsert_episode(slug, episode_id, **upsert_kwargs)

        # A policy rerun keeps the episode served until this point; its
        # ad-data clear happens here rather than when the rerun was queued.
        # 'details' modes clear later, inside the transcribe stage (#692).
        if (reprocess_mode
                and (episode_data or {}).get('reprocess_source') == REPROCESS_SOURCE_POLICY):
            clear_episode_for_mode(db, slug, episode_id, reprocess_mode)

        # Stage 1: Download and transcribe. A retry of a full/reprocess run
        # reuses the transcript an earlier attempt of the same request saved,
        # instead of paying the Whisper run again.
        force_transcription = (
            reprocess_mode in FORCE_TRANSCRIBE_MODES
            and not transcribe._forced_transcription_already_done(
                slug, episode_id,
                (episode_data or {}).get('reprocess_requested_at')))
        # Reset first: last_transcription_stats is a shared-singleton field
        # (see transcriber.Transcriber.__init__), so a stale value from an
        # earlier episode must not be mistaken for this run's outcome when
        # transcription is skipped or an existing transcript is reused.
        transcriber.last_transcription_stats = None
        transcribe_outcome = {}
        try:
            audio_path, segments = transcribe._download_and_transcribe(
                slug, episode_id, episode_url,
                skip_transcription=skip_transcription_active,
                podcast=podcast_settings,
                force_transcription=force_transcription,
                outcome=transcribe_outcome)
        finally:
            # Recorded even when _download_and_transcribe raises (OOM
            # exhaustion): the failure handler's history row still gets the
            # batch/retry/device context, not just a bare error string.
            # One read plus a copy: a concurrent run can replace or mutate
            # the shared singleton field between two reads of it.
            transcription_stats = transcriber.last_transcription_stats
            if transcription_stats is not None:
                run_stats['transcription'] = dict(transcription_stats)
        _check_cancel(cancel_event, slug, episode_id)

        # Stage 1a: upstream transcript differential (best effort, bounded fetch).
        transcript_spans = []
        if not skip_detection:
            transcript_diff = signals._run_transcript_diff(
                slug, episode_id, episode_data, segments, run_stats, podcast=podcast_settings,
                segments_reused=transcribe_outcome.get('segments_reused', False))
            if transcript_diff['status'] == 'ok':
                transcript_spans = transcript_diff['spans']
            _check_cancel(cancel_event, slug, episode_id)

        # Stage 1b: Cross-fetch differential (Layer 3, per-feed opt-in).
        # Started after transcription so the natural delay separates the two
        # fetches, but run on a worker thread so audio analysis (stage 2)
        # proceeds concurrently: the result is only consumed by first-pass
        # detection (stage 3), so the join happens after stage 2. Non-fatal.
        # Its only consumer is ad detection, so a skip-detection feed also
        # skips the second fetch. Thread-safe: the fetch reads audio_path and
        # uses only thread-local db connections plus the lock-guarded
        # status_service; audio analysis shares no mutable state with it.
        dai_differential = None
        # Rebuilt from the differential once detection runs; the skip and
        # cue-only paths reach the late keep partition without it.
        keep_override = keep_rules.KeepDifferentialOverride()
        diff_thread = None
        diff_outcome = {}
        # Both stages want the original's template cues; audio analysis owns
        # the scan and the differential waits for its result.
        primary_cue_future = Future()
        if not skip_detection:
            # Stamp from the main thread BEFORE the worker starts: all status
            # stamps stay on the main thread, so the pass1 ordering 22 -> 25
            # is monotonic and an abandoned worker never stamps another job.
            common._publish_status('update_job_stage', slug, episode_id,
                            "pass1:differential", 22)

            def _diff_worker():
                try:
                    diff_outcome['result'] = signals._run_differential_fetch(
                        slug, episode_id, episode_url, audio_path,
                        podcast_settings.get('id') if podcast_settings else None,
                        dai_platform=(podcast_settings.get('dai_platform')
                                      if podcast_settings else None),
                        podcast=podcast_settings,
                        primary_cue_future=primary_cue_future,
                        download_outcome=transcribe_outcome)
                except BaseException as e:
                    diff_outcome['error'] = e

            # daemon + abandonment is deliberate: if the episode fails before
            # the join, the in-flight fetch finishes in the background (at
            # worst persisting a soon-overwritten error differential) and a
            # daemon thread never blocks interpreter/SIGTERM shutdown the way
            # concurrent.futures' atexit join of non-daemon workers would.
            diff_thread = threading.Thread(
                target=run_context.run_in_worker_thread(_diff_worker), daemon=True,
                name=f"dai-diff-{slug}-{episode_id}")
            diff_thread.start()

        processed_path = None
        audio_analysis_result = None
        try:
            # Everything from the worker start on is covered: a cancel or an
            # analysis crash must still release the waiting differential.
            try:
                _check_cancel(cancel_event, slug, episode_id)
                # Stage 2: Audio analysis (ad-cue detection; nothing to feed
                # when detection is skipped)
                if not skip_detection:
                    with common._measure_run_stage('audio_analysis'):
                        audio_analysis_result = signals._run_audio_analysis(
                            slug, episode_id, audio_path, segments,
                            force_cue_detection=cue_only)
            finally:
                signals._publish_primary_cues(primary_cue_future, audio_analysis_result)
            # Block on the differential fetch before its result is consumed.
            # No timeout: the serial call blocked until fetch_and_diff's own
            # internal timeouts resolved, and the join preserves that. An
            # exception from the fetch (it is documented never to raise, but
            # reproduce the old call-site semantics anyway) re-raises here.
            if diff_thread is not None:
                diff_thread.join()
                if 'error' in diff_outcome:
                    raise diff_outcome['error']
                dai_differential = diff_outcome.get('result')
            if audio_analysis_result is not None and dai_differential is not None:
                # Ride along on the analysis result so the detector prompt and
                # the validator's audio_analysis dict see the differential.
                audio_analysis_result.dai_differential = dai_differential
            # Build the dict the validator receives; carry dai_differential even
            # when Layer 2 (audio analysis) failed so Layer 3 is decoupled from
            # Layer 2 success.
            _val_audio_analysis = signals._make_validator_audio_analysis(
                audio_analysis_result, dai_differential)
            # Count audio-cue signals (issue #350) for the stats dashboard.
            audio_cue_count = (len(audio_analysis_result.get_signals_by_type('audio_cue'))
                               if audio_analysis_result else 0)
            _check_cancel(cancel_event, slug, episode_id)

            # Progress callback for detection stages
            current_pass = "pass1"
            def detection_progress_callback(stage, percent):
                common._publish_status('update_job_stage', slug, episode_id,
                                f"{current_pass}:{stage}", percent)

            # Build the per-episode immutable context once. Podcast tags drive
            # the matcher's community-pattern eligibility check; podcast_id is
            # the integer DB PK used by the reviewer's episode_meta. Re-fetch
            # the row here: download+transcription can take minutes, and tag
            # edits made meanwhile must be visible to pattern eligibility.
            podcast_row_for_ctx = db.get_podcast_by_slug(slug)
            podcast_tags_for_ctx = None
            if podcast_row_for_ctx and podcast_row_for_ctx.get('tags'):
                try:
                    podcast_tags_for_ctx = set(json.loads(podcast_row_for_ctx['tags']))
                except Exception:
                    podcast_tags_for_ctx = None
            ctx = EpisodeContext(
                slug=slug,
                episode_id=episode_id,
                podcast_name=podcast_name,
                episode_title=episode_title,
                podcast_id=(podcast_row_for_ctx.get('id') if podcast_row_for_ctx else None),
                podcast_description=podcast_description,
                detection_notes=detection_notes,
                episode_description=episode_description,
                podcast_tags=podcast_tags_for_ctx,
            )

            # ffprobe duration of the original audio: the single timebase for
            # the prior gate, the prompt hint, and validation normalization.
            episode_duration = audio_processor.get_audio_duration(audio_path)
            if not episode_duration:
                episode_duration = segments[-1]['end'] if segments else 0

            run_stats['downloaded_duration'] = round(episode_duration, 2)
            run_stats['transcript_segments'] = len(segments)

            podcast_id = ctx.podcast_id
            opening_exclusion_seconds = resolve_ad_detection_exclude_start_seconds(
                db, podcast_id)
            # Snapshot for this run: a correction saved mid-run applies on the next run.
            user_corrections = detect._load_user_corrections(slug, episode_id, db)
            if skip_detection:
                # Stages 3-4 skipped: no prior, no detection, no validation.
                # Stage 4 in particular must not run on the empty list because
                # _apply_heuristic_rolls inside it adds pre/post-roll and
                # VAD-gap cuts even then, breaking this mode's nothing-is-cut
                # contract. Detection stats (stage_hits, detected) are not
                # recorded for stages that never ran.
                audio_logger.info(
                    f"[{slug}:{episode_id}] Ad detection skipped (per-feed setting)")
                # Markers and assets left by an earlier detection run describe
                # cut audio; clear them like pass-through does. Transcript
                # inputs survive, so nothing is re-transcribed.
                db.clear_episode_ad_data(slug, episode_id)
                first_pass_count = 0
                max_ad_duration_override, cue_gate_enabled = None, False
                ads_to_remove, all_ads_with_validation = [], []
                keep_ads = []
                segment_actions = {}
            else:
                # Learned positional prior (issue #360 experiment, off by
                # default); consumed by detection and validation only.
                positional_prior = load_positional_prior(db, slug, episode_id,
                                                         episode_duration)

                # Cross-episode text recurrence hint (off until benchmarked;
                # see docs/superpowers/specs/2026-08-25-hushpod-adoption-design.md).
                recurrence_spans = None
                if coerce_bool_setting(db.get_setting('text_recurrence_hints') or 'false'):
                    try:
                        priors = db.get_recent_original_segments(
                            slug, exclude_episode_id=episode_id, limit=5)
                        recurrence_spans = find_recurring_spans(segments, priors)
                        if recurrence_spans:
                            audio_logger.info(
                                f"[{slug}:{episode_id}] Text recurrence: "
                                f"{len(recurrence_spans)} recurring span(s) "
                                f"from {len(priors)} prior episode(s)")
                    except Exception as e:
                        audio_logger.warning(
                            f"[{slug}:{episode_id}] Text recurrence failed, "
                            f"continuing without hint: {e}")

                # One category action map for detection, validation, review and pass 2.
                segment_actions = db.resolve_segment_actions(slug, podcast=podcast_settings)
                recut._log_segment_action_map(slug, episode_id, segment_actions, podcast_settings)

                # Stage 3: First-pass detection
                _reserve_provider()
                provider_attempted = True
                with common._measure_run_stage('detection'):
                    first_pass_ads, first_pass_count, ad_result = detect._detect_ads_first_pass(
                        ctx, segments, audio_path,
                        skip_patterns, audio_analysis_result,
                        detection_progress_callback,
                        cancel_event=cancel_event,
                        positional_prior_hint=format_prior_hint(positional_prior,
                                                                episode_duration),
                        recurrence_spans=recurrence_spans,
                        dai_differential=dai_differential,
                        # None = resolve fresh from the DB at detection time, so a
                        # detection_mode toggle during download/transcription is honored.
                        keep_content=None,
                        skip_llm=cue_only,
                        force_create_from_pairs=cue_only,
                        strict_pair_roles=cue_only,
                        episode_duration=episode_duration,
                        run_stats=run_stats,
                        action_map=segment_actions,
                        transcript_spans=transcript_spans,
                    )
                _check_cancel(cancel_event, slug, episode_id)

                first_pass_ads = detect._exclude_opening_ads(first_pass_ads, opening_exclusion_seconds)
                first_pass_count = len(first_pass_ads)

                cue_templates_for_feed = []
                if cue_only and podcast_id:
                    cue_templates_for_feed = db.list_cue_templates_for_feed_ui(podcast_id)
                    # Produced here, where it is consumed: the suggestion only
                    # ever names a threshold in this notification.
                    detect._notify_quiet_cue_templates(
                        slug, podcast_name, podcast_id, cue_templates_for_feed,
                        threshold_suggestions=detect._suggest_cue_thresholds(
                            slug, episode_id, podcast_id, audio_analysis_result))
                    if cue_only_missing_roles_for_feed(db, podcast_id, cue_templates_for_feed):
                        # Near-unreachable: the API guard blocks the mutation that
                        # would cause this. Belt-and-suspenders log only.
                        audio_logger.error(
                            f"[{slug}:{episode_id}] cue_only feed has no enabled "
                            f"start/end templates; no cuts will be produced")

                _detection_stats = (ad_result or {}).get('detection_stats') or {}
                if 'windows_total' in _detection_stats:
                    run_stats['windows'] = detect._window_stats(
                        _detection_stats['windows_total'],
                        _detection_stats.get('windows_failed', 0),
                        _detection_stats.get('windows_failure_classes'))
                run_stats['stage_hits'] = {
                    'fingerprint': _detection_stats.get('fingerprint_matches', 0),
                    'text_pattern': _detection_stats.get('text_pattern_matches', 0),
                    'differential': _detection_stats.get('dai_differential_matches', 0),
                    'transcript_differential': _detection_stats.get(
                        'transcript_differential_matches', 0),
                    'llm': _detection_stats.get('claude_matches', 0),
                }
                run_stats['detected'] = first_pass_count

                all_ads = first_pass_ads.copy()

                # Keep/mark-action bypass: pull keep-like markers out before
                # the validator/reviewer see them, so the resurrection pool
                # (which iterates all_ads_with_validation) never resurrects
                # one. Merged back into the saved marker list below.
                keep_override = keep_rules._make_keep_differential_override(dai_differential)
                keep_ads, all_ads = keep_rules._partition_keep_ads(
                    all_ads, segment_actions, keep_override)

                # Resolve per-feed hold settings once for the full pipeline.
                max_ad_duration_override = resolve_max_ad_duration_override(db, podcast_id)
                cue_gate_enabled = resolve_cue_gated_approval(db, podcast_id)

                # Cue-only safety: hold_new (default) holds ads whose only
                # backing template lacks enough proven pairs; auto_cut skips this.
                cue_only_safety = None
                cue_unproven_ids = set()
                if cue_only:
                    cue_only_safety = resolve_cue_only_safety(podcast_settings)
                    if cue_only_safety == CUE_ONLY_SAFETY_HOLD_NEW and podcast_id is not None:
                        counts = db.cue_template_paired_episode_counts(podcast_id)
                        enabled = cue_templates_for_feed
                        cue_unproven_ids = {
                            t['id'] for t in enabled if t.get('enabled')
                            and counts.get(t['id'], 0) < CUE_ONLY_PROVEN_EPISODES}

                # Stage 4: Refine and validate
                with common._measure_run_stage('refine_validate'):
                    ads_to_remove, all_ads_with_validation = detect._refine_and_validate(
                        slug, episode_id, all_ads, segments, audio_path,
                        episode_description, episode_duration, min_cut_confidence, podcast_name,
                        skip_patterns=skip_patterns, positional_prior=positional_prior,
                        max_ad_duration_override=max_ad_duration_override,
                        cue_gate_enabled=cue_gate_enabled,
                        audio_analysis=_val_audio_analysis,
                        podcast_id=podcast_id,
                        keep_ads=keep_ads,
                        cue_only_safety=cue_only_safety,
                        cue_unproven_template_ids=cue_unproven_ids,
                        apply_heuristic_rolls=not cue_only,
                        segment_actions=segment_actions,
                        corrections=user_corrections,
                        transcript_spans=transcript_spans,
                        description_trim=((podcast_settings or {}).get('description_trim_pattern'),
                                          (podcast_settings or {}).get('description_trim_mode')),
                    )

                # Late keep partition: _refine_and_validate's heuristic
                # pre/post-roll and VAD-gap markers are synthesized after the
                # early partition above ran, so a keep-resolving one can
                # still be sitting here unstamped. Catch it now, before the
                # reviewer's resurrection pool and the terminal-snap/
                # tail-completion sweeps see it, and join it to keep_ads.
                late_keep_ads, all_ads_with_validation = keep_rules._partition_keep_ads(
                    all_ads_with_validation, segment_actions, keep_override)
                if late_keep_ads:
                    late_keep_ids = {id(ad) for ad in late_keep_ads}
                    ads_to_remove = [ad for ad in ads_to_remove
                                     if id(ad) not in late_keep_ids]
                    keep_ads = keep_ads + late_keep_ads
            _check_cancel(cancel_event, slug, episode_id)

            # cue_only skips this outright: the mode promises zero LLM calls.
            # Otherwise a no-op when enable_ad_review is off (the default).
            ads_to_remove = detect._exclude_opening_ads(ads_to_remove, opening_exclusion_seconds)
            all_ads_with_validation = detect._exclude_opening_ads(all_ads_with_validation, opening_exclusion_seconds)

            # Kept, user-rejected and user-trimmed audio: uncrossable for the reviewer and the render.
            user_trim_ranges = user_trimmed_keep_ranges(user_corrections[1] or [])
            pass1_user_rejects = review._pass1_user_rejects(user_corrections[0], user_trim_ranges)
            pass1_hard = [*keep_ads, *pass1_user_rejects]
            if not cue_only:
                with common._measure_run_stage('refine_validate'):
                    ads_to_remove, all_ads_with_validation = review._run_ad_reviewer(
                        slug, episode_id, podcast_id, ads_to_remove,
                        all_ads_with_validation, segments, podcast_name,
                        episode_title, episode_description, podcast_description,
                        min_cut_confidence, pass_num=1,
                        pass_model=ad_detector.get_model(),
                        pass_provider=ad_detector.get_provider(),
                        audio_analysis=audio_analysis_result,
                        cue_gate_enabled=cue_gate_enabled,
                        segment_actions=segment_actions,
                        keep_ads=keep_ads,
                        user_rejects=pass1_user_rejects,
                        detection_notes=detection_notes,
                    )
            _check_cancel(cancel_event, slug, episode_id)
            # Standing reviewer rejects bar the render and restore, as on recut, except where the user confirmed.
            pass1_reviewer_rejects = reject_barriers(all_ads_with_validation, user_corrections[1])

            # Fold keep-action markers back into the saved marker list now
            # that the validator and reviewer are done with pass 1; they were
            # withheld above so neither stage could hold, cut, or resurrect them.
            if keep_ads:
                all_ads_with_validation = list(all_ads_with_validation) + keep_ads
                all_ads_with_validation.sort(key=lambda x: x['start'])
                storage.save_combined_ads(slug, episode_id, all_ads_with_validation)
                # Kept markers bypass validation and cut-ad learning.
                keep_rules._learn_from_kept_ads(slug, episode_id, keep_ads, segments, audio_path)

            # Terminal start snap to the splice point; reviewer-locked starts stay put.
            ads_to_remove = cuts._snap_terminal_starts(
                slug, episode_id, ads_to_remove, all_ads_with_validation,
                segments, audio_analysis_result, episode_duration,
                podcast_name=podcast_name
            )

            # Tail completion: content-based end sweep that never extends a reviewer-locked end.
            ads_to_remove = cuts._complete_cut_tails(
                slug, episode_id, ads_to_remove, all_ads_with_validation,
                segments, podcast_name=podcast_name
            )

            # A transcript-guided tail extension can stop before an
            # untranscribed sonic logo. Strong forward splice evidence closes
            # that final gap without crossing speech or another marker.
            ads_to_remove = cuts._snap_completed_cut_tails_to_splice(
                slug, episode_id, ads_to_remove, all_ads_with_validation,
                segments, audio_analysis_result, podcast_name=podcast_name
            )

            # Human-approved trim bounds are the final boundary authority.
            # Run after every automated reviewer and tail mutation so neither
            # the audio cut nor its persisted marker can drift from approval.
            ads_to_remove = cuts._finalize_user_confirmed_bounds(
                slug, episode_id, ads_to_remove, all_ads_with_validation,
                episode_duration=episode_duration)
            ads_to_remove, trim_ranges = keep_rules._restore_confirmed_spans(
                ads_to_remove, all_ads_with_validation, user_corrections,
                episode_duration, opening_exclusion_seconds,
                restore=not skip_detection, trim_ranges=user_trim_ranges)

            # Backstop: the late keep partition above should already have
            # caught everything, so this normally finds nothing.
            ads_to_remove = keep_rules._apply_late_keep_safety_net(
                ads_to_remove, all_ads_with_validation, segment_actions,
                keep_override)

            ads_to_remove = keep_rules._stamp_and_carve_cuts(
                slug, episode_id, ads_to_remove, all_ads_with_validation,
                segment_actions, keep_ads)

            # Stage 5: Process audio
            common._publish_status('update_job_stage', slug, episode_id,
                            "pass1:processing", 80)
            audio_logger.info(f"[{slug}:{episode_id}] Starting FFMPEG processing ({len(ads_to_remove)} ads to remove)")

            bitrate, compression_level = common.resolve_audio_encode_settings()
            audio_output = db.resolve_audio_output(slug, podcast=podcast_settings)
            if (db.get_setting('audio_normalize_enabled') or 'false').lower() == 'true':
                audio_output['mp3_stream_copy_enabled'] = False
            local_audio_processor = AudioProcessor(
                bitrate=bitrate, compression_level=compression_level, **audio_output)

            # process_episode returns the cuts ffmpeg actually applied (merged,
            # <10s-filtered, end-trimmed); verification mapping and assets must
            # use that list, not the requested one. 'beep' is derived here,
            # not stored, so persisted marker dicts never carry that flag.
            audio_segments = [dict(ad, beep=(ad['action_applied'] == 'beep'))
                              for ad in ads_to_remove]
            # keep_ads barrier the render for the same reason as on the
            # pass-2 and manual recut paths.
            with common._measure_run_stage('cut'):
                result = local_audio_processor.process_episode(
                    audio_path, audio_segments,
                    **verify._render_barriers([*pass1_hard, *pass1_reviewer_rejects],
                                       all_ads_with_validation, ads_to_remove))
            if not result:
                raise Exception(
                    f"FFMPEG processing failed for {len(ads_to_remove)} ad segments "
                    f"({episode_duration / 60:.1f}min episode) - see audio processor logs above"
                )
            processed_path, applied_cuts = result

            # Pass 2 routes on this state; it is persisted once, after the assets.
            cuts._finalize_cut_state(all_ads_with_validation, ads_to_remove, applied_cuts,
                                episode_duration, tag=f"[{slug}:{episode_id}]")

            original_duration = episode_duration
            _check_cancel(cancel_event, slug, episode_id)

            # Stage 6: Verification pass
            current_pass = "pass2"
            # Pass-1 held markers (original coords): pass 2 must not cut
            # inside them; a corroborating re-detection of a differential
            # hold stamps the marker dict in place for auto-approval.
            pass1_held_markers = [
                m for m in all_ads_with_validation if is_pending_review(m)
            ]
            # Kept markers (original coords): a verification finding
            # overlapping one must never be cut, held, or logged as a miss.
            pass1_kept_markers = [
                m for m in all_ads_with_validation
                if is_keep_like(m.get('action_applied'))
            ]
            verification_skipped = skip_detection or skip_second_pass or cue_only
            if skip_second_pass and not skip_detection:
                audio_logger.info(
                    f"[{slug}:{episode_id}] Verification pass skipped (configured setting)")
            with (common._measure_run_stage('verification')
                  if not verification_skipped else nullcontext()):
                verification_count, v_ads_for_ui, v_cuts_for_assets, v_ads_held, processed_path, verification_cue_count, verification_ok, v_corroborated_count = verify._run_verification_pass(
                    ctx, processed_path, applied_cuts,
                    skip_patterns, min_cut_confidence,
                    local_audio_processor, detection_progress_callback,
                    original_segments=segments,
                    # LLM-only reprocess maps the saved transcript through the cuts
                    # for pass 2 instead of re-transcribing (issue #349).
                    reuse_transcript=(reprocess_mode == 'llm'),
                    max_ad_duration_override=max_ad_duration_override,
                    cue_gate_enabled=cue_gate_enabled,
                    pass1_held_markers=pass1_held_markers,
                    pass1_kept_markers=pass1_kept_markers,
                    pass1_trim_ranges=trim_ranges,
                    skip_verification=verification_skipped,
                    segment_actions=segment_actions,
                    differential_override=keep_override,
                    run_stats=run_stats,
                    original_audio_path=audio_path,
                    pass1_markers=ads_to_remove,
                    false_positive_corrections=user_corrections[0],
                    pass1_reviewer_rejects=pass1_reviewer_rejects,
                )
            # Detection-event accounting, not unique cues (issue #350): a cue
            # in a region pass 1 left in the audio is re-detected here and
            # intentionally counts twice.
            audio_cue_count += verification_cue_count
            _check_cancel(cancel_event, slug, episode_id)

            # Stage 6b: Optional loudness normalization (second ffmpeg pass).
            # Applied only to the FINAL settled output so the cut path
            # (filter_complex) and the ad-detection audio analyzers see
            # uncompressed dynamics.
            normalize_raw = db.get_setting('audio_normalize_enabled')
            if (normalize_raw or 'false').lower() == 'true':
                run_stats['normalization_skipped'] = False
                intensity = db.get_setting('audio_normalize_intensity') or 'normal'
                with common._measure_run_stage('normalization'):
                    normalized_path = local_audio_processor.normalize_audio(
                        processed_path, intensity=intensity,
                    )
                if normalized_path:
                    if os.path.exists(processed_path):
                        try:
                            os.unlink(processed_path)
                        except OSError as e:
                            audio_logger.warning(
                                f"[{slug}:{episode_id}] Failed to remove pre-normalize file: {e}"
                            )
                    processed_path = normalized_path
                    audio_logger.info(
                        f"[{slug}:{episode_id}] Applied audio normalization ({intensity})"
                    )
                else:
                    audio_logger.warning(
                        f"[{slug}:{episode_id}] Normalize pass failed, keeping un-normalized output"
                    )
            else:
                run_stats['normalization_skipped'] = True
            _check_cancel(cancel_event, slug, episode_id)

            # Merge pass 2 ads into combined list for UI.
            # v_ads_held (held-for-review and category-kept originals) merge
            # here too so every uncut pass-2 marker survives persistence. They
            # stay separate from v_ads_for_ui so the reviewer pool and asset
            # mapping are never contaminated with uncut ads.
            merge_v = verify._dedupe_pass2_markers(
                verify._stamp_pass2_marker_categories(v_ads_for_ui + v_ads_held))
            # A pass-2 marker repeating a span pass 1 already persisted folds
            # into that marker; appending it would store one span twice.
            merge_v, _ = verify._merge_pass2_markers(
                all_ads_with_validation, merge_v)
            if merge_v:
                all_ads_with_validation = list(all_ads_with_validation) + merge_v
                all_ads_with_validation.sort(key=lambda x: x['start'])
            # Stage 7 renders assets from these cuts, so marker state and counts use them too.
            all_cuts_for_assets = applied_cuts + v_cuts_for_assets
            cut_groups = cuts._finalize_cut_state(
                all_ads_with_validation, [*ads_to_remove, *v_ads_for_ui],
                all_cuts_for_assets, original_duration, tag=f"[{slug}:{episode_id}]")
            # A rendered cut holding a pass-2 marker is a verification cut.
            verification_count = cuts._rendered_cuts_covering(
                cut_groups, v_ads_for_ui, original_duration)
            pass1_cut_count = len(cut_groups) - verification_count

            new_duration = local_audio_processor.get_audio_duration(processed_path)

            existing_episode = db.get_episode(slug, episode_id) or {}
            new_version = transcribe._next_processed_version(existing_episode)

            # Fence first: resolving the path mkdirs the podcast tree, which
            # would recreate the directory of a feed deleted mid-run.
            common._require_publication_owner(slug, episode_id)
            keep_rules._learn_from_applied_cut_ads(
                slug, episode_id, ads_to_remove, all_ads_with_validation,
                applied_cuts, episode_duration, segments, audio_path)
            final_path = storage.get_episode_path(slug, episode_id, version=new_version)
            shutil.move(processed_path, final_path)

            # Stage 7: Generate assets. Uses the RENDERED cut lists (one
            # replacement beep per span), not the UI ad list: gap-merged
            # pass-2 ads share a single beep in the audio.
            if skip_detection:
                _reserve_provider()
                provider_attempted = True
            with common._measure_run_stage('assets'):
                publish._generate_assets(slug, episode_id, segments, all_cuts_for_assets,
                                  episode_description, podcast_name, episode_title,
                                  audio_path=final_path, audio_duration=new_duration,
                                  podcast_row=podcast_settings,
                                  original_duration=original_duration,
                                  run_stats=run_stats,
                                  markers=all_ads_with_validation)
            # Retain the source only after chapter assets validate.
            keep_original = db.resolve_keep_original_audio(
                slug, podcast_settings)
            if keep_original and os.path.exists(audio_path):
                original_final = storage.get_original_path(slug, episode_id)
                original_final.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(audio_path, original_final)
                audio_logger.info(
                    f"[{slug}:{episode_id}] Retained original audio at {original_final.name}"
                )

            storage.save_combined_ads(slug, episode_id, all_ads_with_validation)

            # Stage 8: Finalize. ads_removed counts rendered cuts, so markers
            # merged into one span count once and a filtered-out one not at all.
            held_count = sum(1 for m in all_ads_with_validation if is_pending_review(m))
            cut_count = sum(1 for m in all_ads_with_validation if m.get('was_cut'))
            not_cut_count = count_not_cut(all_ads_with_validation)
            run_stats['markers'] = {
                'cut': cut_count,
                'held': held_count,
                'not_cut': len(all_ads_with_validation) - cut_count - held_count,
            }
            # Recorded only when the pass actually completed: a crashed or
            # skipped scan must not read as a clean one (0 would be
            # indistinguishable).
            if verification_ok:
                run_stats['verification_ads_cut'] = verification_count
            cuts._record_cut_seconds(run_stats, all_cuts_for_assets, original_duration,
                                new_duration)
            run = run_context.current()
            usage = run.failover_usage() if run else None
            if usage:
                run_stats['failover'] = usage
            # File the confirms before finalizing so the recut below applies
            # them in this run: two finalizes wrote two history rows and
            # notified twice for one reprocess.
            # A matched chapter's trim marker rides the same single in-run recut.
            approvals_filed = verify._file_corroborated_hold_approvals(
                slug, episode_id, all_ads_with_validation,
                corrections=user_corrections)
            if approvals_filed or publish.pending_chapter_trim(all_ads_with_validation):
                # No cancel_event: a cancel here reaches the background
                # wrapper's cleanup, which deletes the finished files.
                recut_progress = {}
                if recut._recut_episode(slug, episode_id, episode_title,
                                   podcast_name, episode_description,
                                   start_time, cancel_event=None,
                                   run_stats=run_stats,
                                   verification_count=verification_count,
                                   audio_cue_detections=audio_cue_count,
                                   owns_failure=False,
                                   progress=recut_progress,
                                   podcast_row=podcast_settings):
                    markers_published = True
                    _reconcile_provider_actuals()
                    _fire_post_completion_actions()
                    return True
                if recut_progress.get('mutated'):
                    # The recut already replaced the markers and the audio, so
                    # this run's render is gone and only it can own the outcome.
                    if not recut_progress.get('published'):
                        recut._restore_saved_markers(slug, episode_id, episode_data)
                    failure._handle_processing_failure(
                        slug, episode_id, episode_title, podcast_name,
                        db.get_episode(slug, episode_id),
                        RuntimeError('In-run recut failed after rewriting the '
                                     'episode'), start_time, run_stats=run_stats)
                    _settle_provider_reservations()
                    return False
                # Nothing was overwritten: finalize this run's render and leave
                # the filed confirms for the next run to apply.
                audio_logger.warning(
                    f"[{slug}:{episode_id}] In-run recut failed before it "
                    f"rewrote anything; finalizing this run's render"
                )

            finalize._finalize_episode(slug, episode_id, episode_title, podcast_name,
                               pass1_cut_count, verification_count, first_pass_count,
                               original_duration, new_duration, start_time,
                               processed_version=new_version,
                               audio_cue_detections=audio_cue_count,
                               run_stats=run_stats,
                               ads_held=held_count, ads_not_cut=not_cut_count,
                               on_persisted=_mark_markers_published)

            _fire_post_completion_actions()

            common._publish_status('complete_job', slug, episode_id)
            _reconcile_provider_actuals()
            return True

        finally:
            # The differential worker is a daemon thread and is deliberately
            # abandoned on failure paths (see its start site); nothing to
            # shut down here.
            if os.path.exists(audio_path):
                os.unlink(audio_path)
            # Still present means the render was never moved to final_path.
            if processed_path and os.path.exists(processed_path):
                try:
                    os.unlink(processed_path)
                except OSError as e:
                    audio_logger.warning(
                        f"[{slug}:{episode_id}] Failed to remove the unpublished render: {e}")

    except ProcessingCancelled:
        if not markers_published:
            recut._restore_saved_markers(slug, episode_id, episode_data)
        _settle_provider_reservations()
        raise
    except Exception as e:
        # Markers saved mid-run must not sit next to the audio still published.
        if not markers_published:
            recut._restore_saved_markers(slug, episode_id, episode_data)
        _settle_provider_reservations()
        failure._handle_processing_failure(slug, episode_id, episode_title, podcast_name,
                                    episode_data, e, start_time,
                                    run_stats=run_stats)
        return False
