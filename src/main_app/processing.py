"""Processing pipeline: _process_episode_background, all pipeline stages."""
import json
import logging
import os
import shutil
import threading
import time

import requests
import requests.exceptions

from cancel import ProcessingCancelled, _check_cancel, _cancel_events, _cancel_events_lock
from config import MIN_CUT_CONFIDENCE, MAX_EPISODE_RETRIES
from llm_client import is_retryable_error, is_llm_api_error, start_episode_token_tracking, get_episode_token_totals
from utils.gpu import get_available_memory_gb, clear_gpu_memory
from utils.text import parse_transcript_segments
from utils.time import parse_timestamp
from webhook_service import fire_event, EVENT_EPISODE_PROCESSED, EVENT_EPISODE_FAILED

audio_logger = logging.getLogger('podcast.audio')

# Import shared warn-dedup set so routes and processing share one instance
from main_app.shared_state import permanently_failed_warned as _permanently_failed_warned


def _get_components():
    """Late import to avoid circular imports at module level."""
    from main_app import (db, storage, transcriber, ad_detector, audio_processor,
                          audio_analyzer, sponsor_service, status_service, pattern_service,
                          processing_queue)
    return (db, storage, transcriber, ad_detector, audio_processor,
            audio_analyzer, sponsor_service, status_service, pattern_service,
            processing_queue)


def get_min_cut_confidence() -> float:
    """Get the minimum confidence threshold for cutting ads from audio.

    This is configurable via the 'min_cut_confidence' setting (aggressiveness slider).
    Lower = more aggressive (removes more potential ads)
    Higher = more conservative (removes only high-confidence ads)

    Default value is MIN_CUT_CONFIDENCE from config.py
    """
    db = _get_components()[0]
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

    # Permanent content/auth errors
    permanent_patterns = [
        'invalid audio', 'unsupported format', 'corrupt',
        'authentication', 'unauthorized', 'forbidden', 'not found',
        '400 ', '401 ', '403 ', '404 ',
    ]
    if any(pattern in error_msg for pattern in permanent_patterns):
        return False

    # Default: assume transient for unknown errors (safer to retry)
    return True


def _process_episode_background(slug, episode_id, original_url, title, podcast_name, description, artwork_url, published_at=None, cancel_event=None):
    """Background thread wrapper for process_episode with queue management."""
    from processing_queue import ProcessingQueue
    db, storage, _, _, _, _, _, status_service, _, _ = _get_components()
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
            db.upsert_episode(slug, episode_id, status='pending', error_message='Canceled by user')
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
    _, _, _, _, _, _, _, status_service, _, _ = _get_components()
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
    _, storage, transcriber, _, _, _, _, status_service, _, _ = _get_components()
    segments = None
    transcript_text = storage.get_transcript(slug, episode_id)

    if transcript_text:
        audio_logger.info(f"[{slug}:{episode_id}] Found existing transcript in database")
        segments = parse_transcript_segments(transcript_text)

        if segments:
            duration_min = segments[-1]['end'] / 60
            audio_logger.info(f"[{slug}:{episode_id}] Loaded {len(segments)} segments, {duration_min:.1f} min")

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
        segments = transcriber.transcribe_chunked(audio_path, podcast_name=podcast_name)
        if not segments:
            raise Exception("Failed to transcribe audio")

        duration_min = segments[-1]['end'] / 60 if segments else 0
        audio_logger.info(f"[{slug}:{episode_id}] Transcription complete: {len(segments)} segments, {duration_min:.1f} min")

        transcript_text = transcriber.segments_to_text(segments)
        storage.save_transcript(slug, episode_id, transcript_text)
        storage.save_original_transcript(slug, episode_id, transcript_text)

    return audio_path, segments


def _run_audio_analysis(slug, episode_id, audio_path, segments):
    """Pipeline stage: Run volume + transition detection on audio."""
    db, _, _, _, _, audio_analyzer, _, status_service, _, _ = _get_components()
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


def _detect_ads_first_pass(slug, episode_id, segments, audio_path,
                            episode_description, podcast_description,
                            skip_patterns, audio_analysis_result,
                            podcast_name, episode_title,
                            progress_callback, cancel_event=None):
    """Pipeline stage: Run first-pass Claude ad detection.

    Returns (first_pass_ads, first_pass_count, ad_result).
    """
    db, storage, _, ad_detector, _, _, _, status_service, _, _ = _get_components()
    status_service.update_job_stage("pass1:detecting", 50)
    ad_result = ad_detector.process_transcript(
        segments, podcast_name, episode_title, slug, episode_id, episode_description,
        audio_path=audio_path,
        podcast_id=slug,
        skip_patterns=skip_patterns,
        podcast_description=podcast_description,
        progress_callback=progress_callback,
        audio_analysis=audio_analysis_result,
        cancel_event=cancel_event
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


def _refine_and_validate(slug, episode_id, all_ads, segments, audio_path,
                          episode_description, episode_duration, min_cut_confidence,
                          podcast_name, skip_patterns=False):
    """Pipeline stage: Refine ad boundaries, detect rolls, validate, gate by confidence.

    Returns (ads_to_remove, all_ads_with_validation).
    """
    from ad_detector import refine_ad_boundaries, snap_early_ads_to_zero, merge_same_sponsor_ads, extend_ad_boundaries_by_content
    from ad_validator import AdValidator
    db, storage, _, ad_detector, _, _, _, _, _, _ = _get_components()

    # Boundary refinement
    if all_ads and segments:
        all_ads = refine_ad_boundaries(all_ads, segments)
    if all_ads and segments:
        all_ads = extend_ad_boundaries_by_content(all_ads, segments)
    if all_ads:
        all_ads = snap_early_ads_to_zero(all_ads)
    if all_ads and segments:
        all_ads = merge_same_sponsor_ads(all_ads, segments)

    # Heuristic pre/post-roll detection
    if segments:
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

    # Validation
    if not all_ads:
        return [], []

    false_positive_corrections = db.get_false_positive_corrections(episode_id)
    if false_positive_corrections:
        audio_logger.info(f"[{slug}:{episode_id}] Loaded {len(false_positive_corrections)} false positive corrections")

    confirmed_corrections = db.get_confirmed_corrections(episode_id)
    if confirmed_corrections:
        audio_logger.info(f"[{slug}:{episode_id}] Loaded {len(confirmed_corrections)} confirmed corrections")

    validator = AdValidator(
        episode_duration, segments, episode_description,
        false_positive_corrections=false_positive_corrections,
        confirmed_corrections=confirmed_corrections,
        min_cut_confidence=min_cut_confidence
    )
    validation_result = validator.validate(all_ads)

    audio_logger.info(
        f"[{slug}:{episode_id}] Validation: "
        f"{validation_result.accepted} accepted, "
        f"{validation_result.reviewed} review, "
        f"{validation_result.rejected} rejected"
    )

    # Confidence gating: ACCEPT = cut, REJECT = keep, REVIEW = threshold check
    ads_to_remove = []
    low_confidence_count = 0
    for ad in validation_result.ads:
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


def _run_verification_pass(slug, episode_id, processed_path, ads_to_remove,
                            podcast_name, episode_title, episode_description,
                            podcast_description, skip_patterns, min_cut_confidence,
                            local_audio_processor, progress_callback):
    """Pipeline stage: Run verification (second pass) on processed audio.

    Returns (verification_count, v_ads_for_ui, processed_path).
    """
    from ad_validator import AdValidator
    db, _, _, ad_detector, _, audio_analyzer, _, _, pattern_service, _ = _get_components()
    verification_count = 0
    v_ads_for_ui = []

    try:
        from verification_pass import VerificationPass
        from main_app import transcriber, storage
        verifier = VerificationPass(
            ad_detector=ad_detector, transcriber=transcriber,
            audio_analyzer=audio_analyzer, pattern_service=pattern_service,
            db=db,
        )
        verification_result = verifier.verify(
            processed_audio_path=processed_path,
            podcast_name=podcast_name, episode_title=episode_title,
            slug=slug, episode_id=episode_id,
            pass1_cuts=ads_to_remove,
            episode_description=episode_description,
            podcast_description=podcast_description,
            skip_patterns=skip_patterns,
            progress_callback=progress_callback,
        )
        verification_ads_original = verification_result.get('ads', [])
        verification_ads_processed = verification_result.get('ads_processed', [])
        verification_segments = verification_result.get('segments', [])
        storage.save_ads_json(slug, episode_id, verification_result, pass_number=2)

        # Heuristic roll detection on pass 2
        if verification_segments:
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

        if verification_ads_processed:
            audio_logger.info(f"[{slug}:{episode_id}] Verification found {len(verification_ads_processed)} missed ads - re-cutting pass 1 output")

            # Validate verification ads
            if verification_segments:
                processed_duration = verification_segments[-1]['end']
                v_validator = AdValidator(processed_duration, verification_segments,
                                         episode_description, min_cut_confidence=min_cut_confidence)
                v_validation = v_validator.validate(verification_ads_processed)

                keep_indices = {idx for idx, ad in enumerate(v_validation.ads)
                                if ad.get('validation', {}).get('decision') != 'REJECT'}
                verification_ads_processed = [ad for idx, ad in enumerate(v_validation.ads) if idx in keep_indices]
                verification_ads_original = [ad for idx, ad in enumerate(verification_ads_original) if idx in keep_indices]

            if verification_ads_processed:
                # Confidence gate and re-cut
                v_ads_to_cut = []
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

                if v_ads_to_cut:
                    recut_path = local_audio_processor.process_episode(processed_path, v_ads_to_cut)
                    if recut_path:
                        if os.path.exists(processed_path):
                            try:
                                os.unlink(processed_path)
                            except OSError as e:
                                audio_logger.warning(f"[{slug}:{episode_id}] Failed to remove old processed file: {e}")
                        processed_path = recut_path
                        verification_count = len(v_ads_to_cut)
                        audio_logger.info(f"[{slug}:{episode_id}] Re-cut pass 1 output, removed {len(v_ads_to_cut)} additional ads")
                    else:
                        audio_logger.error(f"[{slug}:{episode_id}] Verification re-cut failed, keeping pass 1 output")
                        v_ads_for_ui = []
        else:
            audio_logger.info(f"[{slug}:{episode_id}] Verification: clean")

    except Exception as e:
        audio_logger.error(f"[{slug}:{episode_id}] Verification pass failed: {e}")

    return verification_count, v_ads_for_ui, processed_path


def _generate_assets(slug, episode_id, segments, all_cuts, episode_description,
                      podcast_name, episode_title):
    """Pipeline stage: Generate VTT transcript and chapters."""
    from transcript_generator import TranscriptGenerator
    from chapters_generator import ChaptersGenerator
    db, storage, _, _, _, _, _, _, _, _ = _get_components()
    try:
        vtt_enabled = db.get_setting('vtt_transcripts_enabled')
        transcript_gen = TranscriptGenerator()

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
            chapters = chapters_gen.generate_chapters(
                segments,
                episode_description=episode_description,
                ads_removed=all_cuts,
                podcast_name=podcast_name,
                episode_title=episode_title,
            )
            if chapters and chapters.get('chapters'):
                storage.save_chapters_json(slug, episode_id, chapters)
                audio_logger.info(f"[{slug}:{episode_id}] Generated {len(chapters['chapters'])} chapters")
    except Exception as e:
        audio_logger.warning(f"[{slug}:{episode_id}] Failed to generate Podcasting 2.0 assets: {e}")


def _finalize_episode(slug, episode_id, episode_title, podcast_name,
                       ads_to_remove, verification_count, first_pass_count,
                       original_duration, new_duration, start_time):
    """Pipeline stage: Update DB, record history, refresh RSS."""
    from main_app.feeds import get_feed_map, refresh_rss_feed
    db, storage, _, _, _, _, _, _, _, _ = _get_components()
    original_final = storage.get_original_path(slug, episode_id)
    original_file_rel = f"episodes/{episode_id}-original.mp3" if original_final.exists() else None
    db.upsert_episode(slug, episode_id,
        status='processed',
        processed_file=f"episodes/{episode_id}.mp3",
        original_file=original_file_rel,
        original_duration=original_duration,
        new_duration=new_duration,
        ads_removed=len(ads_to_remove) + verification_count,
        ads_removed_firstpass=first_pass_count,
        ads_removed_secondpass=verification_count,
        reprocess_mode=None,
        reprocess_requested_at=None)

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

    try:
        feed_map = get_feed_map()
        if slug in feed_map:
            refresh_rss_feed(slug, feed_map[slug]['in'], force=True)
    except Exception as cache_err:
        audio_logger.warning(f"[{slug}:{episode_id}] Failed to regenerate RSS cache: {cache_err}")

    processing_time = time.time() - start_time

    if original_duration and new_duration:
        time_saved = original_duration - new_duration
        if time_saved > 0:
            db.increment_total_time_saved(time_saved)
        audio_logger.info(
            f"[{slug}:{episode_id}] Complete: {original_duration/60:.1f}->{new_duration/60:.1f}min, "
            f"{len(ads_to_remove)} ads removed, {processing_time:.1f}s"
        )
    else:
        audio_logger.info(f"[{slug}:{episode_id}] Complete: {len(ads_to_remove)} ads removed, {processing_time:.1f}s")

    token_totals = get_episode_token_totals()
    audio_logger.info(f"[{slug}:{episode_id}] Token totals: in={token_totals['input_tokens']} out={token_totals['output_tokens']} cost=${token_totals['cost']:.6f}")

    # Periodic memory cleanup to prevent fragmentation over many processing cycles
    clear_gpu_memory()
    mem_info = get_available_memory_gb()
    if mem_info is not None:
        mem_val, mem_desc = mem_info
        audio_logger.info(f"[{slug}:{episode_id}] Post-cleanup memory: {mem_val:.1f} GB ({mem_desc})")

    try:
        podcast_data = db.get_podcast_by_slug(slug)
        if podcast_data:
            db.record_processing_history(
                podcast_id=podcast_data['id'], podcast_slug=slug,
                podcast_title=podcast_data.get('title') or podcast_name,
                episode_id=episode_id, episode_title=episode_title,
                status='completed', processing_duration_seconds=processing_time,
                ads_detected=len(ads_to_remove),
                input_tokens=token_totals['input_tokens'],
                output_tokens=token_totals['output_tokens'],
                llm_cost=token_totals['cost'],
            )
    except Exception as hist_err:
        audio_logger.warning(f"[{slug}:{episode_id}] Failed to record history: {hist_err}")

    try:
        fire_event(
            event=EVENT_EPISODE_PROCESSED,
            episode_id=episode_id, slug=slug, episode_title=episode_title,
            processing_time=processing_time, llm_cost=token_totals['cost'],
            ads_removed=len(ads_to_remove) + verification_count,
            original_duration=original_duration, new_duration=new_duration,
            podcast_name=podcast_name,
        )
    except Exception as wh_err:
        audio_logger.warning(f"[{slug}:{episode_id}] Webhook fire failed: {wh_err}")


def _handle_processing_failure(slug, episode_id, episode_title, podcast_name,
                                episode_data, error, start_time):
    """Handle processing failure: GPU cleanup, retry logic, error recording."""
    db, _, _, _, _, _, _, status_service, _, _ = _get_components()
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

    if transient:
        new_retry_count = current_retry + 1
        if new_retry_count >= MAX_EPISODE_RETRIES:
            new_status = 'permanently_failed'
            audio_logger.warning(f"[{slug}:{episode_id}] Max retries reached ({MAX_EPISODE_RETRIES}), marking as permanently failed")
        else:
            new_status = 'failed'
            audio_logger.info(f"[{slug}:{episode_id}] Transient error, will retry (attempt {new_retry_count}/{MAX_EPISODE_RETRIES})")
    else:
        new_status = 'permanently_failed'
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

    if new_status == 'permanently_failed':
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
    5. Audio processing (FFMPEG cut)
    6. Verification pass (second-pass detection on processed audio)
    7. Generate Podcasting 2.0 assets (VTT transcript, chapters)
    8. Finalize (update DB, record history, refresh RSS)
    """
    from audio_processor import AudioProcessor
    db, storage, _, _, audio_processor, _, _, status_service, _, _ = _get_components()
    start_time = time.time()
    start_episode_token_tracking()

    episode_data = db.get_episode(slug, episode_id)
    reprocess_mode = episode_data.get('reprocess_mode') if episode_data else None
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
            status='processing'
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
            _check_cancel(cancel_event, slug, episode_id)

            # Progress callback for detection stages
            current_pass = "pass1"
            def detection_progress_callback(stage, percent):
                status_service.update_job_stage(f"{current_pass}:{stage}", percent)

            # Stage 3: First-pass detection
            first_pass_ads, first_pass_count, ad_result = _detect_ads_first_pass(
                slug, episode_id, segments, audio_path,
                episode_description, podcast_description,
                skip_patterns, audio_analysis_result,
                podcast_name, episode_title, detection_progress_callback,
                cancel_event=cancel_event
            )
            _check_cancel(cancel_event, slug, episode_id)

            all_ads = first_pass_ads.copy()

            # Stage 4: Refine and validate
            episode_duration = audio_processor.get_audio_duration(audio_path)
            if not episode_duration:
                episode_duration = segments[-1]['end'] if segments else 0

            ads_to_remove, all_ads_with_validation = _refine_and_validate(
                slug, episode_id, all_ads, segments, audio_path,
                episode_description, episode_duration, min_cut_confidence, podcast_name,
                skip_patterns=skip_patterns
            )
            _check_cancel(cancel_event, slug, episode_id)

            # Stage 5: Process audio
            status_service.update_job_stage("pass1:processing", 80)
            audio_logger.info(f"[{slug}:{episode_id}] Starting FFMPEG processing ({len(ads_to_remove)} ads to remove)")

            settings = db.get_all_settings()
            bitrate = settings.get('audio_bitrate', {}).get('value', '128k')
            local_audio_processor = AudioProcessor(bitrate=bitrate)

            processed_path = local_audio_processor.process_episode(audio_path, ads_to_remove)
            if not processed_path:
                raise Exception(
                    f"FFMPEG processing failed for {len(ads_to_remove)} ad segments "
                    f"({episode_duration / 60:.1f}min episode) - see audio processor logs above"
                )

            original_duration = episode_duration
            _check_cancel(cancel_event, slug, episode_id)

            # Stage 6: Verification pass
            current_pass = "pass2"
            verification_count, v_ads_for_ui, processed_path = _run_verification_pass(
                slug, episode_id, processed_path, ads_to_remove,
                podcast_name, episode_title, episode_description,
                podcast_description, skip_patterns, min_cut_confidence,
                local_audio_processor, detection_progress_callback
            )
            _check_cancel(cancel_event, slug, episode_id)

            # Merge pass 2 ads into combined list for UI
            if v_ads_for_ui:
                all_ads_with_validation = list(all_ads_with_validation) + v_ads_for_ui
                all_ads_with_validation.sort(key=lambda x: x['start'])
                storage.save_combined_ads(slug, episode_id, all_ads_with_validation)

            new_duration = local_audio_processor.get_audio_duration(processed_path)

            # Move processed file to final location
            final_path = storage.get_episode_path(slug, episode_id)
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
            all_cuts_for_assets = ads_to_remove + v_ads_for_ui
            _generate_assets(slug, episode_id, segments, all_cuts_for_assets,
                              episode_description, podcast_name, episode_title)

            # Stage 8: Finalize
            _finalize_episode(slug, episode_id, episode_title, podcast_name,
                               ads_to_remove, verification_count, first_pass_count,
                               original_duration, new_duration, start_time)

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
