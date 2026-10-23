"""Recut and pass-through modes from the retained original."""

import json
import logging
import os
import shutil
from audio_processor import AudioProcessor
from cancel import (
    ProcessingCancelled, _check_cancel,
)

from utils.audio import get_audio_codec
from utils.markers import (EDGE_TOLERANCE, clip_merge_spans, parse_ad_markers,
                           reviewer_hold_stands,
                           reject_barriers, reviewer_reject_stands,
                           spans_match)

from utils.time import overlap_seconds

from config import (
    count_not_cut, is_pending_review, resolve_segment_category_actions_map,
    resolve_transcript_differential,
    resolve_max_ad_duration_override,
    resolve_ad_detection_exclude_start_seconds,
    resolve_cue_gated_approval,
)

from utils.constants import EpisodeStatus

from main_app.pipeline import common, transcribe, detect, cuts, keep_rules, verify, publish, finalize, failure
from main_app.pipeline.shared import (db, storage, audio_processor)

audio_logger = logging.getLogger('podcast.audio')


def _apply_boundary_adjustments(slug, episode_id, all_ads):
    """Override ad bounds with the user's boundary_adjustment corrections so a
    recut cuts the adjusted spans. Newest wins; unmatched corrections are skipped."""
    podcast = db.get_podcast_by_slug(slug)
    corrections = db.get_episode_corrections(podcast['id'], episode_id) if podcast else []
    adjusted = set()
    applied = 0
    for c in corrections:  # newest first (ORDER BY id DESC)
        if c.get('correction_type') != 'boundary_adjustment':
            continue
        orig = c.get('original_bounds') or {}
        new = c.get('corrected_bounds') or {}
        o_start, o_end = orig.get('start'), orig.get('end')
        n_start, n_end = new.get('start'), new.get('end')
        if None in (o_start, o_end, n_start, n_end):
            continue
        under_orig = [ad for ad in all_ads
                      if ad.get('start') is not None and ad.get('end') is not None
                      and overlap_seconds(o_start, o_end, ad['start'], ad['end']) > 0]
        candidates = [ad for ad in under_orig if id(ad) not in adjusted]
        if not candidates:
            audio_logger.info(
                f"[{slug}:{episode_id}] Recut: boundary adjustment "
                f"{o_start:.1f}s-{o_end:.1f}s has no matching ad; skipping"
            )
            continue
        # The ad the user moved is the one nearest the corrected span, so a split's
        # whole-span row cannot land on a sibling piece (#794).
        match = max(candidates, key=lambda ad: (
            overlap_seconds(n_start, n_end, ad['start'], ad['end']),
            overlap_seconds(o_start, o_end, ad['start'], ad['end'])))
        if overlap_seconds(n_start, n_end, match['start'], match['end']) <= 0 and len(under_orig) > 1:
            audio_logger.info(
                f"[{slug}:{episode_id}] Recut: boundary adjustment "
                f"{o_start:.1f}s-{o_end:.1f}s spans several markers and matches none "
                f"at {n_start:.1f}s-{n_end:.1f}s; skipping"
            )
            continue
        adjusted.add(id(match))
        if spans_match(match['start'], match['end'], n_start, n_end, EDGE_TOLERANCE):
            audio_logger.info(
                f"[{slug}:{episode_id}] Recut: boundary adjustment "
                f"{o_start:.1f}s-{o_end:.1f}s already applied at {n_start:.1f}s-{n_end:.1f}s"
            )
            continue
        match['start'], match['end'] = n_start, n_end
        # Boundary adjustments are explicit user edits. Keep measured DAI
        # evidence inside the approved range so validation cannot restore a
        # stale automatic boundary over audio the user chose to preserve.
        clip_merge_spans(match, n_start, n_end)
        applied += 1
    if applied:
        audio_logger.info(f"[{slug}:{episode_id}] Recut: applied {applied} boundary adjustment(s)")


def _split_recut_counts(total_cut, verification_count):
    """Split a recut's cut total into (first pass, verification).

    ad_markers_json already holds the merged pass-2 spans, so a recut's own
    count is the total. Persistence adds the two back together, so the
    verification share has to come out of the total, not on top of it."""
    verification_count = max(0, min(verification_count or 0, total_cut))
    return total_cut - verification_count, verification_count


def _build_recut_ad_list(slug, episode_id, segments, episode_duration,
                          episode_description, min_cut_confidence,
                          podcast_id=None, segment_actions=None, *, corrections,
                          episode_row=None, podcast_row=None):
    """Build the cut list for a recut from the stored detections plus the user's
    edits, with no re-detection. Manual adds already live in ad_markers_json;
    boundary adjustments are applied here; rejects/confirms and confidence
    gating run through the same AdValidator path a full reprocess uses.
    segment_actions is loaded when not passed.
    Returns (ads_to_remove, all_ads_with_validation, keep_ads, reviewer_rejects).
    episode_row is the caller's episode read, loaded when not passed.
    podcast_row, when given, is the caller's already-fetched podcasts row."""
    from ad_validator import Decision

    episode = (episode_row if episode_row is not None
               else db.get_episode(slug, episode_id)) or {}
    all_ads = parse_ad_markers(episode.get('ad_markers_json')) or []
    podcast_row = podcast_row or db.get_podcast_by_slug(slug)
    all_ads = detect.reconcile_description_chapter(
        all_ads, episode_description, episode_duration,
        ((podcast_row or {}).get('description_trim_pattern'),
         (podcast_row or {}).get('description_trim_mode')))
    if not all_ads:
        return [], [], [], []

    _apply_boundary_adjustments(slug, episode_id, all_ads)

    false_positive_corrections, confirmed_corrections = corrections

    # Resolve per-feed hold settings. If podcast_id was not passed, look it up.
    if podcast_id is None:
        podcast_row = podcast_row or db.get_podcast_by_slug(slug)
        podcast_id = podcast_row.get('id') if podcast_row else None
    elif podcast_row is None:
        podcast_row = db.get_podcast_by_slug(slug)
    max_ad_duration_override = resolve_max_ad_duration_override(db, podcast_id)
    cue_gate_enabled = resolve_cue_gated_approval(db, podcast_id)
    # Resolved here so the merge step below sees current category actions;
    # _recut_episode passes its own resolution back in so both agree.
    if segment_actions is None:
        segment_actions = db.resolve_segment_actions(slug)
    dd_parsed = keep_rules._load_stored_dai_differential(slug, episode_id)
    # Kept markers bypass validation as in pass 1, so a merge cannot fold kept audio into a cut.
    keep_ads, all_ads = keep_rules._partition_keep_ads(
        all_ads, segment_actions, keep_rules._make_keep_differential_override(dd_parsed))

    # Stamp saved cut state before validation re-derives it. A marker already
    # cut in the published audio must not flip to held/review when settings
    # tighten (e.g. cue gating newly enabled) -- it would resurrect the ad.
    # The stamp survives validate()'s shallow copy, so it stays attached even
    # when validation clamps/merges/extends the ad's boundaries (a span key
    # would not).
    # A standing reviewer reject outranks the saved-cut stamp; only a user override lifts it.
    # A pending reviewer hold stays pending unless a user or its own pass-2 approval releases it.
    for a in all_ads:
        if reviewer_reject_stands(a, confirmed_corrections):
            continue  # AdValidator derives and finalizes the reject
        if reviewer_hold_stands(a, confirmed_corrections):
            a['_reviewer_held'] = a['hold_reason']
        elif a.get('was_cut'):
            a['_saved_was_cut'] = True

    # Stored audio analysis re-corroborates vad_gap markers on recut; without
    # it re-validation would strip corroborated_by and re-clamp.
    raw_analysis = db.get_episode_audio_analysis(slug, episode_id)
    try:
        audio_analysis = json.loads(raw_analysis) if raw_analysis else None
    except (TypeError, ValueError):
        audio_analysis = None

    # Merge episode-level dai_differential into the audio_analysis dict so the
    # validator's _audio_corroboration_source can find it on the recut path.
    # The persisted audio_analysis_json does not include dai_differential
    # (that lives in episode_details.dai_differential_json separately).
    if dd_parsed:
        if audio_analysis is None:
            audio_analysis = {}
        audio_analysis['dai_differential'] = dd_parsed
    # Recut re-validates on the original timeline, so stored transcript gaps
    # still corroborate, but only while the toggle that produced them is on.
    stored_transcript_diff = (
        db.get_episode_upstream_transcript(slug, episode_id) or {}
        if resolve_transcript_differential(podcast_row, db) else {})

    validator = detect._build_validator(
        episode_duration, segments, episode_description,
        false_positive_corrections=false_positive_corrections,
        confirmed_corrections=confirmed_corrections,
        min_cut_confidence=min_cut_confidence,
        max_ad_duration_override=max_ad_duration_override,
        cue_gate_enabled=cue_gate_enabled,
        podcast_id=podcast_id,
        podcast_name=episode.get('podcast_title'),
        transcript_spans=(stored_transcript_diff.get('spans')
                          if stored_transcript_diff.get('status') == 'ok' else None),
    )
    validation_result = validator.validate(
        all_ads, audio_analysis=audio_analysis, actions_map=segment_actions)

    # Force previously-cut markers back to ACCEPT so the gate cuts them again,
    # overriding any hold/review outcome from re-validation. A trusted fragment
    # split from a validated cut also survives the validator's short-duration
    # rejection. Other fresh REJECTs, including later FP corrections, still win.
    reviewer_rejects = []
    for ad in validation_result.ads:
        if ad.pop('_reviewer_rejected', False):
            reviewer_rejects.append(ad)
            continue
        held_reason = ad.pop('_reviewer_held', None)
        if held_reason:
            ad.pop('_saved_was_cut', None)
            validation = ad.setdefault('validation', {})
            if validation.get('decision') != Decision.REJECT.value:
                ad['held_for_review'] = True
                ad['hold_reason'] = held_reason
                validation['decision'] = Decision.REVIEW.value
            continue
        if ad.pop('_saved_was_cut', False):
            decision = ad.get('validation', {}).get('decision')
            if decision == Decision.REJECT.value:
                error_flags = [
                    flag for flag in ad.get('validation', {}).get('flags', [])
                    if flag.startswith('ERROR:')
                ]
                trusted_duration_reject = (
                    ad.get('_measured_split_fragment')
                    and error_flags
                    and all('Very short' in flag for flag in error_flags)
                )
                if not trusted_duration_reject:
                    continue
            ad.pop('held_for_review', None)
            ad.pop('hold_reason', None)
            ad.setdefault('validation', {})['decision'] = Decision.ACCEPT.value

    ads_to_remove, _low = detect._gate_validation_by_confidence(
        slug, episode_id, validation_result.ads, min_cut_confidence,
        cue_gate_enabled=cue_gate_enabled,
    )
    # Anchor every recut boundary to a transcript segment edge when it lands
    # within snap tolerance: reviewer trims, human trims, and approved
    # differential markers all flow through this list un-snapped otherwise.
    return (ads_to_remove, sorted([*validation_result.ads, *keep_ads],
                                  key=lambda ad: ad['start']), keep_ads,
            reviewer_rejects)


def _passthrough_episode(slug, episode_id, episode_url, episode_title,
                          podcast_name, episode_description,
                          episode_artwork_url, episode_published_at,
                          start_time, episode_data, cancel_event=None, podcast=None):
    """Pass-through mode (#521): download the episode and serve it exactly
    as published -- no transcription, detection, LLM, cutting, or assets.
    MinusPod acts as an archive/relay for the feed while the served feed
    URL stays stable, so turning processing back on later needs no change
    in the podcast app."""
    audio_path = None
    run_stats = {'mode': 'passthrough'}
    try:
        audio_logger.info(f"[{slug}:{episode_id}] Pass-through: \"{episode_title}\"")
        common._publish_status('start_job', slug, episode_id, episode_title, podcast_name)
        common._publish_status('update_job_stage', slug, episode_id, "downloading", 10)

        upsert_kwargs = dict(
            original_url=episode_url, title=episode_title,
            description=episode_description, artwork_url=episode_artwork_url,
            status=EpisodeStatus.PROCESSING.value
        )
        if episode_published_at:
            upsert_kwargs['published_at'] = episode_published_at
        db.upsert_episode(slug, episode_id, **upsert_kwargs)

        # Reuse the retained original when we have it (a previously processed
        # episode being re-run under pass-through): the CDN URL may have
        # expired, and the local copy IS the untouched audio.
        original_path = storage.get_original_path(slug, episode_id)
        if original_path and os.path.exists(original_path):
            audio_path = common._copy_retained_original_to_temp(original_path)
            audio_logger.info(
                f"[{slug}:{episode_id}] Pass-through: reusing retained original")
        else:
            audio_path = transcribe._download_episode_audio(episode_url, podcast=podcast)
        _check_cancel(cancel_event, slug, episode_id)

        duration = audio_processor.get_audio_duration(audio_path)
        if not duration:
            # A CDN error page saved as audio would otherwise be served to
            # every subscriber; fail loudly like the main pipeline does.
            raise Exception("Downloaded file is not playable audio "
                            "(ffprobe found no duration)")

        # The serving stack names episode files .mp3 and declares
        # audio/mpeg, so non-MP3 enclosures (m4a/aac) are converted -- the one
        # transformation pass-through performs. Convert whenever the codec is
        # not confidently 'mp3', including when ffprobe can't determine it
        # (get_audio_codec returns None): the output is unconditionally .mp3,
        # so moving unverified bytes through would serve a mislabeled file.
        codec = get_audio_codec(audio_path)
        if codec is None:
            # A transient ffprobe miss on a genuine MP3 would otherwise force a
            # needless re-encode and break pass-through's untouched-relay
            # promise; retry once before treating the codec as unknown.
            codec = get_audio_codec(audio_path)
        if codec != 'mp3':
            audio_logger.info(
                f"[{slug}:{episode_id}] Pass-through: converting {codec or 'unknown'} to mp3")
            bitrate, compression_level = common.resolve_audio_encode_settings()
            converted = AudioProcessor(
                bitrate=bitrate, compression_level=compression_level).convert_to_mp3(audio_path)
            if not converted:
                raise Exception(f"Failed to convert {codec or 'unknown'} enclosure to mp3")
            os.unlink(audio_path)
            audio_path = converted

        # Any assets left by an earlier interrupted processing run (VTT,
        # chapters, ad markers) describe cut audio; the served RSS must not
        # reference them next to the untouched file. Transcript inputs are
        # kept so re-enabling processing skips re-transcription.
        db.clear_episode_ad_data(slug, episode_id)

        new_version = transcribe._next_processed_version(episode_data)
        # Fence first: resolving the path mkdirs the podcast tree, which would
        # recreate the directory of a feed deleted mid-run.
        common._require_publication_owner(slug, episode_id)
        final_path = storage.get_episode_path(slug, episode_id, version=new_version)
        shutil.move(audio_path, final_path)
        audio_path = None

        run_stats['downloaded_duration'] = round(duration, 2)
        finalize._finalize_episode(slug, episode_id, episode_title, podcast_name,
                           pass1_cut_count=0, verification_count=0,
                           first_pass_count=0,
                           original_duration=duration, new_duration=duration,
                           start_time=start_time,
                           processed_version=new_version,
                           audio_cue_detections=0,
                           run_stats=run_stats)
        common._publish_status('complete_job', slug, episode_id)
        return True

    except ProcessingCancelled:
        raise
    except Exception as e:
        failure._handle_processing_failure(slug, episode_id, episode_title, podcast_name,
                                    episode_data, e, start_time,
                                    run_stats=run_stats)
        return False
    finally:
        if audio_path and os.path.exists(audio_path):
            try:
                os.unlink(audio_path)
            except OSError:
                pass


def _restore_saved_markers(slug, episode_id, episode_data):
    """Put back the markers a failed run replaced, so they match the published audio."""
    raw = (episode_data or {}).get('ad_markers_json')
    if not raw:
        return
    markers = parse_ad_markers(raw)
    if markers is None:
        audio_logger.error(f"[{slug}:{episode_id}] Could not restore markers after a failed run: "
                           "unreadable ad_markers_json")
        return
    try:
        storage.save_combined_ads(slug, episode_id, markers)
    except Exception as err:
        audio_logger.error(f"[{slug}:{episode_id}] Could not restore markers after a failed run: {err}")


def _log_segment_action_map(slug, episode_id, segment_actions, podcast):
    """Log the resolved action map; a run that folds approvals into a recut logs it twice."""
    feed_overrides = resolve_segment_category_actions_map(
        (podcast or {}).get('segment_category_actions'), baseline={})
    audio_logger.info(
        f"[{slug}:{episode_id}] Segment action map: "
        + ', '.join(f"{k}={v}" for k, v in segment_actions.items())
        + f"; feed overrides: {', '.join(feed_overrides) or 'none'}")


def _recut_episode(slug, episode_id, episode_title, podcast_name,
                    episode_description, start_time, cancel_event=None,
                    run_stats=None, verification_count=0,
                    audio_cue_detections=0, owns_failure=True, progress=None,
                    podcast_row=None):
    """Recut mode (issue #422): re-cut the retained original audio from the
    current ad detections and re-time the saved transcript -- no download,
    transcription, detection, LLM, or verification pass. Preconditions
    (retained original, saved segments, ad markers) are enforced by the API.

    Segment-category actions are re-resolved against the current per-feed/
    global maps before cutting (issue #565): a marker's cut/keep fate is
    not pinned to whatever the map said the last time this episode was
    processed; _build_recut_ad_list applies the exact rule.

    run_stats, verification_count, and audio_cue_detections are forwarded to
    the history row, so a folded approval recut keeps the run's stats.
    podcast_row, when given, is the caller's already-fetched podcasts row.
    owns_failure=False leaves the failure to the caller. ``progress`` is a dict
    the recut stamps 'mutated' on before it overwrites the episode's audio, so a
    caller that means to fall back knows whether anything it would finalize is
    still intact. Markers saved before a later failure are restored from entry."""

    work_path = None
    markers_saved = False
    episode_data = db.get_episode(slug, episode_id)

    def _mark_published():
        nonlocal markers_saved
        markers_saved = False
        if progress is not None:
            progress['published'] = True

    try:
        audio_logger.info(f"[{slug}:{episode_id}] Recut: \"{episode_title}\"")
        common._publish_status('start_job', slug, episode_id, episode_title, podcast_name)
        common._publish_status('update_job_stage', slug, episode_id, "recut:loading", 10)
        db.upsert_episode(slug, episode_id, status=EpisodeStatus.PROCESSING.value)

        original_path = storage.get_original_path(slug, episode_id)
        if not original_path.exists():
            raise Exception("Retained original audio is missing; cannot recut")
        work_path = common._copy_retained_original_to_temp(original_path)

        # Empty for an episode that never had a transcript (cue-only, transcription skipped).
        segments = db.get_original_segments(slug, episode_id) or []
        if not segments and (episode_data or {}).get('has_transcript'):
            raise Exception("No saved transcript segments; cannot recut")

        bitrate, compression_level = common.resolve_audio_encode_settings()
        audio_output = db.resolve_audio_output(slug, podcast=podcast_row)
        if (db.get_setting('audio_normalize_enabled') or 'false').lower() == 'true':
            audio_output['mp3_stream_copy_enabled'] = False
        local_audio_processor = AudioProcessor(
            bitrate=bitrate, compression_level=compression_level, **audio_output)
        original_duration = (local_audio_processor.get_audio_duration(work_path)
                             or (segments[-1]['end'] if segments else 0))
        min_cut_confidence = common.get_min_cut_confidence()

        # Resolve podcast_id once from the episode row so _build_recut_ad_list's
        # per-feed override lookup uses it instead of the slug fallback.
        podcast_row = podcast_row or db.get_podcast_by_slug(slug)
        recut_podcast_id = ((episode_data or {}).get('podcast_id')
                            or (podcast_row or {}).get('id'))
        # Resolved once and reused below so a category now resolving
        # keep-like comes back out of ads_to_remove, beating an older approval.
        segment_actions = db.resolve_segment_actions(slug, podcast=podcast_row)
        _log_segment_action_map(slug, episode_id, segment_actions, podcast_row)
        corrections = detect._load_user_corrections(slug, episode_id, db)
        (ads_to_remove, all_ads_with_validation, keep_ads,
         reviewer_rejects) = _build_recut_ad_list(
            slug, episode_id, segments, original_duration,
            episode_description, min_cut_confidence,
            podcast_id=recut_podcast_id, segment_actions=segment_actions,
            corrections=corrections, episode_row=episode_data,
            podcast_row=podcast_row,
        )
        # A user confirm cuts its own interval inside a reviewer reject.
        reject_spans = reject_barriers(reviewer_rejects, corrections[1])
        ads_to_remove, trim_ranges = keep_rules._restore_confirmed_spans(
            ads_to_remove, all_ads_with_validation, corrections, original_duration,
            resolve_ad_detection_exclude_start_seconds(db, recut_podcast_id))
        reject_ids = {id(ad) for ad in reviewer_rejects}
        ads_to_remove = keep_rules._stamp_and_carve_cuts(
            slug, episode_id, [ad for ad in ads_to_remove if id(ad) not in reject_ids],
            all_ads_with_validation, segment_actions, keep_ads)

        audio_logger.info(
            f"[{slug}:{episode_id}] Recut: {len(ads_to_remove)} ad(s) to remove "
            f"from {len(all_ads_with_validation)} marker(s)"
        )
        _check_cancel(cancel_event, slug, episode_id)

        common._publish_status('update_job_stage', slug, episode_id, "recut:processing", 60)
        # 'beep' is derived from action_applied so a marker stamped beep in
        # an earlier pass still renders as beep on recut, not a full remove.
        audio_segments = [dict(ad, beep=(ad.get('action_applied') == 'beep'))
                          for ad in ads_to_remove]
        # Kept markers barrier the render so the <1s-gap merge and the
        # end-of-episode extension cannot swallow kept audio (same
        # protection the pass-2 recut threads through).
        with common._measure_run_stage('cut'):
            result = local_audio_processor.process_episode(
                work_path, audio_segments,
                **verify._render_barriers(
                    [*keep_ads, *trim_ranges, *corrections[0], *reject_spans],
                    all_ads_with_validation, ads_to_remove))
        if not result:
            raise Exception("FFMPEG processing failed during recut")
        processed_path, applied_cuts = result

        # Marker state and counts come from the render before anything is published.
        rendered_cuts = len(cuts._finalize_cut_state(
            all_ads_with_validation, ads_to_remove, applied_cuts, original_duration,
            tag=f"[{slug}:{episode_id}]"))
        held_count = sum(1 for m in all_ads_with_validation if is_pending_review(m))
        not_cut_count = count_not_cut(all_ads_with_validation)
        first_pass_count, verification_count = _split_recut_counts(
            rendered_cuts, verification_count)
        new_duration = local_audio_processor.get_audio_duration(processed_path)
        if run_stats is not None:
            if 'verification_ads_cut' in run_stats:
                # Capped here as well, or the run's stat and the history row report different pass-2 counts.
                run_stats['verification_ads_cut'] = verification_count
            # Recomputed here: the caller's copy predates the approvals this recut cut.
            cuts._record_cut_seconds(run_stats, applied_cuts, original_duration, new_duration)
            if isinstance(run_stats.get('markers'), dict):
                run_stats['markers'] = dict(
                    run_stats['markers'],
                    cut=sum(1 for m in all_ads_with_validation if m.get('was_cut')),
                    held=held_count, not_cut=not_cut_count)

        # processed_version is unaffected by the PROCESSING upsert above, so the
        # episode row read at entry still has the prior version.
        previous_version = (episode_data or {}).get('processed_version') or 0
        new_version = previous_version + 1  # recut is always a reprocess
        final_path = storage.get_episode_path(slug, episode_id, version=new_version)
        common._require_publication_owner(slug, episode_id)
        # The move can replace a caller's unpublished render at the same version path.
        if progress is not None:
            progress['mutated'] = True
        shutil.move(processed_path, final_path)

        common._publish_status('update_job_stage', slug, episode_id, "recut:assets", 85)
        # Skip chapter regeneration: its topic-boundary detection is an LLM call,
        # and recut is meant to be AI-free. The stored chapters JSON is instead
        # remapped arithmetically onto the new cut list; the user can still
        # refresh titles with the manual Regenerate Chapters action.
        #
        # Coordinate note: applied_cuts are in ORIGINAL-episode coordinates
        # (the recut re-cuts the retained original), while the stored chapters
        # JSON sits on the PREVIOUS processed timeline. previous_cuts -- the
        # prior render's applied cut list persisted as applied_cuts_json, also
        # in original coordinates -- lets the remap go previous-processed ->
        # original (inverse via previous_cuts) -> recut (adjust via
        # applied_cuts). None (episode rendered before applied_cuts_json was
        # persisted) makes the remap a safe no-op rather than a wrong guess.
        previous_cuts = storage.get_applied_cuts(slug, episode_id)
        with common._measure_run_stage('assets'):
            publish._generate_assets(slug, episode_id, segments, applied_cuts,
                              episode_description, podcast_name, episode_title,
                              regenerate_chapters=False,
                              audio_path=final_path, audio_duration=new_duration,
                              previous_cuts=previous_cuts,
                              original_duration=original_duration,
                              markers=all_ads_with_validation,
                              podcast_row=podcast_row)
        storage.save_combined_ads(slug, episode_id, all_ads_with_validation)
        markers_saved = True

        # A recut never runs detection, so it must not clear a degraded flag it
        # had no part in setting: forward the pre-recut row's flag when this
        # call has no run_stats of its own to carry it.
        finalize_run_stats = run_stats
        if finalize_run_stats is None and (episode_data or {}).get('detection_degraded'):
            finalize_run_stats = {'detection_degraded': episode_data['detection_degraded']}
        finalize._finalize_episode(slug, episode_id, episode_title, podcast_name,
                           first_pass_count, verification_count=verification_count,
                           first_pass_count=first_pass_count,
                           original_duration=original_duration,
                           new_duration=new_duration, start_time=start_time,
                           processed_version=new_version,
                           audio_cue_detections=audio_cue_detections,
                           run_stats=finalize_run_stats,
                           ads_held=held_count, ads_not_cut=not_cut_count,
                           on_persisted=_mark_published)
        common._publish_status('complete_job', slug, episode_id)
        return True

    except ProcessingCancelled:
        raise
    except Exception as e:
        if markers_saved:
            _restore_saved_markers(slug, episode_id, episode_data)
        if owns_failure:
            failure._handle_processing_failure(slug, episode_id, episode_title, podcast_name,
                                        episode_data, e, start_time)
        else:
            audio_logger.exception(f"[{slug}:{episode_id}] Recut failed: {e}")
        return False
    finally:
        if work_path and os.path.exists(work_path):
            try:
                os.unlink(work_path)
            except OSError:
                pass
