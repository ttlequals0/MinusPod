"""Pass 2 verification over the processed audio and cross-pass cut planning."""

import logging
import os
from dataclasses import dataclass, replace
from ad_detector.boundaries import _content_duration_in_range

from audio_processor import get_replacement_duration
from utils.markers import (EDGE_TOLERANCE, auto_confirm_releases, covering_confirm, finite_number,
                           fold_marker_pair, foldable_twin, reviewer_reject_stands)

from utils.time import (
    merge_cut_spans, overlap_seconds,
    ranges_overlap,
)

from verification_pass import _build_timestamp_map, _map_correction_to_processed, _map_to_original
from config import (
    MIN_CONTENT_BETWEEN_ADS_SECONDS,
    MAX_MERGED_DURATION,
    PASS2_AUTOAPPROVE_HOLD_REASONS, PASS2_REVIEWED_RELEASE_HOLD_REASONS,
    PASS2_AUTOAPPROVE_SNIPPET_PREFIX,
    PASS2_AUTOAPPROVE_TRIM_SLACK_S,
    CORRECTION_ORIGIN_AUTO_PASS2,
    is_pending_review, normalize_segment_category, is_keep_like,
    SEGMENT_CATEGORIES,
    DEFAULT_SEGMENT_ACTION,
    resolve_ad_detection_exclude_start_seconds,
)

from database.settings import registry_get_default
from llm_capabilities import (
    PASS_AD_DETECTION_2,
    clear_fallback,
)

from llm_client import ProviderRateLimitedError

from utils.errors import ModelLoadError

from main_app.verification_reconciliation import (
    _apply_pass2_heuristic_rolls,
    _covered_by_cuts,
    _drop_uncovered_pass2_ads,
    _exclude_kept_spans_from_verification,
    _drop_matching,
    _gate_hold_split_fragments,
    _gate_verification_ads_by_confidence,
    _run_candidate_stages,
    _split_pass2_candidates_around_spans,
    _matches_false_positive_correction,
    _unzip,
    Pass2Ledger,
    labelled_spans,
)
from main_app.pipeline import common, detect, review, cuts, keep_rules
from main_app.pipeline.shared import (db, storage, transcriber, ad_detector, audio_analyzer, pattern_service)

audio_logger = logging.getLogger('podcast.audio')


def _dedupe_pass2_markers(markers):
    """Drop repeats of the same pass-2 span so a marker cannot persist twice.

    v_ads_for_ui and v_ads_held are merged by concatenation, so a marker that
    reaches both lists would be saved twice and render as duplicate review
    cards. Identity is the rounded span plus hold reason; the first wins.
    """
    seen = set()
    unique = []
    for m in markers:
        key = (round(m.get('start', 0.0), 2), round(m.get('end', 0.0), 2),
               m.get('hold_reason'))
        if key in seen:
            audio_logger.warning(
                f"Dropping duplicate pass-2 marker {key[0]:.1f}s-{key[1]:.1f}s "
                f"(hold_reason={key[2]})"
            )
            continue
        seen.add(key)
        unique.append(m)
    return unique


def _merge_pass2_markers(all_ads, pass2_markers):
    """Fold a pass-2 marker into the pass-1 marker it repeats, since appending it
    would double-count pending reviews. Returns (to_append, folded_count)."""
    to_append = []
    folded = 0
    for marker in pass2_markers:
        twin = foldable_twin(all_ads, marker)
        if twin is None:
            to_append.append(marker)
            continue
        audio_logger.info(
            f"Pass-2 marker {marker['start']:.1f}s-{marker['end']:.1f}s repeats "
            f"pass-1 marker {twin['start']:.1f}s-{twin['end']:.1f}s "
            f"(actions {twin.get('action_applied')!r}/{marker.get('action_applied')!r}, "
            f"holds {twin.get('hold_reason')!r}/{marker.get('hold_reason')!r}); "
            f"folding into it"
        )
        fold_marker_pair(twin, marker)
        folded += 1
    return to_append, folded


def _stamp_pass2_marker_categories(markers):
    """Validate the category on pass-2 markers at save time.

    Pass-2 markers never route through the pass-1 merge seam, so an
    unrecognized value would persist and an absent one must stay absent.
    Mutates in place; returns markers for chaining.
    """
    for m in markers:
        if m.get('category') in SEGMENT_CATEGORIES:
            continue
        m.pop('category', None)
    return markers


def _partition_pass2_category_actions(processed_ads, original_ads, actions_map,
                                      differential_override=None):
    """Apply the feed's category actions to paired pass-2 candidates.

    Pass 2 has parallel processed/original coordinate lists, so its keep
    partition cannot reuse the single-list pass-1 helper. Kept candidates are
    removed from both detection lists before validation and reviewer routing;
    both coordinate copies are returned so the processed marker can protect a
    kept tail from the validator's end-of-episode extension while the original
    marker is persisted. Remaining candidates remain unstamped until confidence
    gating and review decide which ones the recut will actually render.

    The same two standing overrides as pass 1 apply: a defined pattern, or a
    span overlapping a measured differential region. The differential test
    uses the original marker, since the regions are in original-audio
    coordinates while the processed marker is not.

    Returns ``(remaining_processed, remaining_original, kept_processed,
    kept_original)``.
    """
    remaining_processed = []
    remaining_original = []
    kept_processed = []
    kept_original = []
    if len(processed_ads) != len(original_ads):
        raise ValueError(
            'Pass-2 processed/original marker lists must stay paired')
    for processed, original in zip(processed_ads, original_ads, strict=True):
        category = normalize_segment_category(
            processed.get('category', original.get('category')))
        action = actions_map.get(category, DEFAULT_SEGMENT_ACTION)
        pattern_defined = bool(
            processed.get('pattern_defined') or original.get('pattern_defined'))
        differential_cut = bool(
            is_keep_like(action) and not pattern_defined and differential_override
            and differential_override.applies_to(original))
        if is_keep_like(action) and not pattern_defined and not differential_cut:
            for marker in (processed, original):
                marker['was_cut'] = False
                marker['action_applied'] = action
                keep_rules._clear_hold_for_keep(marker)
            kept_processed.append(processed)
            kept_original.append(original)
            continue

        if is_keep_like(action):
            stamp = ('keep_overridden_by_differential' if differential_cut
                     else 'keep_overridden_by_pattern')
            processed[stamp] = True
            original[stamp] = True
        remaining_processed.append(processed)
        remaining_original.append(original)

    return (remaining_processed, remaining_original,
            kept_processed, kept_original)


def _exclude_category_kept_spans(processed_ads, original_ads,
                                  kept_processed, pass1_cuts, ledger=None,
                                  kept_original=None):
    """Subtract category-kept pass-2 spans from remaining candidates.

    Heuristic roll detection can overlap an LLM marker whose category action
    is keep. Split the candidate on the processed timeline, then remap each
    surviving fragment to original coordinates so validation, recutting, and
    the UI retain paired markers without cutting through the kept audio.
    """
    return _split_pass2_candidates_around_spans(
        processed_ads, original_ads, kept_processed, pass1_cuts,
        'category-kept audio', ledger=ledger,
        carved_labels=labelled_spans(kept_original, 'kept:category_keep'))


def _reconcile_pass2_cut_actions(processed_cuts, original_cuts, pass1_cuts, ledger=None):
    """Make actual pass-2 cuts disjoint when their render actions differ.

    A beep preserves timeline duration while remove shrinks it, so overlapping
    spans cannot both reach ffmpeg. Beep is explicit protected replacement
    intent: split remove candidates around the beep spans, then restore a
    time-ordered paired list for recutting and UI persistence.
    """
    if len(processed_cuts) != len(original_cuts):
        raise ValueError(
            'Pass-2 processed/original cut lists must stay paired')

    beep_pairs = [
        (processed, original)
        for processed, original in zip(processed_cuts, original_cuts, strict=True)
        if processed.get('action_applied') == 'beep'
    ]
    if not beep_pairs:
        return processed_cuts, original_cuts
    remove_pairs = [
        (processed, original)
        for processed, original in zip(processed_cuts, original_cuts, strict=True)
        if processed.get('action_applied') != 'beep'
    ]
    remove_processed, remove_original = _unzip(remove_pairs)
    for beep_processed, beep_original in beep_pairs:
        if any(
            beep_processed['start'] < remove_processed_ad['end']
            and beep_processed['end'] > remove_processed_ad['start']
            for remove_processed_ad in remove_processed
        ):
            # This beep is the boundary that preserves its contested audio,
            # so it must survive the renderer's short-cut confidence floor.
            beep_processed['_measured_split_fragment'] = True
            beep_original['_measured_split_fragment'] = True
    remove_processed, remove_original = _split_pass2_candidates_around_spans(
        remove_processed,
        remove_original,
        [pair[0] for pair in beep_pairs],
        pass1_cuts,
        'beep-replacement audio',
        ledger=ledger,
        carved_labels=labelled_spans([pair[1] for pair in beep_pairs], 'covered:beep'),
    )
    reconciled = [*beep_pairs, *zip(remove_processed, remove_original, strict=True)]
    reconciled.sort(key=lambda pair: pair[0]['start'])
    return _unzip(reconciled)


def _validate_verification_ads(slug, episode_id, verification_ads_processed,
                                verification_ads_original, verification_segments,
                                ads_to_remove, episode_description,
                                min_cut_confidence, db,
                                processed_duration=None,
                                max_ad_duration_override=None,
                                cue_gate_enabled=False, podcast_id=None,
                                segment_actions=None,
                                keep_barriers_processed=None, ledger=None,
                                false_positive_corrections=(), podcast_name=None):
    """Validate pass-2 ad candidates against processed-coordinate validator.

    Maps the run's user FP corrections from original to processed coordinates,
    then filters both processed and original ad lists by validator decision.
    ``processed_duration`` is the real (ffprobe) duration of the pass-1
    output; the validator clamps and extends trailing ads against it. Falls
    back to the last transcript segment's end when not provided.

    ``max_ad_duration_override`` and ``cue_gate_enabled`` are passed through
    from the per-feed resolvers (one read, done by the caller). Note:
    verification ads can never carry cue evidence (snap is pass-1 only), so on
    a cue-gated feed every pass-2 proposal will be held -- intended conservative
    behavior.

    ``keep_barriers_processed`` holds the hard protected ranges on the processed
    timeline. Validator-only copies stay in the ordered span list so a
    removable candidate cannot be merged or extended through them.

    ``ledger`` records rejected candidates, those merged into a sibling and
    those clamped away past the end of the audio.

    Returns (verification_ads_processed, verification_ads_original).
    """
    ledger = ledger or Pass2Ledger()
    # Pass-1 cut user-rejections in original time; verification
    # operates on cut audio, so map them to processed coordinates
    # before the validator can use them to auto-reject overlaps.
    fp_corrections_processed = []
    if false_positive_corrections:
        ts_map = _build_timestamp_map(ads_to_remove) if ads_to_remove else []
        beep = get_replacement_duration()
        for c in false_positive_corrections:
            proc = _map_correction_to_processed(c['start'], c['end'], ts_map, beep)
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
    # splice_veto=False: pass 2 has no audio analysis, so the splice veto
    # could never corroborate and its settings reads are skipped.
    v_validator = detect._build_validator(
        processed_duration, verification_segments,
        episode_description,
        false_positive_corrections=fp_corrections_processed,
        min_cut_confidence=min_cut_confidence,
        max_ad_duration_override=max_ad_duration_override,
        cue_gate_enabled=cue_gate_enabled,
        splice_veto=False,
        podcast_id=podcast_id,
        podcast_name=podcast_name,
    )

    # Pair each processed candidate with its original-coords twin before
    # validation: validate() sorts, merges and drops ads, so positional
    # indexing against the unvalidated original list mispairs. The shallow
    # ad.copy() inside validate() carries the reference through; a merge
    # keeps the surviving ad's twin and drops the absorbed one, so the
    # original list mirrors the processed list 1:1.
    for proc, orig in zip(verification_ads_processed, verification_ads_original, strict=True):
        proc['_orig_twin'] = orig

    validation_input = list(verification_ads_processed)
    for marker in keep_barriers_processed or []:
        barrier = marker.copy()
        barrier['_pass2_keep_barrier'] = True
        # Also prevent a merge when validation is called without an action map.
        barrier['held_for_review'] = True
        validation_input.append(barrier)

    v_validation = v_validator.validate(
        validation_input, actions_map=segment_actions)

    # validate() worked on copies; strip the key from the input dicts too so
    # no later consumer of the raw verification result can serialize it.
    for proc in verification_ads_processed:
        proc.pop('_orig_twin', None)

    kept_processed, kept_original = [], []
    seen = set()
    for ad in v_validation.ads:
        if ad.pop('_pass2_keep_barrier', False):
            continue
        # Strip the pairing key from every validator output (rejected ones
        # included) so it can never leak into serialized payloads.
        orig = ad.pop('_orig_twin', None)
        seen.add(id(orig))
        if ad.get('validation', {}).get('decision') == 'REJECT':
            if orig is not None:
                ledger.record(orig, 'rejected:validator')
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
    for proc, orig in zip(verification_ads_processed, verification_ads_original, strict=True):
        if id(orig) in seen:
            continue
        merged = any(k['start'] <= proc['start'] + EDGE_TOLERANCE
                     and proc['end'] <= k['end'] + EDGE_TOLERANCE for k in kept_processed)
        ledger.record(orig, 'covered' if merged else 'dropped:validator_clamped')
    return kept_processed, kept_original


def _pass2_confirm_span(marker):
    """The pass-2-attested sub-span when it trims the hold, else None."""
    span = marker.get('pass2_corroborated_span')
    if span and (span['start'] > marker['start'] + PASS2_AUTOAPPROVE_TRIM_SLACK_S
                 or span['end'] < marker['end'] - PASS2_AUTOAPPROVE_TRIM_SLACK_S):
        return span
    return None


def _file_corroborated_hold_approvals(slug, episode_id, markers, corrections):
    """File the confirm corrections for pass-2-corroborated holds.

    Returns the count filed; the caller owns the recut that applies them, so
    the pipeline can fold them into its own and finalize once."""
    holds = [m for m in markers or []
             if m.get('pass2_corroborated') and is_pending_review(m)
             and (m.get('hold_reason') in PASS2_AUTOAPPROVE_HOLD_REASONS
                  or (m.get('pass2_reviewed_release')
                      and m.get('hold_reason') in PASS2_REVIEWED_RELEASE_HOLD_REASONS))]
    if not holds:
        return 0
    filed = 0
    try:
        podcast = db.get_podcast_by_slug(slug)
        if not podcast:
            return 0
        # Same preconditions the recut API enforces; without the retained
        # original or segments a recut would fail and mark the episode FAILED.
        if not storage.get_original_path(slug, episode_id).exists():
            audio_logger.info(
                f"[{slug}:{episode_id}] Not auto-approving hold(s): no "
                f"retained original audio to recut from")
            return 0
        if not db.get_original_segments(slug, episode_id):
            audio_logger.info(
                f"[{slug}:{episode_id}] Not auto-approving hold(s): no "
                f"saved transcript segments to recut with")
            return 0
        # A human reject always wins: never auto-approve a span the user has
        # explicitly marked as content.
        fp_corrections, confirmed_corrections = corrections
        fp_corrections = fp_corrections or []
        reviewer_rejects = [
            r for r in markers if reviewer_reject_stands(r, confirmed_corrections)]
        approvable = []
        for m in holds:
            if any(ranges_overlap(m['start'], m['end'], fp['start'], fp['end'])
                   for fp in fp_corrections):
                audio_logger.info(
                    f"[{slug}:{episode_id}] Not auto-approving hold "
                    f"{m['start']:.1f}s-{m['end']:.1f}s: a user "
                    f"rejection covers the span")
                continue
            released = m.get('pass2_released_spans') or []
            spans = released if len(released) > 1 else [_pass2_confirm_span(m)]
            spans = [span for span in spans if not any(
                overlap_seconds((span or m)['start'], (span or m)['end'],
                                r['start'], r['end']) > 0 for r in reviewer_rejects)]
            if not spans:
                audio_logger.info(
                    f"[{slug}:{episode_id}] Not auto-approving hold "
                    f"{m['start']:.1f}s-{m['end']:.1f}s: the reviewer "
                    f"rejected overlapping audio")
                continue
            approvable.append((m, spans))
        if not approvable:
            return 0
        # Confirms on file plus those filed this run, so overlapping or duplicate holds file once.
        known = [*(confirmed_corrections or [])]
        filed_ids = set()
        for m, span in ((m, span) for m, spans in approvable for span in spans):
            # Reprocess idempotency: skip only on a confirm that would force-accept
            # this span at recut; a stale confirm that merely grazes it must not count.
            target = span or m
            reason = m.get('hold_reason')
            match = covering_confirm(
                target['start'], target['end'], known,
                where=lambda c: not c.get('auto_filed') or auto_confirm_releases(c, reason),
                prefer_confirmed_span=True)
            if match is not None:
                if id(match) in filed_ids:
                    audio_logger.info(
                        f"[{slug}:{episode_id}] Not filing a second confirm for hold "
                        f"{m['start']:.1f}s-{m['end']:.1f}s: this run already filed "
                        f"one covering it")
                continue
            # Trim the confirm to the pass-2-attested sub-span (same shape a
            # human trimmed approval files); the validator clamps the cut to
            # confirmed_span, so hold padding the detection excluded stays.
            trimmed = span is not None
            db.create_pattern_correction(
                correction_type='confirm',
                pattern_id=m.get('pattern_id'),
                episode_id=episode_id,
                original_bounds={'start': m['start'], 'end': m['end']},
                corrected_bounds=(
                    {'start': span['start'], 'end': span['end']}
                    if trimmed else None),
                text_snippet=(
                    f"{PASS2_AUTOAPPROVE_SNIPPET_PREFIX} corroborated "
                    f"{reason} hold"),
                podcast_id=podcast['id'],
                source_hold_reason=reason,
                origin=CORRECTION_ORIGIN_AUTO_PASS2,
                hold_id=m.get('hold_id'),
                addressing_mode=m.get('addressing_mode'),
            )
            filed += 1
            entry = {'start': m['start'], 'end': m['end'],
                     'auto_filed': True, 'hold_reason': reason, 'hold_id': m.get('hold_id'),
                     **({'confirmed_span': dict(span)} if trimmed else {})}
            known.append(entry)
            filed_ids.add(id(entry))
            audio_logger.info(
                f"[{slug}:{episode_id}] Auto-approving hold "
                f"{m['start']:.1f}s-{m['end']:.1f}s"
                + (f" trimmed to {span['start']:.1f}s-{span['end']:.1f}s"
                   if trimmed else "")
                + ": pass-2 independently re-detected the span as an ad")
        return len(approvable)
    except Exception as e:
        # Confirms already filed still need the recut that applies them.
        audio_logger.warning(
            f"[{slug}:{episode_id}] Auto-approve failed after filing {filed}: {e}; "
            f"the other hold(s) remain pending for manual approval")
        return filed


def _pass2_cuts_in_original(recut_applied, pass1_cuts):
    """Map the cuts the recut actually rendered back to original coordinates.

    Asset timestamp math credits one replacement beep per span, so it must
    see the rendered cut list (compute_applied_cuts output, one beep each),
    not the pre-merge UI ad list -- gap-merged pass-2 ads received a single
    beep in the audio.
    """
    if not recut_applied:
        return []
    ts_map = _build_timestamp_map(pass1_cuts)
    beep = get_replacement_duration()
    return [{
        'start': _map_to_original(c['start'], ts_map, beep),
        'end': _map_to_original(c['end'], ts_map, beep),
        'detection_stage': 'verification',
        'replacement_duration': c.get('replacement_duration'),
    } for c in recut_applied]


def _pass1_action_for_applied_cut(cut, pass1_markers):
    """Return the action shared by markers covered by one rendered pass-1 cut."""
    if 'beep' in cut:
        return 'beep' if cut['beep'] else 'remove'
    actions = {
        marker.get('action_applied')
        for marker in pass1_markers or []
        if marker.get('action_applied') in ('remove', 'beep')
        and _covered_by_cuts(marker, [cut])
    }
    return actions.pop() if len(actions) == 1 else None


def _crosspass_cut_plan(pass1_cuts, pass1_markers, pass2_original_cuts,
                        original_segments, protected_ranges, min_content):
    """Return a safe original-coordinate rerender plan when pass boundaries join."""
    if not pass1_cuts or not pass2_original_cuts:
        return None
    candidates = []
    for cut in pass1_cuts:
        action = _pass1_action_for_applied_cut(cut, pass1_markers)
        if action is None:
            return None
        candidates.append(dict(
            cut, action_applied=action, _measured_split_fragment=True,
            _crosspass_sources={'pass1'}))
    for cut in pass2_original_cuts:
        action = cut.get('action_applied')
        if action not in ('remove', 'beep'):
            return None
        candidates.append(dict(cut, _crosspass_sources={'pass2'}))
    if any(
            left['action_applied'] != right['action_applied']
            and left['start'] < right['end'] and left['end'] > right['start']
            for index, left in enumerate(candidates)
            for right in candidates[index + 1:]):
        return None

    def crosses_protected(start, end):
        return any(
            protected['start'] < end and protected['end'] > start
            for protected in protected_ranges or [])

    def locked_at(cut, edge):
        return any(
            (locked := finite_number(marker.get(f'reviewer_locked_{edge}'))) is not None
            and abs(locked - cut[edge]) <= EDGE_TOLERANCE
            and _covered_by_cuts(marker, [cut])
            for marker in pass1_markers or [])

    planned = []
    merged_crosspass = False
    for candidate in sorted(candidates, key=lambda item: item['start']):
        if not planned:
            planned.append(candidate)
            continue
        current = planned[-1]
        start, end = current['start'], max(current['end'], candidate['end'])
        same_action = current['action_applied'] == candidate['action_applied']
        combines_passes = current['_crosspass_sources'] != candidate['_crosspass_sources']
        gap = candidate['start'] - current['end']
        speech = (_content_duration_in_range(
            original_segments, current['end'], candidate['start'])
            if gap > 0 and original_segments else 0.0)
        # A gap next to a reviewer-locked pass-1 edge is audio the reviewer kept.
        eligible = gap <= 0 or (
            bool(original_segments) and min_content > 0 and speech < min_content
            and not locked_at(current, 'end') and not locked_at(candidate, 'start'))
        if (same_action and combines_passes and eligible
                and end - start <= MAX_MERGED_DURATION
                and not crosses_protected(start, end)):
            current['end'] = max(current['end'], candidate['end'])
            current['_crosspass_sources'].update(candidate['_crosspass_sources'])
            current['_measured_split_fragment'] = bool(
                current.get('_measured_split_fragment')
                or candidate.get('_measured_split_fragment'))
            merged_crosspass = True
            continue
        planned.append(candidate)
    if not merged_crosspass:
        return None
    for cut in planned:
        cut.pop('_crosspass_sources', None)
    return planned


def _rerender_crosspass_from_original(slug, episode_id, original_audio_path,
                                      processed_path, planned_cuts,
                                      local_audio_processor, cut_barriers,
                                      hard_barriers=None):
    """Render a final cross-pass union without mutating either input on failure."""
    audio_segments = [dict(cut, beep=(cut['action_applied'] == 'beep'))
                      for cut in planned_cuts]
    result = local_audio_processor.process_episode(
        original_audio_path, audio_segments, cut_barriers=cut_barriers,
        hard_barriers=hard_barriers)
    if not result:
        audio_logger.error(
            f"[{slug}:{episode_id}] Cross-pass original rerender failed; keeping pass 1 output")
        return processed_path, None, False
    rerendered_path, applied = result
    if os.path.exists(processed_path):
        try:
            os.unlink(processed_path)
        except OSError as e:
            audio_logger.warning(
                f"[{slug}:{episode_id}] Failed to remove superseded pass 1 output: {e}")
    audio_logger.info(
        f"[{slug}:{episode_id}] Cross-pass original rerender applied {len(applied)} cut(s)")
    return rerendered_path, applied, True


def _drop_uncovered_ui_ads(ui_ads, applied_cuts, total_duration, ledger):
    """Drop original-time UI markers the rendered cuts do not cover, in place."""
    for ad in [a for a in ui_ads if not _covered_by_cuts(a, applied_cuts, total_duration)]:
        ui_ads.remove(ad)
        ad['was_cut'] = False
        ledger.record(ad, 'dropped:recut_filtered')


def _drop_uncovered_crosspass_ads(slug, episode_id, processed_ads, original_ads,
                                  applied_cuts, total_duration, ledger=None):
    """Keep pass-2 UI markers only when the final original render covers them."""
    ledger = ledger or Pass2Ledger()
    for processed, original in list(zip(processed_ads, original_ads, strict=False)):
        if _covered_by_cuts(original, applied_cuts, total_duration):
            continue
        if processed in processed_ads:
            processed_ads.remove(processed)
        if original in original_ads:
            original_ads.remove(original)
        processed['was_cut'] = False
        original['was_cut'] = False
        ledger.record(original, 'dropped:recut_filtered')


def _protected_ranges_in_processed_audio(ranges, pass1_cuts):
    """Map visible original protected ranges onto the pass-1 output timeline."""
    timestamp_map = _build_timestamp_map(pass1_cuts)
    replacement_duration = get_replacement_duration()
    mapped = []
    for protected in ranges:
        span = _map_correction_to_processed(
            protected['start'], protected['end'], timestamp_map,
            replacement_duration)
        if span is not None:
            mapped.append({'start': span[0], 'end': span[1]})
    return mapped


@dataclass(frozen=True)
class Protection:
    """Pass-2 protected audio: hard barriers never move, holds are unresolved evidence."""

    hard_orig: list
    """Merged keeps, category keeps, user trims, user FP and standing reviewer rejections."""
    hard_proc: list
    """hard_orig mapped onto the pass-1 output timeline."""
    holds_orig: list
    """Temporary holds (pass-1 holds, kept conflicts, pass-2 holds), unmerged originals."""
    pass1_cuts: list
    """Pass-1 cuts the processed mapping was built from."""
    hard_labelled: list
    """Unmerged hard sources labelled keep or user_reject, for the reviewer prompt."""
    hard_sources: list
    """Unmerged hard sources labelled with the pass-2 outcome of audio they carve off."""

    def barriers_orig(self, exclude=()):
        """Hard ranges plus every hold not in exclude (matched by identity)."""
        excluded = {id(hold) for hold in exclude}
        return [*self.hard_orig,
                *(hold for hold in self.holds_orig if id(hold) not in excluded)]

    def barriers_proc(self, exclude=()):
        """barriers_orig mapped onto the pass-1 output timeline."""
        excluded = {id(hold) for hold in exclude}
        return [*self.hard_proc, *_protected_ranges_in_processed_audio(
            [hold for hold in self.holds_orig if id(hold) not in excluded],
            self.pass1_cuts)]


def build_protection(kept, category_kept, user_trims, fp_corrections, holds,
                     pass1_cuts, reviewer_rejects=()):
    """Build the pass-2 Protection from its original-coordinate sources."""
    hard = [*(kept or []), *(category_kept or []), *(user_trims or []),
            *(fp_corrections or []), *(reviewer_rejects or [])]
    hard_orig = [{'start': start, 'end': end}
                 for start, end, *_ in merge_cut_spans(hard)]
    return Protection(
        hard_orig=hard_orig,
        hard_proc=_protected_ranges_in_processed_audio(hard_orig, pass1_cuts),
        holds_orig=list(holds or []),
        pass1_cuts=pass1_cuts or [],
        hard_labelled=review._labelled_protected_spans(
            [*(kept or []), *(category_kept or [])],
            [*(user_trims or []), *(fp_corrections or [])]),
        hard_sources=[*labelled_spans(fp_corrections, 'rejected:fp_correction'),
                      *labelled_spans(kept, 'kept:pass1_keep'),
                      *labelled_spans(category_kept, 'kept:category_keep'),
                      *labelled_spans(user_trims, 'kept:user_trim'),
                      *labelled_spans(reviewer_rejects, 'rejected:pass1_reviewer')],
    )


def _render_barriers(hard, markers, rendered_cuts=(), pass1_cuts=None):
    """Render kwargs: cuts clip at hard ranges and never bridge or extend over a pending hold.

    With pass1_cuts the holds are mapped onto the pass-1 output timeline, where hard already is.
    """
    seen = {id(cut) for cut in rendered_cuts}
    holds = []
    for m in markers or []:
        if cuts._unrendered_hold(m) and id(m) not in seen:
            seen.add(id(m))
            holds.append(m)
    if pass1_cuts is not None:
        holds = _protected_ranges_in_processed_audio(holds, pass1_cuts)
    return {'cut_barriers': holds, 'hard_barriers': list(hard or [])}


def _recut_processed_audio(slug, episode_id, processed_path, v_ads_to_cut,
                            local_audio_processor,
                            cut_barriers=None, hard_barriers=None):
    """Re-cut the pass-1 processed audio with verification ads.

    Returns (processed_path, recut_applied, recut_ok) where recut_applied is
    the cut list ffmpeg actually applied. recut_ok is False if the re-cut
    failed (caller should clear v_ads_for_ui).
    """
    # 'beep' is derived from action_applied, same as the pass-1 call site,
    # so a marker stamped beep in an earlier pass renders as beep here too
    # instead of silently falling back to a full remove.
    audio_segments = [dict(ad, beep=(ad.get('action_applied') == 'beep'))
                      for ad in v_ads_to_cut]
    recut_result = local_audio_processor.process_episode(
        processed_path, audio_segments,
        cut_barriers=cut_barriers, hard_barriers=hard_barriers)
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


def _run_verification_pass(ctx, processed_path, pass1_cuts,
                            skip_patterns, min_cut_confidence,
                            local_audio_processor, progress_callback,
                            original_segments=None, reuse_transcript=False,
                            max_ad_duration_override=None, cue_gate_enabled=False,
                            pass1_held_markers=None, pass1_kept_markers=None,
                            pass1_trim_ranges=None,
                            skip_verification=False, segment_actions=None,
                            differential_override=None, run_stats=None,
                            original_audio_path=None, pass1_markers=None,
                            pass1_reviewer_rejects=None, *, false_positive_corrections):
    """Pipeline stage: Run verification (second pass) on processed audio.

    ``pass1_cuts`` must be the cuts ffmpeg actually applied (see
    compute_applied_cuts), not the requested list -- every use here is
    processed-to-original timestamp mapping.

    ``pass1_held_markers`` are pass-1 markers held for review (original
    coordinates); a pass-2 cut overlapping one is dropped so the protected
    region survives. A corroborating re-detection of a differential hold
    stamps the marker dict pass2_corroborated in place so the run files the
    approval before it finalizes.

    ``pass1_kept_markers`` are pass-1 markers with a keep-like action_applied;
    a verification finding overlapping one is dropped before it can be cut,
    held, or logged as a miss (see _exclude_kept_spans_from_verification).

    When an eligible pass-2 cut joins a pass-1 cut, ``pass1_cuts`` is mutated
    only after a successful original-audio rerender. It then holds the final
    rendered union and is the sole cut authority for downstream assets.

    ``skip_verification`` covers both opt-outs the caller resolves: skipping
    ad detection (#538), which would otherwise still pay for a second LLM
    scan, and the per-feed skip of pass 2 alone (#599).

    Returns (verification_count, v_ads_for_ui, v_cuts_for_assets, v_ads_held,
    processed_path, verification_cue_count, verification_ok,
    v_corroborated_count). ``verification_ok`` is False when the pass did not
    complete, skipped included, so callers do not report a clean scan.
    """
    if skip_verification:
        return 0, [], [], [], processed_path, 0, False, 0
    slug = ctx.slug
    episode_id = ctx.episode_id
    podcast_name = ctx.podcast_name
    episode_title = ctx.episode_title
    episode_description = ctx.episode_description
    podcast_description = ctx.podcast_description
    pass1_path = processed_path
    verification_count = 0
    v_ads_for_ui = []
    v_cuts_for_assets = []
    v_ads_held = []
    verification_cue_count = 0
    v_corroborated_count = 0
    crosspass_rerender_failed = False
    clear_fallback(episode_id, PASS_AD_DETECTION_2)
    if segment_actions is None:
        segment_actions = db.resolve_segment_actions(slug)
    category_kept = []
    kept_conflicts = []
    hard_protection = None
    ledger = Pass2Ledger()
    verification_ads_original = []

    def current_protection():
        # Hard sources are fixed after the category partition; only the holds grow.
        return replace(hard_protection, holds_orig=[
            *(pass1_held_markers or []), *kept_conflicts, *v_ads_held])

    # Read once per verification pass: standalone-miss hold/autocut floors
    # for _gate_verification_ads_by_confidence (registry defaults when unset).
    verification_miss_hold_min_confidence = db.get_setting_float(
        'verification_miss_hold_min_confidence',
        registry_get_default('verification_miss_hold_min_confidence'))
    verification_miss_autocut_min_confidence = db.get_setting_float(
        'verification_miss_autocut_min_confidence',
        registry_get_default('verification_miss_autocut_min_confidence'))

    try:
        from verification_pass import VerificationPass
        verifier = VerificationPass(
            ad_detector=ad_detector, transcriber=transcriber,
            audio_analyzer=audio_analyzer, pattern_service=pattern_service,
            db=db,
        )
        # Pass the feed PK (already on the context) so the verification pass's
        # audio analysis can select the per-feed cue template matcher instead of
        # only the spectral fallback (issue #350).
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
            feed_id=ctx.podcast_id,
            action_map=segment_actions,
        )
        opening_exclusion_seconds = resolve_ad_detection_exclude_start_seconds(
            db, ctx.podcast_id)
        verification_pairs = []
        for original, processed in zip(
                verification_result.get('ads', []),
                verification_result.get('ads_processed', []), strict=True):
            if original.get('start', 0) >= opening_exclusion_seconds:
                verification_pairs.append((original, processed))
            else:
                ledger.record(original, 'dropped:opening_exclusion')
        verification_ads_original, verification_ads_processed = _unzip(verification_pairs)
        verification_segments = verification_result.get('segments', [])
        verification_cue_count = verification_result.get('audio_cue_count', 0)
        storage.save_ads_json(slug, episode_id, verification_result, pass_number=2)

        # Recorded before the status branch so a pass that lost windows still
        # reports how much of the output audio went unexamined.
        if run_stats is not None and verification_result.get('windows_total') is not None:
            run_stats['verification_windows'] = detect._window_stats(
                verification_result['windows_total'],
                verification_result.get('windows_failed') or 0,
                verification_result.get('windows_failure_classes'))

        v_status = verification_result.get('status')
        if v_status in ('no_segments', 'transcription_failed', 'detection_failed'):
            if verification_result.get('rate_limited_hold'):
                raise ProviderRateLimitedError(
                    f"Verification failed: {verification_result.get('error')}",
                    retry_after_seconds=float(
                        verification_result.get('retry_after_seconds') or 0),
                    provider_key=verification_result.get('provider_key'),
                    credential_slot=verification_result.get('credential_slot'),
                    manual=verification_result.get('manual', False),
                    phase='verification')
            v_error = verification_result.get('error')
            detail = f": {v_error}" if v_error else ""
            audio_logger.warning(
                f"[{slug}:{episode_id}] Verification incomplete ({v_status}{detail}); "
                "not reporting a clean scan")
            ledger.fail(verification_ads_original)
            ledger.emit(slug, episode_id, run_stats)
            return (verification_count, v_ads_for_ui, v_cuts_for_assets,
                    v_ads_held, processed_path, verification_cue_count,
                    False, v_corroborated_count)

        # Heuristic roll detection on pass 2
        _apply_pass2_heuristic_rolls(
            slug, episode_id, verification_ads_processed,
            verification_ads_original, verification_segments,
            pass1_cuts, podcast_name, skip_patterns,
        )
        # Same >=50% rule the validator applies, before any carve hides the overlap.
        verification_ads_processed, verification_ads_original = _drop_matching(
            verification_ads_processed, verification_ads_original,
            lambda o: _matches_false_positive_correction(o, false_positive_corrections),
            'rejected:fp_correction', ledger)
        # A pass-1 keep is operator intent. Settle overlaps before category
        # partitioning so a same-category keep does not become a duplicate
        # pass-2 marker and only fragments outside the keep reach validation.
        (verification_ads_processed,
         verification_ads_original,
         kept_conflicts) = _exclude_kept_spans_from_verification(
            verification_ads_processed,
            verification_ads_original,
            pass1_kept_markers,
            pass1_cuts,
            ledger=ledger,
        )
        (verification_ads_processed,
         verification_ads_original,
         category_kept_processed,
         category_kept) = _partition_pass2_category_actions(
            verification_ads_processed,
            verification_ads_original,
            segment_actions,
            differential_override,
        )
        if category_kept:
            audio_logger.info(
                f"[{slug}:{episode_id}] Verification kept "
                f"{len(category_kept)} segment(s) by category action"
            )

        (verification_ads_processed,
         verification_ads_original) = _exclude_category_kept_spans(
            verification_ads_processed,
            verification_ads_original,
            category_kept_processed,
            pass1_cuts,
            ledger=ledger,
            kept_original=category_kept,
        )
        # After the category partition, so every Protection carries the category keeps.
        hard_protection = build_protection(
            kept=pass1_kept_markers, category_kept=category_kept,
            user_trims=pass1_trim_ranges,
            fp_corrections=false_positive_corrections,
            holds=[], pass1_cuts=pass1_cuts, reviewer_rejects=pass1_reviewer_rejects)
        # Split here so findings wholly inside protected audio still log a clean scan.
        (verification_ads_processed,
         verification_ads_original) = _split_pass2_candidates_around_spans(
            verification_ads_processed, verification_ads_original, hard_protection.hard_proc,
            pass1_cuts, 'protected audio', ledger=ledger,
            carved_labels=hard_protection.hard_sources)
        had_verification_candidates = bool(verification_ads_processed)
        if verification_ads_processed:
            audio_logger.info(f"[{slug}:{episode_id}] Verification found {len(verification_ads_processed)} missed ads")

            # Real duration of the pass-1 output, probed once: validation
            # clamps and extends trailing ads against it (Whisper's last
            # segment end can over- or under-run the file), and the recut
            # coverage check below needs the same pre-recut bounds.
            processed_duration = local_audio_processor.get_audio_duration(processed_path)

            def validate(processed, original, barriers):
                if not verification_segments:
                    return processed, original
                return _validate_verification_ads(
                    slug, episode_id, processed, original, verification_segments,
                    pass1_cuts, episode_description, min_cut_confidence, db,
                    processed_duration=processed_duration,
                    max_ad_duration_override=max_ad_duration_override,
                    cue_gate_enabled=cue_gate_enabled,
                    podcast_id=ctx.podcast_id,
                    segment_actions=segment_actions,
                    keep_barriers_processed=barriers,
                    ledger=ledger,
                    false_positive_corrections=false_positive_corrections,
                    podcast_name=ctx.podcast_name,
                )

            def gate(processed, original, held_markers, hold_overlaps):
                return _gate_verification_ads_by_confidence(
                    processed, original, min_cut_confidence,
                    pass1_held_markers=held_markers,
                    verification_miss_hold_min_confidence=verification_miss_hold_min_confidence,
                    verification_miss_autocut_min_confidence=verification_miss_autocut_min_confidence,
                    hard_barriers_orig=hard_protection.hard_orig,
                    segments=original_segments,
                    cue_gate_enabled=cue_gate_enabled,
                    hold_overlaps=hold_overlaps,
                    ledger=ledger,
                )

            # The FP check ran above, before any carve hid the overlap.
            hold_overlaps = []
            (verification_ads_processed, verification_ads_original,
             gated) = _run_candidate_stages(
                slug, episode_id, verification_ads_processed, verification_ads_original,
                hard_protection.hard_proc, current_protection(), validate, gate,
                holds=pass1_held_markers or [], hold_overlaps=hold_overlaps, ledger=ledger)

            if verification_ads_processed:
                (v_ads_to_cut, v_ads_for_ui, gated_held, v_corroborated_count,
                 hold_release_candidates) = gated
                v_ads_held.extend(gated_held)
                # Before the fragment gate so a covering adjust's outside pieces take that path.
                released, outside_pairs = review._review_hold_release_candidates(
                    ctx, hold_release_candidates, original_segments,
                    current_protection(), segment_actions=segment_actions,
                    min_cut_confidence=min_cut_confidence, pass1_cuts=pass1_cuts,
                    covered=[*v_ads_for_ui, *(orig for _proc, orig in hold_overlaps)],
                    ledger=ledger)
                v_corroborated_count += released
                hold_overlaps.extend(outside_pairs)
                fragments = _gate_hold_split_fragments(
                    slug, episode_id, hold_overlaps, current_protection(),
                    false_positive_corrections, validate, gate, ledger=ledger)
                v_ads_to_cut.extend(fragments.to_cut)
                v_ads_for_ui.extend(fragments.for_ui)
                v_ads_held.extend(fragments.held)
                verification_ads_processed = [*verification_ads_processed, *fragments.processed]
                verification_ads_original = [*verification_ads_original, *fragments.original]

                # Reviewer adjustments map back only when they stay in
                # surviving, unprotected original audio.
                reviewer_protection = current_protection()
                review._apply_pass2_reviewer(
                    ctx,
                    v_ads_to_cut, v_ads_for_ui, v_ads_held,
                    verification_ads_processed, verification_ads_original,
                    original_segments, min_cut_confidence,
                    cue_gate_enabled=cue_gate_enabled,
                    pass1_cuts=pass1_cuts,
                    protected_original_ranges=reviewer_protection.barriers_orig(),
                    segment_actions=segment_actions,
                    protected_spans=reviewer_protection.hard_labelled,
                    ledger=ledger,
                    hard_barriers=reviewer_protection.hard_orig,
                )
                review._hold_adjustments_crossing_final_holds(
                    v_ads_to_cut, v_ads_for_ui, v_ads_held)

                # Stamped only now: a candidate diverted to a hold or reject must not advertise a cut.
                keep_rules._partition_cut_actions([*v_ads_to_cut, *v_ads_for_ui], segment_actions)
                v_ads_to_cut, v_ads_for_ui = _reconcile_pass2_cut_actions(
                    v_ads_to_cut, v_ads_for_ui, pass1_cuts, ledger=ledger)
                protection = current_protection()
                v_ads_to_cut, v_ads_for_ui = _split_pass2_candidates_around_spans(
                    v_ads_to_cut, v_ads_for_ui, protection.hard_proc,
                    pass1_cuts, 'protected audio',
                    ledger=ledger, carved_labels=protection.hard_sources)

                if v_ads_to_cut:
                    # Probed above, before the recut deletes the pre-recut
                    # file: the coverage check needs the bounds the recut
                    # clamped to.
                    pre_recut_duration = processed_duration
                    crosspass_plan = _crosspass_cut_plan(
                        pass1_cuts, pass1_markers, v_ads_for_ui,
                        original_segments, protection.barriers_orig(),
                        common._setting_float(
                            db, 'min_content_between_ads_seconds',
                            MIN_CONTENT_BETWEEN_ADS_SECONDS, allow_zero=True))
                    if crosspass_plan and original_audio_path:
                        original_render_duration = local_audio_processor.get_audio_duration(
                            original_audio_path)
                        processed_path, recut_applied, recut_ok = _rerender_crosspass_from_original(
                            slug, episode_id, original_audio_path, processed_path,
                            crosspass_plan, local_audio_processor,
                            **_render_barriers(protection.hard_orig, protection.holds_orig))
                    else:
                        audio_logger.info(
                            f"[{slug}:{episode_id}] Re-cutting pass 1 output for "
                            f"{len(v_ads_to_cut)} verification ad(s)")
                        processed_path, recut_applied, recut_ok = _recut_processed_audio(
                            slug, episode_id, processed_path, v_ads_to_cut,
                            local_audio_processor,
                            **_render_barriers(protection.hard_proc, protection.holds_orig,
                                               pass1_cuts=protection.pass1_cuts),
                        )
                    if recut_ok:
                        # Cut authority first, so a later failure still finalizes against the new audio.
                        crosspass = bool(crosspass_plan and original_audio_path)
                        if crosspass:
                            pass1_cuts[:] = recut_applied
                            cover = (recut_applied, original_render_duration)
                        else:
                            v_cuts_for_assets = _pass2_cuts_in_original(
                                recut_applied, pass1_cuts)
                            cover = (v_cuts_for_assets, None)
                        try:
                            if crosspass:
                                _drop_uncovered_crosspass_ads(
                                    slug, episode_id, v_ads_to_cut, v_ads_for_ui,
                                    recut_applied, original_render_duration, ledger=ledger)
                            else:
                                _drop_uncovered_pass2_ads(
                                    slug, episode_id, v_ads_to_cut, v_ads_for_ui,
                                    recut_applied, verification_ads_processed,
                                    verification_ads_original, pre_recut_duration,
                                    pass1_cuts=pass1_cuts, ledger=ledger,
                                )
                        except Exception:
                            # The recut audio is in place: markers it does not cover were filtered.
                            _drop_uncovered_ui_ads(v_ads_for_ui, *cover, ledger=ledger)
                            raise
                        verification_count = len(v_ads_to_cut)
                    else:
                        for ad in v_ads_for_ui:
                            ledger.record(ad, 'dropped:recut_failed')
                        v_ads_for_ui = []
                        if crosspass_plan and original_audio_path:
                            crosspass_rerender_failed = True

        # Kept conflicts are disjoint from the category and confidence output.
        # They remain uncut and must never also enter v_ads_for_ui.
        v_ads_held.extend(kept_conflicts)
        if (not had_verification_candidates
                and not category_kept
                and not kept_conflicts):
            audio_logger.info(f"[{slug}:{episode_id}] Verification: clean")
        ledger.settle(v_ads_for_ui, v_ads_held, category_kept)
        ledger.emit(slug, episode_id, run_stats)

        verification_ok = not crosspass_rerender_failed
    except (ProviderRateLimitedError, ModelLoadError):
        # The run-level handler must see these: re-queue and redo the run, not finalize unverified.
        raise
    except Exception as e:
        audio_logger.error(f"[{slug}:{episode_id}] Verification pass failed: {e}")
        # The pass did not complete; callers must not report a clean scan.
        verification_ok = False
        # Held and kept markers still persist, so they keep their outcome.
        if processed_path != pass1_path:
            ledger.settle(v_ads_for_ui, v_ads_held, category_kept)
            ledger.fail(kept_conflicts, verification_ads_original)
        else:
            ledger.settle([], v_ads_held, category_kept)
            ledger.fail(v_ads_for_ui, kept_conflicts, verification_ads_original)
            v_ads_for_ui = []
        ledger.emit(slug, episode_id, run_stats)

    # Category keeps persist with the pass-2 markers but were never holds.
    return (verification_count, v_ads_for_ui, v_cuts_for_assets,
            [*category_kept, *v_ads_held], processed_path,
            verification_cue_count, verification_ok, v_corroborated_count)
