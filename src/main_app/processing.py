"""Processing pipeline: _process_episode_background, all pipeline stages."""
import json
import logging
import os
import shutil
import tempfile
import threading
import time

import requests
import requests.exceptions

from ad_detector import (
    refine_ad_boundaries, snap_early_ads_to_zero, merge_same_sponsor_ads,
    extend_ad_boundaries_by_content,
)
from ad_reviewer import (
    AdReviewer, ReviewVerdict, split_resurrection_pool,
)
from cancel import ProcessingCancelled, _check_cancel, _cancel_events, _cancel_events_lock
from config import MIN_CUT_CONFIDENCE, MAX_EPISODE_RETRIES
from llm_capabilities import (
    PASS_AD_DETECTION_1, PASS_AD_DETECTION_2,
    PASS_CHAPTER_GENERATION, PASS_REVIEWER_1, PASS_REVIEWER_2,
    clear_fallback,
)
from llm_client import is_retryable_error, is_llm_api_error, is_rate_limit_error, start_episode_token_tracking, get_episode_token_totals
from positional_prior import format_prior_hint, load_positional_prior
from utils.constants import EpisodeStatus
from utils.episode_paths import episode_relative_path
from utils.gpu import get_available_memory_gb, clear_gpu_memory
from utils.language import get_feed_language_override
from utils.text import parse_transcript_segments
from utils.time import parse_timestamp
from webhook_service import fire_event, EVENT_EPISODE_PROCESSED, EVENT_EPISODE_FAILED

audio_logger = logging.getLogger('podcast.audio')

# Import shared warn-dedup set so routes and processing share one instance
from main_app.shared_state import permanently_failed_warned as _permanently_failed_warned
from main_app.episode_context import EpisodeContext
# Singletons created in main_app/__init__.py before this submodule is
# loaded by the explicit `from main_app.processing import ...` near the
# bottom of that module, so the apparent circular import is safe.
# Replaces a positional 10-tuple from _get_components() that the audit
# flagged as silently break-on-reorder.
from main_app import (db, storage, transcriber, ad_detector, audio_processor,
                      audio_analyzer, sponsor_service, status_service, pattern_service,
                      processing_queue)


def get_min_cut_confidence() -> float:
    """Get the minimum confidence threshold for cutting ads from audio.

    This is configurable via the 'min_cut_confidence' setting (aggressiveness slider).
    Lower = more aggressive (removes more potential ads)
    Higher = more conservative (removes only high-confidence ads)

    Default value is MIN_CUT_CONFIDENCE from config.py
    """
    try:
        value = db.get_setting('min_cut_confidence')
        if value:
            threshold = float(value)
            # Clamp to valid range
            return max(0.50, min(0.95, threshold))
    except (ValueError, TypeError):
        pass
    return MIN_CUT_CONFIDENCE


def is_transient_error(error: Exception) -> bool:
    """Determine if an error is transient (worth retrying) or permanent.

    Delegates LLM API error classification to llm_client.is_retryable_error(),
    then applies episode-processing-specific checks for network, OOM, CDN, and
    audio format errors.
    """
    # Network/connection errors are transient
    if isinstance(error, (
        requests.exceptions.Timeout,
        requests.exceptions.ConnectionError,
        ConnectionError,
        TimeoutError,
    )):
        return True

    # Delegate LLM API error checks to the shared classifier
    if is_retryable_error(error):
        return True

    # Known LLM API error that wasn't retryable -- permanent
    if is_llm_api_error(error):
        return False

    # Permanent errors - don't retry
    if isinstance(error, (
        ValueError,
        FileNotFoundError,
        PermissionError,
        TypeError,
    )):
        return False

    # Check error message for patterns
    error_msg = str(error).lower()

    # OOM errors are PERMANENT - retrying without more RAM won't help
    oom_patterns = [
        'out of memory', 'oom', 'cuda out of memory',
        'cannot allocate memory', 'memory allocation failed',
        'killed', 'memoryerror', 'torch.cuda.outofmemoryerror',
    ]
    if any(pattern in error_msg for pattern in oom_patterns):
        return False

    # CDN errors are transient
    transient_patterns = [
        'cdn not ready', 'cdn timeout', 'cdn server error', 'cdn check failed',
    ]
    if any(pattern in error_msg for pattern in transient_patterns):
        return True

    # Permanent content/auth errors. 404 / "not found" is deliberately absent:
    # a freshly published episode 404s briefly while the host provisions the
    # media URL, so it is transient (the retry cap still fails a dead link).
    permanent_patterns = [
        'invalid audio', 'unsupported format', 'corrupt',
        'authentication', 'unauthorized', 'forbidden',
        '400 ', '401 ', '403 ',
    ]
    if any(pattern in error_msg for pattern in permanent_patterns):
        return False

    # Default: assume transient for unknown errors (safer to retry)
    return True


def _process_episode_background(slug, episode_id, original_url, title, podcast_name, description, artwork_url, published_at=None, cancel_event=None):
    """Background thread wrapper for process_episode with queue management."""
    from processing_queue import ProcessingQueue
    queue = ProcessingQueue()
    start_time = time.time()
    try:
        process_episode(slug, episode_id, original_url, title, podcast_name, description, artwork_url, published_at, cancel_event=cancel_event)
    except ProcessingCancelled:
        audio_logger.info(f"[{slug}:{episode_id}] Cancelled - cleaning up partial files")
        try:
            storage.delete_processed_file(slug, episode_id)
        except Exception as cleanup_err:
            audio_logger.warning(f"[{slug}:{episode_id}] Failed to clean up partial file: {cleanup_err}")
        # Reset DB status (before finally releases queue, preventing re-queue race)
        try:
            db.upsert_episode(slug, episode_id, status=EpisodeStatus.PENDING.value, error_message='Canceled by user')
        except Exception as db_err:
            audio_logger.warning(f"[{slug}:{episode_id}] Failed to reset status after cancel: {db_err}")
        status_service.complete_job()
    except Exception as e:
        # This outer handler only fires if process_episode's own error handling
        # raises (e.g., DB unreachable during _handle_processing_failure).
        # It's a best-effort retry of failure bookkeeping.
        audio_logger.error(f"[{slug}:{episode_id}] Background processing failed: {e}")
        try:
            episode_data = db.get_episode(slug, episode_id)
            _handle_processing_failure(slug, episode_id, title, podcast_name,
                                       episode_data, e, start_time)
        except Exception as handler_err:
            audio_logger.error(f"[{slug}:{episode_id}] Failed to handle failure: {handler_err}")
    finally:
        queue.release()
        with _cancel_events_lock:
            _cancel_events.pop(f"{slug}:{episode_id}", None)


def start_background_processing(slug, episode_id, original_url, title, podcast_name, description, artwork_url, published_at=None):
    """
    Start processing in background thread.

    Returns:
        Tuple of (started: bool, reason: str)
        - (True, "started") if processing was started
        - (False, "already_processing") if this episode is already being processed
        - (False, "queue_busy:slug:episode_id") if another episode is processing
    """
    from processing_queue import ProcessingQueue
    queue = ProcessingQueue()

    # Check if already processing this episode
    if queue.is_processing(slug, episode_id):
        return False, "already_processing"

    # Check if queue is busy with another episode
    if not queue.acquire(slug, episode_id, timeout=0):
        current = queue.get_current()
        if current:
            return False, f"queue_busy:{current[0]}:{current[1]}"
        return False, "queue_busy"

    # Update StatusService IMMEDIATELY after lock acquired (prevents race condition)
    # This ensures the new episode is tracked before any other episode can start
    status_service.start_job(slug, episode_id, title, podcast_name)

    # Create cancel event for cooperative cancellation
    cancel_event = threading.Event()
    key = f"{slug}:{episode_id}"
    with _cancel_events_lock:
        _cancel_events[key] = cancel_event

    # Start background thread
    processing_thread = threading.Thread(
        target=_process_episode_background,
        args=(slug, episode_id, original_url, title, podcast_name, description, artwork_url, published_at, cancel_event),
        daemon=True
    )
    processing_thread.start()

    return True, "started"


def _download_and_transcribe(slug, episode_id, episode_url, podcast_name):
    """Pipeline stage: Download audio and get/create transcript segments.

    Returns (audio_path, segments) or raises on failure.
    """
    segments = None
    transcript_text = storage.get_transcript(slug, episode_id)

    if transcript_text:
        # Prefer the saved whisper segments (with word-level timestamps) over
        # re-parsing the transcript text. parse_transcript_segments drops the
        # word timing that boundary refinement and detection rely on, which
        # measurably weakened first-pass detection in LLM-only mode (issue #349).
        # Fall back to the text parse if the original segments were never saved.
        segments = db.get_original_segments(slug, episode_id)
        if segments:
            audio_logger.info(
                f"[{slug}:{episode_id}] Reusing {len(segments)} saved whisper segments (word-level)")
        else:
            segments = parse_transcript_segments(transcript_text)

    if segments:
        # Existing usable transcript: reuse it and skip transcription. A
        # transcript that yields no segments falls through to a fresh
        # transcription below rather than proceeding with nothing.
        duration_min = segments[-1]['end'] / 60
        audio_logger.info(
            f"[{slug}:{episode_id}] Found existing transcript: "
            f"{len(segments)} segments, {duration_min:.1f} min")

        # Reuse the retained original audio instead of re-downloading from the
        # CDN when we kept it (issue #349 LLM-only reprocess). Copy it to a temp
        # working file so the retain-move and cleanup-unlink later in
        # process_episode operate on the copy, never on the retained original.
        original_path = storage.get_original_path(slug, episode_id)
        if original_path and os.path.exists(original_path):
            fd, audio_path = tempfile.mkstemp(suffix='.mp3')
            os.close(fd)
            shutil.copyfile(original_path, audio_path)
            audio_logger.info(f"[{slug}:{episode_id}] Reusing retained original audio (skipped download)")
        else:
            available, cdn_error = transcriber.check_audio_availability(episode_url)
            if not available:
                raise Exception(f"CDN not ready: {cdn_error}")

            audio_path = transcriber.download_audio(episode_url)
            if not audio_path:
                raise Exception("Failed to download audio")
    else:
        available, cdn_error = transcriber.check_audio_availability(episode_url)
        if not available:
            raise Exception(f"CDN not ready: {cdn_error}")

        audio_logger.info(f"[{slug}:{episode_id}] Downloading audio")
        audio_path = transcriber.download_audio(episode_url)
        if not audio_path:
            raise Exception("Failed to download audio")

        status_service.update_job_stage("pass1:transcribing", 20)
        audio_logger.info(f"[{slug}:{episode_id}] Starting transcription")
        language_override = get_feed_language_override(db, slug)
        segments = transcriber.transcribe_chunked(
            audio_path, podcast_name=podcast_name, language_override=language_override,
        )
        if not segments:
            raise Exception("Failed to transcribe audio")

        corrected_segments = 0
        for seg in segments:
            original = seg.get('text', '')
            fixed = sponsor_service.apply_transcript_corrections(original)
            if fixed != original:
                seg['text'] = fixed
                corrected_segments += 1
        if corrected_segments:
            audio_logger.info(
                f"[{slug}:{episode_id}] Applied transcript corrections to "
                f"{corrected_segments} segment(s)"
            )

        duration_min = segments[-1]['end'] / 60 if segments else 0
        audio_logger.info(f"[{slug}:{episode_id}] Transcription complete: {len(segments)} segments, {duration_min:.1f} min")

        transcript_text = transcriber.segments_to_text(segments)
        storage.save_transcript(slug, episode_id, transcript_text)
        storage.save_original_transcript(slug, episode_id, transcript_text)
        storage.save_original_segments(slug, episode_id, segments)

    return audio_path, segments


def _run_audio_analysis(slug, episode_id, audio_path, segments):
    """Pipeline stage: Run volume + transition detection on audio."""
    status_service.update_job_stage("pass1:analyzing", 25)
    audio_logger.info(f"[{slug}:{episode_id}] Running audio analysis")
    try:
        result = audio_analyzer.analyze(
            audio_path,
            transcript_segments=segments,
            status_callback=lambda stage, progress: status_service.update_job_stage(stage, progress)
        )
        if result.signals:
            audio_logger.info(
                f"[{slug}:{episode_id}] Audio analysis: {len(result.signals)} signals "
                f"in {result.analysis_time_seconds:.1f}s"
            )
        if result.errors:
            for err in result.errors:
                audio_logger.warning(f"[{slug}:{episode_id}] Audio analysis warning: {err}")

        db.save_episode_audio_analysis(slug, episode_id, json.dumps(result.to_dict()))
        return result
    except Exception as e:
        audio_logger.error(f"[{slug}:{episode_id}] Audio analysis failed: {e}")
        return None


def _detect_ads_first_pass(ctx, segments, audio_path,
                            skip_patterns, audio_analysis_result,
                            progress_callback, cancel_event=None,
                            positional_prior_hint=""):
    """Pipeline stage: Run first-pass Claude ad detection.

    Returns (first_pass_ads, first_pass_count, ad_result).
    """
    slug = ctx.slug
    episode_id = ctx.episode_id
    status_service.update_job_stage("pass1:detecting", 50)
    clear_fallback(episode_id, PASS_AD_DETECTION_1)

    ad_result = ad_detector.process_transcript(
        segments,
        audio_path=audio_path,
        skip_patterns=skip_patterns,
        progress_callback=progress_callback,
        audio_analysis=audio_analysis_result,
        cancel_event=cancel_event,
        ctx=ctx,
        positional_prior_hint=positional_prior_hint,
    )
    storage.save_ads_json(slug, episode_id, ad_result, pass_number=1)

    ad_detection_status = ad_result.get('status', 'success')
    first_pass_ads = ad_result.get('ads', [])

    if ad_detection_status == 'failed':
        error_msg = ad_result.get('error', 'Unknown error')
        audio_logger.error(f"[{slug}:{episode_id}] Ad detection failed: {error_msg}")
        db.upsert_episode(slug, episode_id, ad_detection_status='failed')
        raise Exception(f"Ad detection failed: {error_msg}")

    db.upsert_episode(slug, episode_id, ad_detection_status='success')

    if first_pass_ads:
        total_ad_time = sum(ad['end'] - ad['start'] for ad in first_pass_ads)
        audio_logger.info(f"[{slug}:{episode_id}] First pass: Detected {len(first_pass_ads)} ads ({total_ad_time/60:.1f} min)")
    else:
        audio_logger.info(f"[{slug}:{episode_id}] First pass: No ads detected")

    return first_pass_ads, len(first_pass_ads), ad_result


def _vad_gap_enabled(db) -> bool:
    """Read the vad_gap_detection_enabled setting (default True)."""
    value = db.get_setting('vad_gap_detection_enabled')
    if value is None:
        return True
    return str(value).strip().lower() != 'false'


def _setting_float(db, key: str, default: float) -> float:
    """Read a float setting with graceful fallback on missing/invalid values."""
    value = db.get_setting(key)
    if value is None or value == '':
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        audio_logger.warning(f"Invalid float for setting {key!r}: {value!r}; using default {default}")
        return default
    return parsed if parsed > 0 else default


def _refine_boundaries(all_ads, segments):
    """Apply the four-step ad-boundary refinement pipeline. Returns updated list."""
    if all_ads and segments:
        all_ads = refine_ad_boundaries(all_ads, segments)
    if all_ads and segments:
        all_ads = extend_ad_boundaries_by_content(all_ads, segments)
    if all_ads:
        all_ads = snap_early_ads_to_zero(all_ads)
    if all_ads and segments:
        all_ads = merge_same_sponsor_ads(all_ads, segments)
    return all_ads


def _apply_heuristic_rolls(slug, episode_id, all_ads, segments, podcast_name,
                            episode_duration, skip_patterns, db):
    """Append heuristic pre/post-roll and VAD-gap ads to ``all_ads`` in place."""
    if not segments:
        return
    from roll_detector import detect_preroll, detect_postroll
    preroll_ad = detect_preroll(segments, all_ads, podcast_name=podcast_name,
                                skip_patterns=skip_patterns)
    if preroll_ad:
        all_ads.append(preroll_ad)
        audio_logger.info(f"[{slug}:{episode_id}] Heuristic pre-roll: 0.0s-{preroll_ad['end']:.1f}s")

    postroll_ad = detect_postroll(segments, all_ads, episode_duration=episode_duration)
    if postroll_ad:
        all_ads.append(postroll_ad)
        audio_logger.info(f"[{slug}:{episode_id}] Heuristic post-roll: {postroll_ad['start']:.1f}s-{postroll_ad['end']:.1f}s")

    # VAD-gap detection (head/mid/tail)
    if _vad_gap_enabled(db):
        from vad_gap_detector import detect_vad_gaps
        vad_gap_ads = detect_vad_gaps(
            segments, all_ads, episode_duration,
            start_min_seconds=_setting_float(db, 'vad_gap_start_min_seconds', 3.0),
            mid_min_seconds=_setting_float(db, 'vad_gap_mid_min_seconds', 8.0),
            tail_min_seconds=_setting_float(db, 'vad_gap_tail_min_seconds', 3.0),
        )
        if vad_gap_ads:
            all_ads.extend(vad_gap_ads)
            audio_logger.info(
                f"[{slug}:{episode_id}] VAD gap detector: {len(vad_gap_ads)} gap(s) marked"
            )


def _load_user_corrections(slug, episode_id, db):
    """Load FP and confirmed corrections for the episode and log counts."""
    false_positive_corrections = db.get_false_positive_corrections(episode_id)
    if false_positive_corrections:
        audio_logger.info(f"[{slug}:{episode_id}] Loaded {len(false_positive_corrections)} false positive corrections")

    confirmed_corrections = db.get_confirmed_corrections(episode_id)
    if confirmed_corrections:
        audio_logger.info(f"[{slug}:{episode_id}] Loaded {len(confirmed_corrections)} confirmed corrections")

    return false_positive_corrections, confirmed_corrections


def _gate_validation_by_confidence(slug, episode_id, validation_ads, min_cut_confidence):
    """Apply ACCEPT/REJECT/REVIEW confidence gating. Returns (ads_to_remove, low_confidence_count)."""
    ads_to_remove = []
    low_confidence_count = 0
    for ad in validation_ads:
        validation = ad.get('validation', {})
        decision = validation.get('decision')
        if decision == 'REJECT':
            ad['was_cut'] = False
            continue
        if decision == 'ACCEPT':
            ad['was_cut'] = True
            ads_to_remove.append(ad)
            continue
        confidence = validation.get('adjusted_confidence', ad.get('confidence', 1.0))
        if confidence < min_cut_confidence:
            low_confidence_count += 1
            ad['was_cut'] = False
            audio_logger.info(
                f"[{slug}:{episode_id}] Keeping REVIEW ad in audio: "
                f"{ad['start']:.1f}s-{ad['end']:.1f}s ({confidence:.0%} < {min_cut_confidence:.0%})"
            )
            continue
        ad['was_cut'] = True
        ads_to_remove.append(ad)
    return ads_to_remove, low_confidence_count


def _refine_and_validate(slug, episode_id, all_ads, segments, audio_path,
                          episode_description, episode_duration, min_cut_confidence,
                          podcast_name, skip_patterns=False, positional_prior=None):
    """Pipeline stage: Refine ad boundaries, detect rolls, validate, gate by confidence.

    Returns (ads_to_remove, all_ads_with_validation).
    """
    from ad_validator import AdValidator

    # Boundary refinement
    all_ads = _refine_boundaries(all_ads, segments)

    # Heuristic pre/post-roll detection
    _apply_heuristic_rolls(slug, episode_id, all_ads, segments, podcast_name,
                            episode_duration, skip_patterns, db)

    # Validation
    if not all_ads:
        return [], []

    false_positive_corrections, confirmed_corrections = _load_user_corrections(
        slug, episode_id, db
    )

    validator = AdValidator(
        episode_duration, segments, episode_description,
        false_positive_corrections=false_positive_corrections,
        confirmed_corrections=confirmed_corrections,
        min_cut_confidence=min_cut_confidence,
        positional_prior=positional_prior
    )
    validation_result = validator.validate(all_ads)

    audio_logger.info(
        f"[{slug}:{episode_id}] Validation: "
        f"{validation_result.accepted} accepted, "
        f"{validation_result.reviewed} review, "
        f"{validation_result.rejected} rejected"
    )

    # Confidence gating: ACCEPT = cut, REJECT = keep, REVIEW = threshold check
    ads_to_remove, low_confidence_count = _gate_validation_by_confidence(
        slug, episode_id, validation_result.ads, min_cut_confidence
    )

    all_ads_with_validation = validation_result.ads
    storage.save_combined_ads(slug, episode_id, all_ads_with_validation)

    # Learn patterns from cut ads
    cut_ads = [a for a in all_ads_with_validation if a.get('was_cut')]
    if cut_ads and slug:
        patterns_learned = ad_detector.learn_from_detections(
            cut_ads, segments, slug, episode_id, audio_path=audio_path
        )
        if patterns_learned > 0:
            audio_logger.info(f"[{slug}:{episode_id}] Learned {patterns_learned} new patterns from cut ads")

    rejected_count = validation_result.rejected
    if rejected_count > 0 or low_confidence_count > 0:
        audio_logger.info(
            f"[{slug}:{episode_id}] Kept in audio: {rejected_count} rejected, "
            f"{low_confidence_count} low-confidence (<{min_cut_confidence:.0%})"
        )

    return ads_to_remove, all_ads_with_validation


def _build_reviewer(db, ad_detector) -> AdReviewer:
    return AdReviewer(
        db=db,
        llm_client=ad_detector._llm_client,
        sponsor_service=getattr(ad_detector, 'sponsor_service', None),
        sponsor_history_provider=ad_detector._get_podcast_sponsor_history,
    )


def _build_episode_meta(slug, episode_id, podcast_id, podcast_name,
                        episode_title, podcast_description, episode_description):
    return {
        'podcast_name': podcast_name,
        'episode_title': episode_title,
        'episode_description': episode_description,
        'podcast_description': podcast_description,
        'slug': slug,
        'episode_id': episode_id,
        'podcast_id': podcast_id,
    }


def _apply_pass2_reviewer(ctx, v_ads_to_cut, v_ads_for_ui,
                           verification_ads_processed, verification_ads_original,
                           original_segments, min_cut_confidence):
    """Run the reviewer on pass 2 results, in original transcript coordinates.

    Mutates ``v_ads_to_cut`` and ``v_ads_for_ui`` in place. Adjust verdicts
    are coerced to confirmed in pass 2 because applying a boundary shift in
    original coords cannot safely round-trip through pass 1 cuts to processed
    coords; supporting it would require a per-pass-1-cut timestamp map.
    """
    slug = ctx.slug
    episode_id = ctx.episode_id
    podcast_name = ctx.podcast_name
    episode_title = ctx.episode_title
    podcast_description = ctx.podcast_description
    episode_description = ctx.episode_description
    clear_fallback(episode_id, PASS_REVIEWER_2)

    if not _ad_review_enabled(db):
        return

    accepted_originals = list(v_ads_for_ui)
    if not accepted_originals and not verification_ads_original:
        return

    eligible_originals = split_resurrection_pool(
        verification_ads_original, accepted_originals, min_cut_confidence
    )
    if not accepted_originals and not eligible_originals:
        return

    status_service.update_job_stage("pass2:reviewing", 90)

    podcast_row = db.get_podcast_by_slug(slug)
    podcast_id = podcast_row.get('id') if podcast_row else None

    audio_logger.info(
        f"[{slug}:{episode_id}] Reviewer pass 2: "
        f"{len(accepted_originals)} accepted + {len(eligible_originals)} resurrection-eligible"
    )

    reviewer = _build_reviewer(db, ad_detector)
    episode_meta = _build_episode_meta(
        slug, episode_id, podcast_id, podcast_name,
        episode_title, podcast_description, episode_description,
    )
    pass2_model = ad_detector.get_verification_model()
    result = reviewer.review(
        accepted_ads=accepted_originals,
        resurrection_eligible=eligible_originals,
        segments=original_segments or [],
        episode_meta=episode_meta,
        pass_num=2,
        pass_model=pass2_model,
    )

    # Index by (start, end) so verdict application is O(V), not O(V*N).
    original_to_processed = {
        (orig.get('start'), orig.get('end')): proc
        for orig, proc in zip(verification_ads_original, verification_ads_processed)
    }
    ui_by_key = {(a.get('start'), a.get('end')): a for a in v_ads_for_ui}
    original_by_key = {(a.get('start'), a.get('end')): a for a in verification_ads_original}

    def _stamp(ad, v):
        ad['reviewer_verdict'] = v.verdict
        if v.reasoning is not None:
            ad['reviewer_reasoning'] = v.reasoning
        if v.confidence is not None:
            ad['reviewer_confidence'] = v.confidence
        if v.model_used:
            ad['reviewer_model'] = v.model_used

    for v in result.verdicts:
        key = (v.original_start, v.original_end)
        proc_ad = original_to_processed.get(key)
        ui_ad = ui_by_key.get(key)

        if v.verdict == 'adjust':
            # Pass 2 cannot safely round-trip a boundary shift across pass 1
            # cuts, so coerce to confirmed instead of mutating boundaries.
            audio_logger.info(
                f"[{slug}:{episode_id}] Pass 2 reviewer proposed adjust "
                f"@ {v.original_start:.1f}s; treating as confirmed"
            )
            coerced = ReviewVerdict(
                pool=v.pool, pass_num=v.pass_num, verdict='confirmed',
                original_start=v.original_start, original_end=v.original_end,
                reasoning=v.reasoning, confidence=v.confidence,
                model_used=v.model_used, latency_ms=v.latency_ms,
                success=v.success,
            )
            if proc_ad is not None:
                _stamp(proc_ad, coerced)
            if ui_ad is not None:
                _stamp(ui_ad, coerced)
            continue

        if v.verdict == 'reject':
            if proc_ad in v_ads_to_cut:
                v_ads_to_cut.remove(proc_ad)
            if ui_ad is not None:
                _stamp(ui_ad, v)
                ui_ad['was_cut'] = False
                ui_ad['source'] = 'reviewer'
                v_ads_for_ui.remove(ui_ad)
            continue

        if v.verdict == 'resurrect':
            if proc_ad is None:
                # Without a processed-coord twin we cannot add to the recut
                # list; UI would falsely show it cut. Drop the resurrection
                # rather than create that mismatch.
                audio_logger.warning(
                    f"[{slug}:{episode_id}] Pass 2 resurrect dropped "
                    f"@ {v.original_start:.1f}s: no processed-coord twin"
                )
                continue
            if proc_ad not in v_ads_to_cut:
                proc_ad['was_cut'] = True
                proc_ad['detection_stage'] = 'verification'
                proc_ad['source'] = 'reviewer'
                _stamp(proc_ad, v)
                v_ads_to_cut.append(proc_ad)
            orig_ad = original_by_key.get(key)
            if orig_ad is not None:
                orig_ad['was_cut'] = True
                orig_ad['detection_stage'] = 'verification'
                orig_ad['source'] = 'reviewer'
                _stamp(orig_ad, v)
                if orig_ad not in v_ads_for_ui:
                    v_ads_for_ui.append(orig_ad)
            continue

        # confirmed or failure: stamp reviewer fields without mutating cuts.
        if proc_ad is not None:
            _stamp(proc_ad, v)
        if ui_ad is not None:
            _stamp(ui_ad, v)

    audio_logger.info(
        f"[{slug}:{episode_id}] Reviewer pass 2 verdicts: "
        f"{sum(1 for v in result.verdicts if v.verdict == 'confirmed')} confirmed, "
        f"{sum(1 for v in result.verdicts if v.verdict == 'adjust')} adjusted, "
        f"{sum(1 for v in result.verdicts if v.verdict == 'reject')} rejected, "
        f"{sum(1 for v in result.verdicts if v.verdict == 'resurrect')} resurrected, "
        f"{sum(1 for v in result.verdicts if v.verdict == 'failure')} failed"
    )


def _ad_review_enabled(db) -> bool:
    """Read the opt-in flag for the LLM ad reviewer."""
    try:
        value = db.get_setting('enable_ad_review')
    except Exception:
        return False
    return str(value or '').strip().lower() == 'true'


def _apply_reviewer_verdict_to_ad(ad, v):
    """Merge a single reviewer verdict into the master ad dict, in place."""
    ad['reviewer_verdict'] = v.verdict
    if v.reasoning is not None:
        ad['reviewer_reasoning'] = v.reasoning
    if v.confidence is not None:
        ad['reviewer_confidence'] = v.confidence
    if v.model_used:
        ad['reviewer_model'] = v.model_used
    if v.verdict == 'adjust':
        ad['reviewer_original_start'] = v.original_start
        ad['reviewer_original_end'] = v.original_end
        ad['start'] = v.adjusted_start
        ad['end'] = v.adjusted_end
    elif v.verdict == 'reject':
        ad['was_cut'] = False
        ad['source'] = 'reviewer'
    elif v.verdict == 'resurrect':
        ad['was_cut'] = True
        ad['source'] = 'reviewer'


def _merge_reviewer_result(result, all_ads_with_validation):
    """Apply reviewer verdicts to the master ad list and append any newly
    resurrected ads. Mutates ``all_ads_with_validation`` in place.
    """
    # Index by (start, end) so the verdict loop is O(V), not O(V*N).
    master_by_key = {(a.get('start'), a.get('end')): a for a in all_ads_with_validation}
    for v in result.verdicts:
        ad = master_by_key.get((v.original_start, v.original_end))
        if ad is None:
            continue
        _apply_reviewer_verdict_to_ad(ad, v)

    for ad in result.resurrected:
        key = (ad.get('start'), ad.get('end'))
        if key not in master_by_key:
            all_ads_with_validation.append(ad)
            master_by_key[key] = ad


def _run_ad_reviewer(slug, episode_id, podcast_id, ads_to_remove,
                     all_ads_with_validation, segments, podcast_name,
                     episode_title, episode_description, podcast_description,
                     min_cut_confidence, pass_num, pass_model):
    """Run the LLM ad reviewer over the cut list and resurrection-eligible
    rejects. Returns updated ``(ads_to_remove, all_ads_with_validation)``.

    Non-blocking: any failure inside the reviewer falls through with the
    original lists. Skips entirely when ``enable_ad_review`` is false.
    """
    clear_fallback(episode_id, PASS_REVIEWER_1 if pass_num == 1 else PASS_REVIEWER_2)

    if not _ad_review_enabled(db):
        return ads_to_remove, all_ads_with_validation

    eligible = split_resurrection_pool(
        all_ads_with_validation, ads_to_remove, min_cut_confidence
    )
    if not ads_to_remove and not eligible:
        return ads_to_remove, all_ads_with_validation

    status_service.update_job_stage(f"pass{pass_num}:reviewing", 75)

    audio_logger.info(
        f"[{slug}:{episode_id}] Reviewer pass {pass_num}: "
        f"{len(ads_to_remove)} accepted + {len(eligible)} resurrection-eligible"
    )

    reviewer = _build_reviewer(db, ad_detector)
    episode_meta = _build_episode_meta(
        slug, episode_id, podcast_id, podcast_name,
        episode_title, podcast_description, episode_description,
    )
    result = reviewer.review(
        accepted_ads=ads_to_remove,
        resurrection_eligible=eligible,
        segments=segments,
        episode_meta=episode_meta,
        pass_num=pass_num,
        pass_model=pass_model,
    )

    new_ads_to_remove = list(result.accepted_after_review)

    # Merge reviewer fields into the master list (in-place), and pull in any
    # resurrected ads that weren't there before. Index by (start, end) so the
    # verdict loop is O(V), not O(V*N).
    _merge_reviewer_result(result, all_ads_with_validation)

    audio_logger.info(
        f"[{slug}:{episode_id}] Reviewer pass {pass_num} verdicts: "
        f"{sum(1 for v in result.verdicts if v.verdict == 'confirmed')} confirmed, "
        f"{sum(1 for v in result.verdicts if v.verdict == 'adjust')} adjusted, "
        f"{sum(1 for v in result.verdicts if v.verdict == 'reject')} rejected, "
        f"{sum(1 for v in result.verdicts if v.verdict == 'resurrect')} resurrected, "
        f"{sum(1 for v in result.verdicts if v.verdict == 'failure')} failed"
    )

    # Persist the reviewer's mutations. The downstream save in process_episode
    # is gated on v_ads_for_ui being non-empty, so a pass-2 reviewer that
    # rejects everything will skip that save and lose pass-1 reviewer fields.
    storage.save_combined_ads(slug, episode_id, all_ads_with_validation)

    return new_ads_to_remove, all_ads_with_validation


def _complete_cut_tails(slug, episode_id, ads_to_remove, all_ads_with_validation,
                        segments):
    """Re-run content-based end extension as the last step before cutting.

    This sweep exists to undo reviewer end-pullbacks: the reviewer can pull a
    cut's end back to the detector boundary, and the pre-reviewer extension
    pass in _refine_boundaries never sees that. Without the reviewer enabled,
    _refine_boundaries already extended these ends and a second pass would
    just compound the extension window, so the sweep is gated on the reviewer.
    End-only: starts don't drift short.

    Mutates matching ``all_ads_with_validation`` entries in place and re-saves
    combined ads when anything changed. Returns the (possibly extended) cut list.
    """
    if not ads_to_remove or not segments:
        return ads_to_remove
    if not _ad_review_enabled(db):
        return ads_to_remove

    extended = extend_ad_boundaries_by_content(
        ads_to_remove, segments, extend_start=False
    )

    changed = False
    for old, new in zip(ads_to_remove, extended):
        if new['end'] <= old['end']:
            continue
        # Never extend a cut into the next detected ad: overlapping spans in
        # combined_ads.json double-subtract in timestamp mapping.
        next_start = min(
            (m['start'] for m in all_ads_with_validation
             if m.get('start') is not None and m['start'] >= old['end']),
            default=None,
        )
        if next_start is not None and new['end'] > next_start:
            new['end'] = next_start
        if new['end'] <= old['end']:
            continue
        changed = True
        audio_logger.info(
            f"[{slug}:{episode_id}] Tail completion: cut end "
            f"{old['end']:.1f}s -> {new['end']:.1f}s "
            f"(+{new['end'] - old['end']:.1f}s, {new.get('sponsor', 'unknown')})"
        )
        for master in all_ads_with_validation:
            if master is old or (master.get('start') == old['start']
                                 and master.get('end') == old['end']):
                master['end'] = new['end']
                master['tail_completed'] = True
                break
        else:
            audio_logger.warning(
                f"[{slug}:{episode_id}] Tail completion: no master ad matched "
                f"{old['start']:.1f}s-{old['end']:.1f}s; UI list will not show "
                f"the extension"
            )

    if not changed:
        return ads_to_remove

    storage.save_combined_ads(slug, episode_id, all_ads_with_validation)
    return extended


def _apply_pass2_heuristic_rolls(slug, episode_id, verification_ads_processed,
                                  verification_ads_original, verification_segments,
                                  ads_to_remove, podcast_name, skip_patterns):
    """Append pass-2 heuristic pre/post-rolls in both processed and original coords."""
    if not verification_segments:
        return
    from roll_detector import detect_preroll, detect_postroll
    from verification_pass import _build_timestamp_map, _map_to_original

    processed_dur = verification_segments[-1]['end'] if verification_segments else 0
    ts_map = _build_timestamp_map(ads_to_remove) if ads_to_remove else None

    preroll_v = detect_preroll(verification_segments, verification_ads_processed,
                              podcast_name=podcast_name, skip_patterns=skip_patterns)
    if preroll_v:
        verification_ads_processed.append(preroll_v)
        mapped = preroll_v.copy()
        if ts_map:
            mapped['start'] = _map_to_original(preroll_v['start'], ts_map)
            mapped['end'] = _map_to_original(preroll_v['end'], ts_map)
        verification_ads_original.append(mapped)
        audio_logger.info(f"[{slug}:{episode_id}] Pass 2 heuristic pre-roll: 0.0s-{preroll_v['end']:.1f}s")

    postroll_v = detect_postroll(verification_segments, verification_ads_processed, episode_duration=processed_dur)
    if postroll_v:
        verification_ads_processed.append(postroll_v)
        mapped = postroll_v.copy()
        if ts_map:
            mapped['start'] = _map_to_original(postroll_v['start'], ts_map)
            mapped['end'] = _map_to_original(postroll_v['end'], ts_map)
        verification_ads_original.append(mapped)
        audio_logger.info(f"[{slug}:{episode_id}] Pass 2 heuristic post-roll: {postroll_v['start']:.1f}s-{postroll_v['end']:.1f}s")


def _validate_verification_ads(slug, episode_id, verification_ads_processed,
                                verification_ads_original, verification_segments,
                                ads_to_remove, episode_description,
                                min_cut_confidence, db,
                                processed_duration=None):
    """Validate pass-2 ad candidates against processed-coordinate validator.

    Maps pass-1 user FP corrections from original to processed coordinates,
    then filters both processed and original ad lists by validator decision.
    ``processed_duration`` is the real (ffprobe) duration of the pass-1
    output; the validator clamps and extends trailing ads against it. Falls
    back to the last transcript segment's end when not provided.
    Returns (verification_ads_processed, verification_ads_original).
    """
    from ad_validator import AdValidator
    # Pass-1 cut user-rejections in original time; verification
    # operates on cut audio, so map them to processed coordinates
    # before the validator can use them to auto-reject overlaps.
    from verification_pass import _build_timestamp_map, _map_correction_to_processed
    fp_corrections_orig = db.get_false_positive_corrections(episode_id) or []
    fp_corrections_processed = []
    if fp_corrections_orig:
        ts_map = _build_timestamp_map(ads_to_remove) if ads_to_remove else []
        for c in fp_corrections_orig:
            proc = _map_correction_to_processed(c['start'], c['end'], ts_map)
            if proc is not None:
                fp_corrections_processed.append({'start': proc[0], 'end': proc[1]})
        if fp_corrections_processed:
            audio_logger.info(
                f"[{slug}:{episode_id}] Pass 2 honoring "
                f"{len(fp_corrections_processed)} user-rejected region(s)"
            )

    if not processed_duration:
        # Whisper's last segment end approximates the file duration but can
        # over- or under-run it; only used when the ffprobe probe failed.
        processed_duration = verification_segments[-1]['end']
    # No positional_prior here: pass 2 runs in processed-audio coordinates
    # (post-cut timeline), so zones learned on original durations do not map.
    v_validator = AdValidator(
        processed_duration, verification_segments,
        episode_description,
        false_positive_corrections=fp_corrections_processed,
        min_cut_confidence=min_cut_confidence,
    )

    # Pair each processed candidate with its original-coords twin before
    # validation: validate() sorts, merges and drops ads, so positional
    # indexing against the unvalidated original list mispairs. The shallow
    # ad.copy() inside validate() carries the reference through; a merge
    # keeps the surviving ad's twin and drops the absorbed one, so the
    # original list mirrors the processed list 1:1.
    for proc, orig in zip(verification_ads_processed, verification_ads_original):
        proc['_orig_twin'] = orig

    v_validation = v_validator.validate(verification_ads_processed)

    # validate() worked on copies; strip the key from the input dicts too so
    # no later consumer of the raw verification result can serialize it.
    for proc in verification_ads_processed:
        proc.pop('_orig_twin', None)

    kept_processed, kept_original = [], []
    for ad in v_validation.ads:
        # Strip the pairing key from every validator output (rejected ones
        # included) so it can never leak into serialized payloads.
        orig = ad.pop('_orig_twin', None)
        if ad.get('validation', {}).get('decision') == 'REJECT':
            continue
        if orig is None:
            audio_logger.warning(
                f"[{slug}:{episode_id}] Pass 2 ad {ad['start']:.1f}s-"
                f"{ad['end']:.1f}s has no original twin after validation; "
                f"dropping"
            )
            continue
        kept_processed.append(ad)
        kept_original.append(orig)
    return kept_processed, kept_original


def _gate_verification_ads_by_confidence(verification_ads_processed,
                                          verification_ads_original,
                                          min_cut_confidence):
    """Confidence gate pass-2 ads. Returns (v_ads_to_cut, v_ads_for_ui)."""
    v_ads_to_cut = []
    v_ads_for_ui = []
    for i, ad in enumerate(verification_ads_processed):
        confidence = ad.get('validation', {}).get('adjusted_confidence', ad.get('confidence', 1.0))
        if confidence >= min_cut_confidence:
            ad['was_cut'] = True
            ad['detection_stage'] = 'verification'
            v_ads_to_cut.append(ad)
            orig_ad = verification_ads_original[i]
            orig_ad['was_cut'] = True
            orig_ad['detection_stage'] = 'verification'
            v_ads_for_ui.append(orig_ad)
        else:
            ad['was_cut'] = False
    return v_ads_to_cut, v_ads_for_ui


def _recut_processed_audio(slug, episode_id, processed_path, v_ads_to_cut,
                            local_audio_processor):
    """Re-cut the pass-1 processed audio with verification ads.

    Returns (processed_path, recut_applied, recut_ok) where recut_applied is
    the cut list ffmpeg actually applied. recut_ok is False if the re-cut
    failed (caller should clear v_ads_for_ui).
    """
    recut_result = local_audio_processor.process_episode(processed_path, v_ads_to_cut)
    if recut_result:
        recut_path, recut_applied = recut_result
        if os.path.exists(processed_path):
            try:
                os.unlink(processed_path)
            except OSError as e:
                audio_logger.warning(f"[{slug}:{episode_id}] Failed to remove old processed file: {e}")
        processed_path = recut_path
        audio_logger.info(f"[{slug}:{episode_id}] Re-cut pass 1 output, removed {len(recut_applied)} additional ads")
        return processed_path, recut_applied, True
    audio_logger.error(f"[{slug}:{episode_id}] Verification re-cut failed, keeping pass 1 output")
    return processed_path, None, False


def _covered_by_cuts(ad, applied_cuts, total_duration=None, tolerance=0.01):
    """True when ``ad`` falls inside one of the cuts ffmpeg applied.

    ``total_duration`` clamps the ad to the audio bounds first: applied cuts
    are clamped (compute_applied_cuts), so an ad whose end overruns the file
    (Whisper's last segment vs ffprobe) would never match its own cut.
    """
    start = max(0.0, ad['start'])
    end = min(ad['end'], total_duration) if total_duration else ad['end']
    return any(c['start'] <= start + tolerance and end <= c['end'] + tolerance
               for c in applied_cuts)


def _drop_uncovered_pass2_ads(slug, episode_id, v_ads_to_cut, v_ads_for_ui,
                               recut_applied, verification_ads_processed,
                               verification_ads_original, total_duration=None):
    """Drop pass-2 ads the recut did not actually remove (e.g. <10s filtered).

    Mutates v_ads_to_cut / v_ads_for_ui in place so the count and the UI list
    only claim cuts that exist in the audio. Merged-away ads still count: a
    merged span covers its members.
    """
    twin = {id(p): o for p, o in zip(verification_ads_processed,
                                     verification_ads_original)}
    for ad in [a for a in v_ads_to_cut
               if not _covered_by_cuts(a, recut_applied, total_duration)]:
        v_ads_to_cut.remove(ad)
        ad['was_cut'] = False
        ui_ad = twin.get(id(ad))
        if ui_ad is not None:
            ui_ad['was_cut'] = False
            for i, u in enumerate(v_ads_for_ui):
                if u is ui_ad:
                    del v_ads_for_ui[i]
                    break
        audio_logger.info(
            f"[{slug}:{episode_id}] Pass 2 ad {ad['start']:.1f}s-{ad['end']:.1f}s "
            f"was filtered out of the recut; not counting it as removed"
        )


def _run_verification_pass(ctx, processed_path, pass1_cuts,
                            skip_patterns, min_cut_confidence,
                            local_audio_processor, progress_callback,
                            original_segments=None, reuse_transcript=False):
    """Pipeline stage: Run verification (second pass) on processed audio.

    ``pass1_cuts`` must be the cuts ffmpeg actually applied (see
    compute_applied_cuts), not the requested list -- every use here is
    processed-to-original timestamp mapping.

    Returns (verification_count, v_ads_for_ui, processed_path,
    verification_cue_count).
    """
    slug = ctx.slug
    episode_id = ctx.episode_id
    podcast_name = ctx.podcast_name
    episode_title = ctx.episode_title
    episode_description = ctx.episode_description
    podcast_description = ctx.podcast_description
    verification_count = 0
    v_ads_for_ui = []
    verification_cue_count = 0
    clear_fallback(episode_id, PASS_AD_DETECTION_2)

    try:
        from verification_pass import VerificationPass
        verifier = VerificationPass(
            ad_detector=ad_detector, transcriber=transcriber,
            audio_analyzer=audio_analyzer, pattern_service=pattern_service,
            db=db,
        )
        verification_result = verifier.verify(
            processed_audio_path=processed_path,
            podcast_name=podcast_name, episode_title=episode_title,
            slug=slug, episode_id=episode_id,
            pass1_cuts=pass1_cuts,
            episode_description=episode_description,
            podcast_description=podcast_description,
            skip_patterns=skip_patterns,
            progress_callback=progress_callback,
            original_segments=original_segments,
            reuse_transcript=reuse_transcript,
        )
        verification_ads_original = verification_result.get('ads', [])
        verification_ads_processed = verification_result.get('ads_processed', [])
        verification_segments = verification_result.get('segments', [])
        verification_cue_count = verification_result.get('audio_cue_count', 0)
        storage.save_ads_json(slug, episode_id, verification_result, pass_number=2)

        # Heuristic roll detection on pass 2
        _apply_pass2_heuristic_rolls(
            slug, episode_id, verification_ads_processed,
            verification_ads_original, verification_segments,
            pass1_cuts, podcast_name, skip_patterns,
        )

        if verification_ads_processed:
            audio_logger.info(f"[{slug}:{episode_id}] Verification found {len(verification_ads_processed)} missed ads - re-cutting pass 1 output")

            # Real duration of the pass-1 output, probed once: validation
            # clamps and extends trailing ads against it (Whisper's last
            # segment end can over- or under-run the file), and the recut
            # coverage check below needs the same pre-recut bounds.
            processed_duration = local_audio_processor.get_audio_duration(processed_path)

            # Validate verification ads
            if verification_segments:
                verification_ads_processed, verification_ads_original = _validate_verification_ads(
                    slug, episode_id, verification_ads_processed,
                    verification_ads_original, verification_segments,
                    pass1_cuts, episode_description,
                    min_cut_confidence, db,
                    processed_duration=processed_duration,
                )

            if verification_ads_processed:
                # Confidence gate and re-cut
                v_ads_to_cut, v_ads_for_ui = _gate_verification_ads_by_confidence(
                    verification_ads_processed, verification_ads_original,
                    min_cut_confidence,
                )

                # Pass 2 reviewer operates on original-coord ads (the prompt
                # context window comes from the original transcript). Adjust
                # verdicts are coerced to confirmed in pass 2 because mapping
                # a boundary shift back to processed coordinates is unsafe
                # across pass 1 cuts.
                _apply_pass2_reviewer(
                    ctx,
                    v_ads_to_cut, v_ads_for_ui,
                    verification_ads_processed, verification_ads_original,
                    original_segments, min_cut_confidence,
                )

                if v_ads_to_cut:
                    # Probed above, before the recut deletes the pre-recut
                    # file: the coverage check needs the bounds the recut
                    # clamped to.
                    pre_recut_duration = processed_duration
                    processed_path, recut_applied, recut_ok = _recut_processed_audio(
                        slug, episode_id, processed_path, v_ads_to_cut,
                        local_audio_processor,
                    )
                    if recut_ok:
                        _drop_uncovered_pass2_ads(
                            slug, episode_id, v_ads_to_cut, v_ads_for_ui,
                            recut_applied, verification_ads_processed,
                            verification_ads_original, pre_recut_duration,
                        )
                        verification_count = len(v_ads_to_cut)
                    else:
                        v_ads_for_ui = []
        else:
            audio_logger.info(f"[{slug}:{episode_id}] Verification: clean")

    except Exception as e:
        audio_logger.error(f"[{slug}:{episode_id}] Verification pass failed: {e}")

    return verification_count, v_ads_for_ui, processed_path, verification_cue_count


def _generate_assets(slug, episode_id, segments, all_cuts, episode_description,
                      podcast_name, episode_title):
    """Pipeline stage: Generate VTT transcript and chapters."""
    from transcript_generator import TranscriptGenerator
    from chapters_generator import ChaptersGenerator
    try:
        vtt_enabled = db.get_setting('vtt_transcripts_enabled')
        transcript_gen = TranscriptGenerator()

        # Persist final segments unconditionally; consumers (e.g. the offline
        # benchmark) need them even when VTT generation is disabled.
        final_segments = transcript_gen.compute_final_segments(segments, all_cuts)
        storage.save_final_segments(slug, episode_id, final_segments)

        if vtt_enabled is None or vtt_enabled.lower() == 'true':
            vtt_content = transcript_gen.generate_vtt(segments, all_cuts)
            if vtt_content and len(vtt_content) > 10:
                storage.save_transcript_vtt(slug, episode_id, vtt_content)
                audio_logger.info(f"[{slug}:{episode_id}] Generated VTT transcript")

        processed_text = transcript_gen.generate_text(segments, all_cuts)
        if processed_text:
            db.save_episode_details(slug, episode_id, transcript_text=processed_text)

        chapters_enabled = db.get_setting('chapters_enabled')
        if chapters_enabled is None or chapters_enabled.lower() == 'true':
            chapters_gen = ChaptersGenerator()
            clear_fallback(episode_id, PASS_CHAPTER_GENERATION)
            chapters = chapters_gen.generate_chapters(
                segments,
                episode_description=episode_description,
                ads_removed=all_cuts,
                podcast_name=podcast_name,
                episode_title=episode_title,
                episode_id=episode_id,
            )
            if chapters and chapters.get('chapters'):
                storage.save_chapters_json(slug, episode_id, chapters)
                audio_logger.info(f"[{slug}:{episode_id}] Generated {len(chapters['chapters'])} chapters")
    except Exception as e:
        audio_logger.warning(f"[{slug}:{episode_id}] Failed to generate Podcasting 2.0 assets: {e}")


def _persist_episode_state(slug, episode_id, pass1_cut_count, verification_count,
                            first_pass_count, original_duration, new_duration,
                            processed_version, db, storage):
    """Upsert the processed episode row and update related DB state."""
    original_final = storage.get_original_path(slug, episode_id)
    original_file_rel = f"episodes/{episode_id}-original.mp3" if original_final.exists() else None
    processed_file_rel = episode_relative_path(episode_id, processed_version)
    db.upsert_episode(slug, episode_id,
        status=EpisodeStatus.PROCESSED.value,
        processed_file=processed_file_rel,
        processed_version=processed_version or 0,
        original_file=original_file_rel,
        original_duration=original_duration,
        new_duration=new_duration,
        ads_removed=pass1_cut_count + verification_count,
        ads_removed_firstpass=first_pass_count,
        ads_removed_secondpass=verification_count,
        reprocess_mode=None,
        reprocess_requested_at=None)

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


def _refresh_rss_for_slug(slug, episode_id):
    """Force-refresh the RSS feed cache for ``slug``, logging on failure."""
    from main_app.feeds import get_feed_map, refresh_rss_feed
    try:
        feed_map = get_feed_map()
        if slug in feed_map:
            refresh_rss_feed(slug, feed_map[slug]['in'], force=True)
    except Exception as cache_err:
        audio_logger.warning(f"[{slug}:{episode_id}] Failed to regenerate RSS cache: {cache_err}")


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
        if time_saved > 0:
            db.increment_total_time_saved(time_saved)
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
    if mem_info is not None:
        mem_val, mem_desc = mem_info
        audio_logger.info(f"[{slug}:{episode_id}] Post-cleanup memory: {mem_val:.1f} GB ({mem_desc})")

    return token_totals


def _record_history_and_event(slug, episode_id, episode_title, podcast_name,
                               pass1_cut_count, verification_count,
                               original_duration, new_duration,
                               processing_time, token_totals, db,
                               audio_cue_detections=0):
    """Record processing history row and fire the episode-processed webhook.

    The webhook fires whenever the episode pipeline completed, including
    the case where the podcast row is missing (we still have slug +
    episode_id + counts for the payload). The webhook is skipped only
    when `record_processing_history` raised, which signals a real DB
    write failure that would leave the History page out of sync.
    """
    ads_removed_total = pass1_cut_count + verification_count
    history_write_raised = False
    try:
        podcast_data = db.get_podcast_by_slug(slug)
        if podcast_data:
            db.record_processing_history(
                podcast_id=podcast_data['id'], podcast_slug=slug,
                podcast_title=podcast_data.get('title') or podcast_name,
                episode_id=episode_id, episode_title=episode_title,
                status='completed', processing_duration_seconds=processing_time,
                ads_detected=ads_removed_total,
                input_tokens=token_totals['input_tokens'],
                output_tokens=token_totals['output_tokens'],
                llm_cost=token_totals['cost'],
                audio_cues_detected=audio_cue_detections,
            )
        else:
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
            original_duration=original_duration, new_duration=new_duration,
            podcast_name=podcast_name,
        )
    except Exception as wh_err:
        audio_logger.warning(f"[{slug}:{episode_id}] Webhook fire failed: {wh_err}")


def _finalize_episode(slug, episode_id, episode_title, podcast_name,
                       pass1_cut_count, verification_count, first_pass_count,
                       original_duration, new_duration, start_time,
                       processed_version=0, audio_cue_detections=0):
    """Pipeline stage: Update DB, record history, refresh RSS."""
    _persist_episode_state(slug, episode_id, pass1_cut_count, verification_count,
                            first_pass_count, original_duration, new_duration,
                            processed_version, db, storage)
    _refresh_rss_for_slug(slug, episode_id)

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
    )


def _handle_processing_failure(slug, episode_id, episode_title, podcast_name,
                                episode_data, error, start_time):
    """Handle processing failure: GPU cleanup, retry logic, error recording."""
    processing_time = time.time() - start_time
    audio_logger.error(f"[{slug}:{episode_id}] Failed: {error} ({processing_time:.1f}s)")

    try:
        from transcriber import WhisperModelSingleton
        from utils.gpu import clear_gpu_memory
        clear_gpu_memory()
        WhisperModelSingleton.unload_model()
        audio_logger.info(f"[{slug}:{episode_id}] Cleaned up GPU memory after failure")
    except Exception as cleanup_err:
        audio_logger.warning(f"[{slug}:{episode_id}] Failed to clean up GPU memory: {cleanup_err}")

    status_service.fail_job()

    transient = is_transient_error(error)
    current_retry = (episode_data.get('retry_count', 0) or 0) if episode_data else 0

    # 429 retries don't burn retry_count (#238).
    rate_limited = is_rate_limit_error(error)

    if transient:
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
                audio_logger.info(f"[{slug}:{episode_id}] Transient error, will retry (attempt {new_retry_count}/{MAX_EPISODE_RETRIES})")
    else:
        new_status = EpisodeStatus.PERMANENTLY_FAILED.value
        new_retry_count = current_retry
        audio_logger.warning(f"[{slug}:{episode_id}] Permanent error, not retrying: {type(error).__name__}")

    db.upsert_episode(slug, episode_id, status=new_status,
        retry_count=new_retry_count, error_message=str(error))

    token_totals = get_episode_token_totals()
    audio_logger.info(f"[{slug}:{episode_id}] Token totals: in={token_totals['input_tokens']} out={token_totals['output_tokens']} cost=${token_totals['cost']:.6f}")

    try:
        podcast_data = db.get_podcast_by_slug(slug)
        if podcast_data:
            db.record_processing_history(
                podcast_id=podcast_data['id'], podcast_slug=slug,
                podcast_title=podcast_data.get('title') or podcast_name,
                episode_id=episode_id, episode_title=episode_title,
                status='failed', processing_duration_seconds=processing_time,
                ads_detected=0, error_message=str(error),
                input_tokens=token_totals['input_tokens'],
                output_tokens=token_totals['output_tokens'],
                llm_cost=token_totals['cost'],
            )
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


def process_episode(slug: str, episode_id: str, episode_url: str,
                   episode_title: str = "Unknown", podcast_name: str = "Unknown",
                   episode_description: str = None, episode_artwork_url: str = None,
                   episode_published_at: str = None, cancel_event: threading.Event = None):
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
    from audio_processor import AudioProcessor
    start_time = time.time()
    start_episode_token_tracking()

    episode_data = db.get_episode(slug, episode_id)
    reprocess_mode = episode_data.get('reprocess_mode') if episode_data else None
    # Only 'full' skips the learned-pattern DB. 'reprocess', 'llm' (#349) and the
    # default first run all keep patterns; any new mode keeps them unless added here.
    skip_patterns = reprocess_mode == 'full'

    if reprocess_mode:
        audio_logger.info(f"[{slug}:{episode_id}] Reprocess mode: {reprocess_mode} (skip_patterns={skip_patterns})")

    podcast_settings = db.get_podcast_by_slug(slug)
    podcast_description = podcast_settings.get('description') if podcast_settings else None

    try:
        audio_logger.info(f"[{slug}:{episode_id}] Starting: \"{episode_title}\"")
        mem_info = get_available_memory_gb()
        if mem_info is not None:
            mem_val, mem_desc = mem_info
            audio_logger.info(f"[{slug}:{episode_id}] Available memory: {mem_val:.1f} GB ({mem_desc})")
        min_cut_confidence = get_min_cut_confidence()
        audio_logger.info(f"[{slug}:{episode_id}] Confidence threshold: {min_cut_confidence:.0%}")

        status_service.start_job(slug, episode_id, episode_title, podcast_name)
        status_service.update_job_stage("downloading", 0)

        upsert_kwargs = dict(
            original_url=episode_url, title=episode_title,
            description=episode_description, artwork_url=episode_artwork_url,
            status=EpisodeStatus.PROCESSING.value
        )
        if episode_published_at:
            upsert_kwargs['published_at'] = episode_published_at
        db.upsert_episode(slug, episode_id, **upsert_kwargs)

        # Stage 1: Download and transcribe
        audio_path, segments = _download_and_transcribe(slug, episode_id, episode_url, podcast_name)
        _check_cancel(cancel_event, slug, episode_id)

        try:
            # Stage 2: Audio analysis
            audio_analysis_result = _run_audio_analysis(slug, episode_id, audio_path, segments)
            # Count audio-cue signals (issue #350) for the stats dashboard.
            audio_cue_count = (len(audio_analysis_result.get_signals_by_type('audio_cue'))
                               if audio_analysis_result else 0)
            _check_cancel(cancel_event, slug, episode_id)

            # Progress callback for detection stages
            current_pass = "pass1"
            def detection_progress_callback(stage, percent):
                status_service.update_job_stage(f"{current_pass}:{stage}", percent)

            # Build the per-episode immutable context once. Podcast tags drive
            # the matcher's community-pattern eligibility check; podcast_id is
            # the integer DB PK used by the reviewer's episode_meta.
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
                episode_description=episode_description,
                podcast_tags=podcast_tags_for_ctx,
            )

            # ffprobe duration of the original audio: the single timebase for
            # the prior gate, the prompt hint, and validation normalization.
            episode_duration = audio_processor.get_audio_duration(audio_path)
            if not episode_duration:
                episode_duration = segments[-1]['end'] if segments else 0

            # Learned positional prior (issue #360 experiment, off by default)
            positional_prior = load_positional_prior(db, slug, episode_id,
                                                     episode_duration)

            # Stage 3: First-pass detection
            first_pass_ads, first_pass_count, ad_result = _detect_ads_first_pass(
                ctx, segments, audio_path,
                skip_patterns, audio_analysis_result,
                detection_progress_callback,
                cancel_event=cancel_event,
                positional_prior_hint=format_prior_hint(positional_prior,
                                                        episode_duration),
            )
            _check_cancel(cancel_event, slug, episode_id)

            all_ads = first_pass_ads.copy()

            # Stage 4: Refine and validate
            ads_to_remove, all_ads_with_validation = _refine_and_validate(
                slug, episode_id, all_ads, segments, audio_path,
                episode_description, episode_duration, min_cut_confidence, podcast_name,
                skip_patterns=skip_patterns, positional_prior=positional_prior
            )
            _check_cancel(cancel_event, slug, episode_id)

            # No-op when enable_ad_review is off (the default).
            podcast_row = db.get_podcast_by_slug(slug)
            podcast_id = podcast_row.get('id') if podcast_row else None
            ads_to_remove, all_ads_with_validation = _run_ad_reviewer(
                slug, episode_id, podcast_id, ads_to_remove,
                all_ads_with_validation, segments, podcast_name,
                episode_title, episode_description, podcast_description,
                min_cut_confidence, pass_num=1,
                pass_model=ad_detector.get_model(),
            )
            _check_cancel(cancel_event, slug, episode_id)

            # Tail completion: final content-based end sweep after the reviewer,
            # which can pull cut ends back to the detector boundary and strand
            # the trailing CTA in the audio.
            ads_to_remove = _complete_cut_tails(
                slug, episode_id, ads_to_remove, all_ads_with_validation, segments
            )

            # Stage 5: Process audio
            status_service.update_job_stage("pass1:processing", 80)
            audio_logger.info(f"[{slug}:{episode_id}] Starting FFMPEG processing ({len(ads_to_remove)} ads to remove)")

            settings = db.get_all_settings()
            bitrate = settings.get('audio_bitrate', {}).get('value', '128k')
            local_audio_processor = AudioProcessor(bitrate=bitrate)

            # process_episode returns the cuts ffmpeg actually applied (merged,
            # <10s-filtered, end-trimmed); verification mapping and assets must
            # use that list, not the requested one.
            result = local_audio_processor.process_episode(audio_path, ads_to_remove)
            if not result:
                raise Exception(
                    f"FFMPEG processing failed for {len(ads_to_remove)} ad segments "
                    f"({episode_duration / 60:.1f}min episode) - see audio processor logs above"
                )
            processed_path, applied_cuts = result

            # A requested cut the applied list does not cover (e.g. a short
            # untrusted span the filter dropped) is still in the audio; the
            # ad editor must not claim it was removed. Reviewer adjustments
            # rebuild dicts, so match the master entry by identity or span
            # (same approach as the tail-completion sweep).
            uncovered = [ad for ad in ads_to_remove
                         if not _covered_by_cuts(ad, applied_cuts, episode_duration)]
            if uncovered:
                for ad in uncovered:
                    ad['was_cut'] = False
                    for master in all_ads_with_validation:
                        if master is ad or (master.get('start') == ad['start']
                                            and master.get('end') == ad['end']):
                            master['was_cut'] = False
                            break
                    audio_logger.info(
                        f"[{slug}:{episode_id}] Pass 1 ad {ad['start']:.1f}s-"
                        f"{ad['end']:.1f}s was filtered out of the applied "
                        f"cuts; marking as not cut"
                    )
                storage.save_combined_ads(slug, episode_id, all_ads_with_validation)

            original_duration = episode_duration
            _check_cancel(cancel_event, slug, episode_id)

            # Stage 6: Verification pass
            current_pass = "pass2"
            verification_count, v_ads_for_ui, processed_path, verification_cue_count = _run_verification_pass(
                ctx, processed_path, applied_cuts,
                skip_patterns, min_cut_confidence,
                local_audio_processor, detection_progress_callback,
                original_segments=segments,
                # LLM-only reprocess maps the saved transcript through the cuts
                # for pass 2 instead of re-transcribing (issue #349).
                reuse_transcript=(reprocess_mode == 'llm'),
            )
            # Detection-event accounting, not unique cues (issue #350): a cue
            # in a region pass 1 left in the audio is re-detected here and
            # intentionally counts twice.
            audio_cue_count += verification_cue_count
            _check_cancel(cancel_event, slug, episode_id)

            # Merge pass 2 ads into combined list for UI
            if v_ads_for_ui:
                all_ads_with_validation = list(all_ads_with_validation) + v_ads_for_ui
                all_ads_with_validation.sort(key=lambda x: x['start'])
                storage.save_combined_ads(slug, episode_id, all_ads_with_validation)

            new_duration = local_audio_processor.get_audio_duration(processed_path)

            existing_episode = db.get_episode(slug, episode_id) or {}
            # ``processed_at`` is cleared by the reprocess reset before we get
            # here, so it can't signal "been processed before". ``processed_version``
            # is not reset and ``reprocess_requested_at`` is set by the reprocess
            # endpoints - either one means we bump.
            previous_version = existing_episode.get('processed_version') or 0
            is_reprocess = (
                previous_version > 0
                or bool(existing_episode.get('reprocess_requested_at'))
            )
            new_version = previous_version + 1 if is_reprocess else 0

            final_path = storage.get_episode_path(slug, episode_id, version=new_version)
            shutil.move(processed_path, final_path)

            # Retain the pre-cut audio for the ad-editor "Review mode" playback
            # when the user hasn't opted out. Moved rather than copied so the
            # temp file in the finally-block below no longer exists.
            keep_original_raw = db.get_setting('keep_original_audio')
            keep_original = (keep_original_raw or 'true').lower() != 'false'
            if keep_original and os.path.exists(audio_path):
                original_final = storage.get_original_path(slug, episode_id)
                original_final.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(audio_path, original_final)
                audio_logger.info(
                    f"[{slug}:{episode_id}] Retained original audio at {original_final.name}"
                )

            # Stage 7: Generate assets
            all_cuts_for_assets = applied_cuts + v_ads_for_ui
            _generate_assets(slug, episode_id, segments, all_cuts_for_assets,
                              episode_description, podcast_name, episode_title)

            # Stage 8: Finalize. ads_removed accounting counts the cuts that
            # exist in the audio: an ad merged into a covering span still
            # counts; one filtered out of the applied list (<10s) does not.
            pass1_cut_count = sum(
                1 for ad in ads_to_remove
                if _covered_by_cuts(ad, applied_cuts, original_duration)
            )
            _finalize_episode(slug, episode_id, episode_title, podcast_name,
                               pass1_cut_count, verification_count, first_pass_count,
                               original_duration, new_duration, start_time,
                               processed_version=new_version,
                               audio_cue_detections=audio_cue_count)

            status_service.complete_job()
            return True

        finally:
            if os.path.exists(audio_path):
                os.unlink(audio_path)

    except ProcessingCancelled:
        raise
    except Exception as e:
        _handle_processing_failure(slug, episode_id, episode_title, podcast_name,
                                    episode_data, e, start_time)
        return False
