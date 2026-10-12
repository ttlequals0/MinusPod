"""Episode state persistence, run log, history rows and completion events."""

import logging
import time
from ad_yield import low_ad_yield
from utils.time import (
    epoch_to_iso, utc_now_iso,
)

from config import (
    resolve_low_ad_yield_action,
    resolve_episode_log_level,
    resolve_episode_log_storage,
    LOW_AD_YIELD_ACTION_MODES,
)

from llm_client import get_episode_token_totals

import run_context
import run_log
from reprocess_modes import REPROCESS_MODE_NEEDS_TRANSCRIPT

from utils.constants import (
    EpisodeStatus, PIPELINE_REPROCESS_SOURCES,
    REPROCESS_SOURCE_DEGRADED, REPROCESS_SOURCE_POLICY,
)

from utils.episode_paths import episode_relative_path
from utils.gpu import get_available_memory_gb, clear_gpu_memory
from webhook_service import (
    fire_event, EVENT_EPISODE_PROCESSED,
)

from main_app.pipeline import common
from main_app.pipeline.shared import (db, storage)

audio_logger = logging.getLogger('podcast.audio')


def _persist_episode_state(slug, episode_id, pass1_cut_count, verification_count,
                            first_pass_count, original_duration, new_duration,
                            processed_version, detection_degraded=None,
                            run_started_iso=None):
    """Upsert the processed episode row and update related DB state.

    ``detection_degraded``: the sanitized reason string when this run
    degraded, else None to clear a flag left by an earlier failure.
    ``run_started_iso``: when the run began; only pending-recut stamps from
    before then are cleared, so a decision recorded mid-run survives.
    """
    common._require_publication_owner(slug, episode_id)
    original_final = storage.get_original_path(slug, episode_id)
    original_file_rel = f"episodes/{episode_id}-original.mp3" if original_final.exists() else None
    processed_file_rel = episode_relative_path(episode_id, processed_version)
    try:
        processed_size_bytes = storage.get_episode_path(
            slug, episode_id, version=processed_version).stat().st_size
    except OSError:
        processed_size_bytes = None
    db.upsert_episode(slug, episode_id,
        status=EpisodeStatus.PROCESSED.value,
        processed_at=utc_now_iso(),
        processed_file=processed_file_rel,
        processed_size_bytes=processed_size_bytes,
        processed_version=processed_version or 0,
        original_file=original_file_rel,
        original_duration=original_duration,
        new_duration=new_duration,
        ads_removed=pass1_cut_count + verification_count,
        ads_removed_firstpass=first_pass_count,
        ads_removed_secondpass=verification_count,
        # A successful finalize clears any message left by an earlier failure
        # or by the stuck-row sweep, including a failed chapter regeneration
        # whose chapters this run has just replaced.
        error_message=None,
        retry_count=0,
        chapters_regen_error=None,
        reprocess_mode=None,
        reprocess_requested_at=None,
        deferred_at=None,
        deferred_service=None,
        # A clean run clears a degraded flag from an earlier failure; a
        # degraded run re-stamps its own reason so this unconditional
        # write does not clobber the flag detection just set.
        detection_degraded=detection_degraded)

    # A completed run cuts from the markers it loaded, so only decisions
    # waiting when it started are now in the audio; a stamp from mid-run
    # stays for the next apply.
    db.clear_episode_pending_recut(slug, episode_id, before=run_started_iso)

    try:
        removed = storage.cleanup_stale_audio_versions(
            slug, episode_id, processed_version or 0
        )
        if removed:
            audio_logger.info(
                f"[{slug}:{episode_id}] Cleaned up {removed} stale audio version(s)"
            )
    except Exception as cleanup_err:
        audio_logger.warning(
            f"[{slug}:{episode_id}] Failed to clean stale audio versions: {cleanup_err}"
        )

    try:
        closed = db.close_queue_rows_for_episode(slug, episode_id)
        if closed:
            audio_logger.info(
                f"[{slug}:{episode_id}] Closed {closed} auto-process queue row(s) after successful finalize"
            )
    except Exception as q_err:
        audio_logger.warning(f"[{slug}:{episode_id}] Failed to close auto-process queue rows: {q_err}")

    try:
        db.index_episode(episode_id, slug)
    except Exception as idx_err:
        audio_logger.warning(f"[{slug}:{episode_id}] Failed to update search index: {idx_err}")


def _maybe_enqueue_degraded_redetect(slug, episode_id, episode_url, episode_title,
                                      podcast_name, episode_description,
                                      episode_published_at, episode_data, run_stats):
    """Queue one low-priority llm-mode re-detect after a degraded publish.

    Fires only on the transition into degraded (episode_data is the row as
    it stood before this run): a run that was already degraded, or one that
    degrades again on the automatic re-detect itself, does not re-enqueue.
    """
    if not (run_stats or {}).get('detection_degraded'):
        return
    if (episode_data or {}).get('detection_degraded'):
        return
    db.upsert_episode(slug, episode_id, reprocess_mode='llm',
                       reprocess_requested_at=utc_now_iso(),
                       reprocess_source=REPROCESS_SOURCE_DEGRADED)
    db.upsert_episode_for_processing(
        slug, episode_id, episode_url, episode_title,
        episode_published_at, episode_description, priority=-10)
    audio_logger.info(
        f"[{slug}:{episode_id}] Degraded pass-1 detection; queued one "
        f"low-priority automatic llm re-detect")


def _maybe_fire_low_ad_yield_action(slug, episode_id, episode_url, episode_title,
                                     podcast_name, episode_description,
                                     episode_published_at, episode_data, run_stats,
                                     podcast_row=None):
    """Queue one automatic rerun when a run removed far less ad time than the
    feed usually yields.

    Fires once per episode ever (the low_yield_rerun_at stamp) and only for
    runs nobody asked for by hand: scheduled auto-process and play requests
    qualify, manual reprocesses and this policy's own reruns do not. Never
    raises into the pipeline.

    podcast_row, when given, is the already-fetched podcasts row the pipeline
    holds; None falls back to fetching it here.
    """
    try:
        row = episode_data or {}
        # The stamp alone only clears the auto-process gate; the source says
        # whether the pipeline or a person asked for this run.
        if (row.get('reprocess_requested_at')
                and row.get('reprocess_source') not in PIPELINE_REPROCESS_SOURCES):
            return
        # A degraded run queues its own re-detect, and its yield says nothing
        # about detection quality.
        if (run_stats or {}).get('detection_degraded'):
            return
        # A cue-only feed reruns the same cue pipeline whatever mode is asked
        # for, so a rerun can only spend the one shot.
        if (run_stats or {}).get('cue_only'):
            return
        podcast = podcast_row if podcast_row is not None else db.get_podcast_by_slug(slug)
        action = resolve_low_ad_yield_action(db, podcast)
        if action not in LOW_AD_YIELD_ACTION_MODES:
            return
        episode = db.get_episode(slug, episode_id)
        if not episode:
            return
        if episode.get('low_yield_rerun_at'):
            audio_logger.info(
                f"[{slug}:{episode_id}] low_ad_yield_action suppressed: "
                f"already rerun at {episode['low_yield_rerun_at']}")
            return
        yield_info = low_ad_yield(db, episode,
                                  [{'status': 'completed', 'stats': run_stats}])
        if not yield_info:
            return

        mode = LOW_AD_YIELD_ACTION_MODES[action]
        if REPROCESS_MODE_NEEDS_TRANSCRIPT[mode] and not db.has_transcript(slug, episode_id):
            audio_logger.info(
                f"[{slug}:{episode_id}] low_ad_yield_action redetect needs a "
                f"stored transcript; falling back to reprocess")
            mode = 'reprocess'

        # Stamped before anything is queued so a crash cannot fire twice.
        db.upsert_episode(slug, episode_id, low_yield_rerun_at=utc_now_iso())
        # Status stays 'processed' so the episode keeps serving until the rerun
        # starts, which is also when the per-mode clear runs. The policy source
        # stops this rerun from triggering the policy again.
        db.upsert_episode(slug, episode_id, reprocess_mode=mode,
                          reprocess_requested_at=utc_now_iso(),
                          reprocess_source=REPROCESS_SOURCE_POLICY)

        # Queued rather than started: this run still holds the processing lock,
        # so the queue processor picks it up after the release. Low priority,
        # like the degraded re-detect: fresh episodes come first.
        db.upsert_episode_for_processing(
            slug, episode_id, episode_url, episode_title,
            episode_published_at, episode_description, priority=-10)
        common._publish_status('queue_episode', slug, episode_id, episode_title, podcast_name)
        audio_logger.info(
            f"low_ad_yield_action fired action={mode} slug={slug} "
            f"episode_id={episode_id} removed={yield_info['removedSeconds']:.1f}s "
            f"feed_avg={yield_info['feedAverageSeconds']:.1f}s")
    except Exception as err:
        audio_logger.warning(
            f"[{slug}:{episode_id}] low_ad_yield_action hook failed: {err}")


def _log_completion_summary(slug, episode_id, pass1_cut_count, *, verification_count,
                             original_duration, new_duration, processing_time, db):
    """Log completion summary and post-cleanup memory; return token totals.

    Total cuts reported as pass-1 applied cuts + verification re-cut.
    Matches what ``_persist_episode_state`` stores in episodes.ads_removed
    and what ``_record_history_and_event`` writes to history.ads_detected.

    ``verification_count`` is keyword-only so a future positional caller
    using the older 7-arg signature cannot silently bind a float duration
    into this slot.
    """
    total_cuts = pass1_cut_count + verification_count
    if original_duration and new_duration:
        time_saved = original_duration - new_duration
        # Unconditional so a recut that removes the saving corrects the
        # counter downward, not just credits further gains.
        db.credit_time_saved(slug, episode_id, max(time_saved, 0.0))
        audio_logger.info(
            f"[{slug}:{episode_id}] Complete: {original_duration/60:.1f}->{new_duration/60:.1f}min, "
            f"{total_cuts} ads removed, {processing_time:.1f}s"
        )
    else:
        audio_logger.info(f"[{slug}:{episode_id}] Complete: {total_cuts} ads removed, {processing_time:.1f}s")

    token_totals = get_episode_token_totals()
    audio_logger.info(f"[{slug}:{episode_id}] Token totals: in={token_totals['input_tokens']} out={token_totals['output_tokens']} cost=${token_totals['cost']:.6f}")

    # Periodic memory cleanup to prevent fragmentation over many processing cycles
    clear_gpu_memory()
    mem_info = get_available_memory_gb()
    if mem_info is not None and mem_info[0] is not None:
        mem_val, mem_desc = mem_info
        audio_logger.info(f"[{slug}:{episode_id}] Post-cleanup memory: {mem_val:.1f} GB ({mem_desc})")

    return token_totals


def _start_run_log(slug, episode_id):
    """Attach a run-log recorder when this feed stores logs (#660).

    Returns the recorder or None; setup problems disable capture for the run
    rather than touching the pipeline.
    """
    try:
        if not resolve_episode_log_storage(db, db.get_podcast_by_slug(slug)):
            return None
        recorder = run_log.RunLogRecorder(
            slug, episode_id, resolve_episode_log_level(db),
            run_log.run_log_temp_dir(storage.data_dir))
        recorder.attach()
        return recorder
    except Exception as err:
        audio_logger.warning(f"[{slug}:{episode_id}] run log setup failed: {err}")
        return None


def _end_run_log(recorder):
    """Detach the recorder; a run that never wrote a history row drops its file."""
    if recorder is None:
        return
    try:
        recorder.detach()
        recorder.discard()
    except Exception as err:
        audio_logger.warning(f"run log teardown failed: {err}")


def _finalize_run_log(db, history_id, slug, episode_id):
    """Move this run's log onto its freshly written history row."""
    recorder = run_log.current_recorder()
    if recorder is None or not history_id:
        return
    if recorder.tag != f"[{slug}:{episode_id}]":
        # The slot holds another run's recorder; finalizing would file its log
        # under this row.
        audio_logger.warning(
            f"[{slug}:{episode_id}] run log slot holds {recorder.tag}; not finalizing")
        return
    try:
        if recorder.finalize(
                run_log.run_log_path(storage.data_dir, slug, episode_id, history_id)):
            db.set_history_log_pointer(
                history_id,
                run_log.run_log_relative_path(slug, episode_id, history_id))
    except Exception as err:
        audio_logger.warning(f"[{slug}:{episode_id}] run log finalize failed: {err}")


def _record_history_row(db, slug, episode_id, episode_title, podcast_name, status,
                        processing_time, ads_detected, token_totals,
                        error_message=None, audio_cues_detected=0,
                        run_stats=None):
    """Write one processing-history row for this episode. Returns False when
    the podcast row is missing (nothing written); raises on DB write failure."""
    podcast_data = db.get_podcast_by_slug(slug)
    if not podcast_data:
        return False
    stats = dict(run_stats or {})
    ctx = run_context.current()
    usage = ctx.failover_usage() if ctx is not None and ctx.slug == slug and ctx.episode_id == str(episode_id) else None
    if usage:
        stats['failover'] = usage
    run_totals = token_totals
    run_id_for_history = None
    if (ctx is not None and ctx.slug == slug
            and ctx.episode_id == str(episode_id) and ctx.run_id):
        notices = ctx.thinking_notices(ctx.run_id)
        if notices:
            stats['thinking_notices'] = notices
        timings = ctx.timing.snapshot()
        if timings:
            stats['timings'] = timings
        # Persisted totals come from the ledger, not the in-process
        # accumulator: a pool worker that finishes after collection would
        # otherwise silently drop its tokens/cost from this run's row.
        ledger_totals = db.get_run_usage_totals(ctx.run_id)
        run_totals = {
            'input_tokens': ledger_totals['input_tokens'],
            'output_tokens': ledger_totals['output_tokens'],
            'cost': float(ledger_totals['cost_usd']),
        }
        run_id_for_history = ctx.run_id
    history_id = db.record_processing_history(
        podcast_id=podcast_data['id'], podcast_slug=slug,
        podcast_title=podcast_data.get('title') or podcast_name,
        episode_id=episode_id, episode_title=episode_title,
        status=status, processing_duration_seconds=processing_time,
        ads_detected=ads_detected, error_message=error_message,
        input_tokens=run_totals['input_tokens'],
        output_tokens=run_totals['output_tokens'],
        llm_cost=run_totals['cost'],
        audio_cues_detected=audio_cues_detected,
        processing_stats=stats or None,
        run_id=run_id_for_history,
    )
    _finalize_run_log(db, history_id, slug, episode_id)
    return True


def _record_history_and_event(slug, episode_id, episode_title, podcast_name,
                               pass1_cut_count, verification_count,
                               original_duration, new_duration,
                               processing_time, token_totals, db,
                               audio_cue_detections=0, run_stats=None,
                               ads_held=0, ads_not_cut=0):
    """Record processing history row and fire the episode-processed webhook.

    The webhook fires whenever the episode pipeline completed, including
    the case where the podcast row is missing (we still have slug +
    episode_id + counts for the payload). The webhook is skipped only
    when `record_processing_history` raised, which signals a real DB
    write failure that would leave the History page out of sync.
    """
    common._require_publication_owner(slug, episode_id)
    ads_removed_total = pass1_cut_count + verification_count
    history_write_raised = False
    try:
        if not _record_history_row(
                db, slug, episode_id, episode_title, podcast_name,
                status='completed', processing_time=processing_time,
                ads_detected=ads_removed_total, token_totals=token_totals,
                audio_cues_detected=audio_cue_detections, run_stats=run_stats):
            audio_logger.warning(
                f"[{slug}:{episode_id}] Skipping history record: podcast row not found"
            )
    except Exception as hist_err:
        history_write_raised = True
        audio_logger.warning(f"[{slug}:{episode_id}] Failed to record history: {hist_err}")

    if history_write_raised:
        audio_logger.warning(
            f"[{slug}:{episode_id}] Skipping EVENT_EPISODE_PROCESSED: history INSERT raised"
        )
        return

    try:
        fire_event(
            event=EVENT_EPISODE_PROCESSED,
            episode_id=episode_id, slug=slug, episode_title=episode_title,
            processing_time=processing_time, llm_cost=token_totals['cost'],
            ads_removed=ads_removed_total,
            ads_held=ads_held, ads_not_cut=ads_not_cut,
            original_duration=original_duration, new_duration=new_duration,
            podcast_name=podcast_name,
        )
    except Exception as wh_err:
        audio_logger.warning(f"[{slug}:{episode_id}] Webhook fire failed: {wh_err}")


def _finalize_episode(slug, episode_id, episode_title, podcast_name,
                       pass1_cut_count, verification_count, first_pass_count,
                       original_duration, new_duration, start_time,
                       processed_version=0, audio_cue_detections=0,
                       run_stats=None, ads_held=0, ads_not_cut=0, on_persisted=None):
    """Pipeline stage: Update DB, record history, refresh RSS."""
    # Stop this timer before history snapshots it. The history write and event
    # happen afterward, so finalizeSeconds has one stable, documented bound.
    with common._measure_run_stage('finalize'):
        _persist_episode_state(slug, episode_id, pass1_cut_count, verification_count,
                                first_pass_count, original_duration, new_duration,
                                processed_version,
                                detection_degraded=(run_stats or {}).get('detection_degraded'),
                                run_started_iso=epoch_to_iso(start_time))
        # The row now points at the new audio, so a later failure must not roll markers back.
        if on_persisted is not None:
            on_persisted()
        common._refresh_rss_for_slug(slug, episode_id)

        processing_time = time.time() - start_time

        token_totals = _log_completion_summary(
            slug, episode_id, pass1_cut_count,
            verification_count=verification_count,
            original_duration=original_duration,
            new_duration=new_duration,
            processing_time=processing_time,
            db=db,
        )

    _record_history_and_event(
        slug, episode_id, episode_title, podcast_name,
        pass1_cut_count, verification_count,
        original_duration, new_duration,
        processing_time, token_totals, db,
        audio_cue_detections=audio_cue_detections,
        run_stats=run_stats,
        ads_held=ads_held, ads_not_cut=ads_not_cut,
    )
