"""Download and transcription, including tail and hole re-transcription."""

import logging
import os
from typing import cast
from config import (
    log_download_query_enabled,
    UNREVIEWABLE_GAP_SECONDS,
    resolve_tail_retranscribe_tunables,
)

from database.podcasts import is_local_feed
from transcriber import CDN_REFUSED_PREFIX
from user_agent import download_user_agent, feed_user_agent
from utils.episode_paths import published_episode_version
from utils.errors import AudioNotReadyError

from utils.http import safe_url_for_log
from utils.language import get_feed_language_override
from utils.segments import normalize_segments, split_and_merge_segments
from utils.text import parse_transcript_segments

from main_app.pipeline import common
from main_app.pipeline.shared import (db, storage, transcriber, sponsor_service)

audio_logger = logging.getLogger('podcast.audio')


def _retranscribe_tail_no_vad(slug, episode_id, audio_path, segments,
                              language_override):
    """Re-transcribe the untranscribed episode tail without VAD (spec 1.2).

    Whisper's VAD drops quiet DAI post-rolls, so the transcript can end well
    before the audio does and no LLM window ever sees the tail. When the gap
    is inside the configured window, re-run just the tail with
    vad_filter=False and return the segments flagged novad_tail=True.
    On the API whisper backend the tail is sent as its own upload carrying
    `vad_filter=false`; servers without the switch ignore it.
    """
    if not segments:
        return []
    duration = transcriber.get_audio_duration(audio_path)
    if not duration:
        return []
    last_end = segments[-1]['end']
    gap = duration - last_end
    tunables = resolve_tail_retranscribe_tunables(db)
    if gap < tunables['min_seconds'] or gap > tunables['max_seconds']:
        return []

    audio_logger.info(
        f"[{slug}:{episode_id}] Untranscribed tail {gap:.1f}s "
        f"({last_end:.1f}s-{duration:.1f}s); re-transcribing without VAD")
    tail_segments, _reason = transcriber.transcribe_span_no_vad(
        audio_path, last_end, duration, language_override, 'novad_tail')
    return tail_segments or []


def _retranscribe_holes_no_vad(audio_path, segments, language_override, tried=()):
    """Re-decode holes the batched decoder skipped. Returns (added segments, empty holes)."""
    if len(segments) < 2:
        return [], []
    hole_min = max(common._setting_float(db, 'vad_gap_mid_min_seconds', 8.0),
                   UNREVIEWABLE_GAP_SECONDS)
    return transcriber.repair_gaps(audio_path, segments, hole_min, language_override,
                                   skip=tried)


def _repair_transcript(slug, episode_id, audio_path, segments, language_override,
                       tried=()):
    """Hole then tail no-VAD repair. Returns (segments, added segments, empty holes)."""
    with common._measure_run_stage('transcription'):
        holes, empty = _retranscribe_holes_no_vad(
            audio_path, segments, language_override, tried)
        if holes:
            segments = sorted(segments + holes, key=lambda seg: seg['start'])
        tail = _retranscribe_tail_no_vad(
            slug, episode_id, audio_path, segments, language_override)
    added = holes + tail
    _apply_transcript_corrections(slug, episode_id, added)
    return segments + tail, added, empty


def _apply_transcript_corrections(slug, episode_id, segments):
    """Apply learned sponsor-name transcript corrections to segment text in place."""
    corrected = 0
    for seg in segments:
        original = seg.get('text', '')
        fixed = sponsor_service.apply_transcript_corrections(original)
        if fixed != original:
            seg['text'] = fixed
            corrected += 1
    if corrected:
        audio_logger.info(
            f"[{slug}:{episode_id}] Applied transcript corrections to "
            f"{corrected} segment(s)")


CDN_BLOCKED_MESSAGE = 'CDN blocked the request (403) with both User-Agents'


def _download_episode_audio(episode_url, podcast=None, outcome=None):
    """Check CDN availability and download the enclosure. Returns the temp
    audio path; raises on either failure."""
    with common._measure_run_stage('download'):
        url_for_log = safe_url_for_log(
            episode_url, keep_path=True, keep_query=log_download_query_enabled())
        user_agent = download_user_agent(podcast)
        if outcome is not None:
            outcome['download_user_agent'] = user_agent
        available, cdn_error = transcriber.check_audio_availability(episode_url, user_agent=user_agent)
        if not available and cdn_error.startswith(CDN_REFUSED_PREFIX):
            # A 403 may be a permanent User-Agent refusal or a transient block.
            # Probe the alternate configured agent to distinguish them.
            alternate = feed_user_agent(podcast)
            accepted, _ = transcriber.check_audio_availability(episode_url, user_agent=alternate)
            if accepted:
                setting_location = ("Feed settings > Advanced"
                                    if (podcast or {}).get("download_user_agent_override")
                                    else "Settings > Outbound Requests")
                audio_logger.warning(
                    f"Host at {url_for_log} refuses the download User-Agent "
                    f"'{user_agent}' but accepts the feed User-Agent "
                    f"'{alternate}'. Downloading with the feed string. Update the "
                    f"download User-Agent in {setting_location}.")
                available, cdn_error, user_agent = True, None, alternate
                if outcome is not None:
                    outcome['fallback_user_agent'] = alternate
            else:
                cdn_error = CDN_BLOCKED_MESSAGE
        if not available:
            # Without the host the failure is unattributable: the download log
            # line below never runs on this path.
            audio_logger.warning(f"Audio unavailable at {url_for_log}: {cdn_error}")
            raise AudioNotReadyError(cdn_error)
        audio_path = transcriber.download_audio(episode_url, user_agent=user_agent)
        if not audio_path:
            raise AudioNotReadyError("Failed to download audio")
        return audio_path


def _resolve_audio_path(slug, episode_id, episode_url, podcast, outcome):
    """Working audio file: a copy of the retained original, else a fresh download.

    The copy keeps the retain-move and cleanup-unlink later in process_episode
    off the retained original. Local originals are never re-downloadable
    (original_url is a local:// sentinel), so a missing one is a hard failure.
    """
    original_path = storage.get_original_path(slug, episode_id)
    if original_path and os.path.exists(original_path):
        audio_logger.info(f"[{slug}:{episode_id}] Reusing retained original audio (skipped download)")
        return common._copy_retained_original_to_temp(original_path)
    if is_local_feed(podcast):
        raise Exception("original audio missing")
    common._publish_status('update_job_stage', slug, episode_id, "downloading", 5)
    audio_logger.info(f"[{slug}:{episode_id}] Downloading audio")
    return _download_episode_audio(episode_url, podcast=podcast, outcome=outcome)


def _next_processed_version(episode_data):
    """Choose a fresh version for published or explicitly requested reruns."""
    previous_version = (episode_data or {}).get('processed_version') or 0
    is_reprocess = (previous_version > 0
                    or bool((episode_data or {}).get('reprocess_requested_at'))
                    or published_episode_version(episode_data) is not None)
    return previous_version + 1 if is_reprocess else 0


def _forced_transcription_already_done(slug, episode_id, requested_at) -> bool:
    """True when an earlier attempt of this same reprocess saved a transcript.

    The forced clear deletes and recreates the details row, so a row newer
    than the reprocess request holds this request's transcript, not the stale
    pre-request one, and a retry after a transient failure may reuse it.
    """
    if not requested_at:
        return False
    # Reuse only when the retry will read the same audio file the transcript
    # came from. Without a retained original it re-downloads, and on a DAI
    # feed the fresh file's ad inlays shift, so reused timestamps cut content.
    if not storage.get_original_path(slug, episode_id).exists():
        return False
    transcribed_at = db.get_transcribed_details_created_at(slug, episode_id)
    if transcribed_at and transcribed_at > requested_at:
        audio_logger.info(
            f"[{slug}:{episode_id}] Reusing transcript from an earlier "
            f"attempt of this reprocess (saved {transcribed_at})")
        return True
    return False


def _download_and_transcribe(slug, episode_id, episode_url,
                              skip_transcription=False, podcast=None,
                              force_transcription=False, outcome=None):
    """Pipeline stage: Download audio and get/create transcript segments.

    ``skip_transcription``: cue_only preset opt-out; goes straight to
    audio acquisition and returns (audio_path, []) without transcribing.

    ``force_transcription``: full/reprocess reruns (#692). Transcribes fresh
    instead of reusing the saved transcript; the stale details row (old
    transcript plus detection data) is wiped only once the fresh transcript
    exists in memory, so a crash cannot lose both.

    ``podcast``: the caller's already-fetched podcast row (avoids a
    redundant db.get_podcast_by_slug here), matching the pattern used by
    _run_differential_fetch. None is treated as non-local.

    ``outcome``: optional dict; gets ``segments_reused`` True only when saved segments were used.

    Returns (audio_path, segments) or raises on failure.
    """
    outcome = {} if outcome is None else outcome
    outcome['segments_reused'] = False
    if skip_transcription:
        original_path = storage.get_original_path(slug, episode_id)
        if original_path and os.path.exists(original_path):
            audio_path = common._copy_retained_original_to_temp(original_path)
            audio_logger.info(f"[{slug}:{episode_id}] Reusing retained original audio (skipped download)")
        elif is_local_feed(podcast):
            raise Exception("original audio missing")
        else:
            audio_path = _download_episode_audio(episode_url, podcast=podcast, outcome=outcome)
        audio_logger.info(f"[{slug}:{episode_id}] Transcription skipped (per-feed setting)")
        if force_transcription:
            # The rerun will not write a transcript, so the stale row must
            # go now to keep the pre-existing behavior for this combination.
            db.clear_episode_details(slug, episode_id)
        return audio_path, []

    segments: list[dict] | None = None
    transcript_text = None if force_transcription else storage.get_transcript(slug, episode_id)

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
        outcome['segments_reused'] = True
        duration_min = segments[-1]['end'] / 60
        audio_logger.info(
            f"[{slug}:{episode_id}] Found existing transcript: "
            f"{len(segments)} segments, {duration_min:.1f} min")

        audio_path = _resolve_audio_path(slug, episode_id, episode_url, podcast, outcome)
        language_override = get_feed_language_override(db, slug)
        segments, added, empty_holes = _repair_transcript(
            slug, episode_id, audio_path, segments, language_override,
            tried=db.get_repair_holes(slug, episode_id))
        db.add_repair_holes(slug, episode_id, empty_holes)
        if added:
            # A repair only adds segments to the same transcription, so it
            # replaces the write-once originals and is not redone next time.
            repaired_text = transcriber.segments_to_text(segments)
            storage.save_transcript(slug, episode_id, repaired_text)
            db.save_repaired_original_transcript(
                slug, episode_id, repaired_text, segments)
            audio_logger.info(
                f"[{slug}:{episode_id}] Stored repaired original transcript "
                f"(+{len(added)} segments)")
    else:
        audio_path = _resolve_audio_path(slug, episode_id, episode_url, podcast, outcome)

        common._publish_status('update_job_stage', slug, episode_id, "pass1:transcribing", 20)
        audio_logger.info(f"[{slug}:{episode_id}] Starting transcription")
        language_override = get_feed_language_override(db, slug)
        with common._measure_run_stage('transcription'):
            segments = transcriber.transcribe_chunked(
                audio_path, language_override=language_override,
            )
        if not segments:
            raise Exception("Failed to transcribe audio")

        # Normalize, split, and merge segments before performing any repairs
        # since most repairs create discrepancies between the segment text and
        # the word-level segments.
        invalid_segments = []
        valid_segments = normalize_segments(segments, invalid_segments=invalid_segments)
        if invalid_segments:
            audio_logger.warning(f"[{slug}:{episode_id}] Dropped {len(invalid_segments)} invalid segments: {invalid_segments}")
        segments = cast(list[dict], split_and_merge_segments(valid_segments, reporter=audio_logger.info))

        _apply_transcript_corrections(slug, episode_id, segments)

        duration_min = segments[-1]['end'] / 60
        audio_logger.info(f"[{slug}:{episode_id}] Transcription complete: {len(segments)} segments, {duration_min:.1f} min")

        segments, _added, empty_holes = _repair_transcript(
            slug, episode_id, audio_path, segments, language_override)

        transcript_text = transcriber.segments_to_text(segments)
        if force_transcription:
            # Wipe the stale row (old transcript, markers, write-once
            # originals) only now that the fresh transcript exists in
            # memory (#692); the saves below recreate it.
            db.clear_episode_details(slug, episode_id)
        storage.save_transcript(slug, episode_id, transcript_text)
        storage.save_original_transcript(slug, episode_id, transcript_text)
        storage.save_original_segments(slug, episode_id, segments)
        db.add_repair_holes(slug, episode_id, empty_holes, replace=True)

    return audio_path, segments
