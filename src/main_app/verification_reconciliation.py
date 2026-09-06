"""Pass-2 verification reconciliation: validating, gating, and recutting
pass-2 ad candidates against pass-1 output."""
import logging

from audio_processor import get_replacement_duration
from config import (
    CORRECTION_MATCH_MIN_COVERAGE,
    HOLD_REASON_VERIFICATION_KEPT_CONFLICT,
    HOLD_REASON_VERIFICATION_MISS,
    PASS2_AUTOAPPROVE_HOLD_REASONS,
    PASS2_AUTOAPPROVE_PROPOSED_IOU,
    PASS2_DIFFERENTIAL_AUTOAPPROVE_MIN_AD_INSIDE,
    PASS2_DIFFERENTIAL_AUTOAPPROVE_MIN_HOLD_COVERAGE,
)
from database.settings import registry_get_default
from utils.time import (
    adjust_timestamp, overlap_ratio, overlap_seconds, ranges_overlap,
)
from verification_pass import _build_timestamp_map, _map_to_original

audio_logger = logging.getLogger('podcast.audio')


def _apply_pass2_heuristic_rolls(slug, episode_id, verification_ads_processed,
                                  verification_ads_original, verification_segments,
                                  ads_to_remove, podcast_name, skip_patterns):
    """Append pass-2 heuristic pre/post-rolls in both processed and original coords."""
    if not verification_segments:
        return
    from roll_detector import detect_preroll, detect_postroll
    processed_dur = verification_segments[-1]['end'] if verification_segments else 0
    ts_map = _build_timestamp_map(ads_to_remove) if ads_to_remove else None
    beep = get_replacement_duration()

    # Sequential by design: the post-roll detector must see any pre-roll
    # already appended to verification_ads_processed.
    for label in ('pre-roll', 'post-roll'):
        if label == 'pre-roll':
            roll = detect_preroll(verification_segments, verification_ads_processed,
                                  podcast_name=podcast_name, skip_patterns=skip_patterns)
        else:
            roll = detect_postroll(verification_segments, verification_ads_processed,
                                   episode_duration=processed_dur, skip_patterns=skip_patterns)
        if not roll:
            continue
        verification_ads_processed.append(roll)
        mapped = roll.copy()
        if ts_map:
            mapped['start'] = _map_to_original(roll['start'], ts_map, beep)
            mapped['end'] = _map_to_original(roll['end'], ts_map, beep)
        verification_ads_original.append(mapped)
        shown_start = 0.0 if label == 'pre-roll' else roll['start']
        audio_logger.info(f"[{slug}:{episode_id}] Pass 2 heuristic {label}: {shown_start:.1f}s-{roll['end']:.1f}s")


def _proposed_span_agrees(hold, orig_ad):
    """True when the hold carries the reviewer's own proposed ad sub-span
    and the pass-2 ad names essentially the same audio (IoU of the two
    sub-spans at or above PASS2_AUTOAPPROVE_PROPOSED_IOU). Two independent
    signals agreeing on a sub-span corroborates regardless of how much of
    the (padded) hold either one covers."""
    p_start = hold.get('reviewer_proposed_start')
    p_end = hold.get('reviewer_proposed_end')
    if p_start is None or p_end is None or p_end <= p_start:
        return False
    # The proposed span must actually reach into the hold: a reviewer that
    # relocated the ad entirely outside the held span is not corroborating
    # the hold, and intersecting a disjoint span would invert the stamped
    # bounds and file a degenerate confirm.
    if overlap_seconds(p_start, p_end, hold['start'], hold['end']) <= 0:
        return False
    inter = overlap_seconds(orig_ad['start'], orig_ad['end'], p_start, p_end)
    union = max(orig_ad['end'], p_end) - min(orig_ad['start'], p_start)
    return union > 0 and inter / union >= PASS2_AUTOAPPROVE_PROPOSED_IOU


def _corroborates_hold(overlapping, orig_ad, confidence,
                        min_cut_confidence):
    """True when a confident non-held pass-2 ad is the independent
    corroboration a held span was waiting for: it overlaps exactly that one
    pending marker, and either covers nearly all of it while sitting mostly
    inside it, or agrees with the reviewer's own proposed sub-span (see
    _proposed_span_agrees). The ad is still dropped (pending audio is never
    cut mid-pipeline); the hold is stamped for auto-approval instead."""
    if (confidence < min_cut_confidence
            or len(overlapping) != 1
            or overlapping[0].get('hold_reason')
            not in PASS2_AUTOAPPROVE_HOLD_REASONS):
        return False
    hold = overlapping[0]
    ad_inside = overlap_ratio(hold['start'], hold['end'],
                              orig_ad['start'], orig_ad['end'])
    hold_covered = overlap_ratio(orig_ad['start'], orig_ad['end'],
                                 hold['start'], hold['end'])
    if (ad_inside >= PASS2_DIFFERENTIAL_AUTOAPPROVE_MIN_AD_INSIDE
            and hold_covered >= PASS2_DIFFERENTIAL_AUTOAPPROVE_MIN_HOLD_COVERAGE):
        return True
    return _proposed_span_agrees(hold, orig_ad)


def _corroborated_span(hold, orig_ad):
    """The sub-span the corroboration attests, clamped inside the hold: the
    reviewer's proposed span when that is what agreed with the pass-2 ad
    (_proposed_span_agrees), else the hold itself. Either way intersected
    with the pass-2 ad's own bounds, since the ad never attests audio
    outside itself."""
    lo, hi = hold['start'], hold['end']
    if _proposed_span_agrees(hold, orig_ad):
        lo = max(lo, hold['reviewer_proposed_start'])
        hi = min(hi, hold['reviewer_proposed_end'])
    return {
        'start': max(lo, orig_ad['start']),
        'end': min(hi, orig_ad['end']),
    }


def _pass2_keep_barriers_processed(pass1_kept_markers, pass1_cuts,
                                    category_kept_processed=None):
    """Collect every keep marker on the pass-1 processed timeline."""
    replacement_duration = get_replacement_duration()
    pass1_processed = [
        dict(
            marker,
            start=adjust_timestamp(
                marker['start'], pass1_cuts, replacement_duration),
            end=adjust_timestamp(
                marker['end'], pass1_cuts, replacement_duration),
        )
        for marker in pass1_kept_markers or []
    ]
    return [*pass1_processed, *(category_kept_processed or [])]


def _matches_false_positive_correction(orig_ad, false_positive_corrections):
    """Same >=50%-of-segment rule as AdValidator._overlaps_false_positive."""
    duration = orig_ad['end'] - orig_ad['start']
    if duration < 0.001:
        return False
    return any(
        overlap_ratio(corr['start'], corr['end'],
                      orig_ad['start'], orig_ad['end'])
        >= CORRECTION_MATCH_MIN_COVERAGE
        for corr in false_positive_corrections or [])


def _exclude_kept_spans_from_verification(verification_ads_processed,
                                           verification_ads_original,
                                           pass1_kept_markers, pass1_cuts,
                                           false_positive_corrections=None):
    """Divert pass-2 findings that overlap a kept pass-1 span into review
    rather than cutting them.

    A kept span reflects the operator's segment-action map, so pass 2 must
    not cut through it. Silently discarding the finding hid a real
    disagreement, so each one is stamped held_for_review and returned
    separately for the pending-review queue.

    Runs before _gate_verification_ads_by_confidence so none of its
    autocut/hold/log branches ever see a finding inside a kept span.
    pass1_kept_markers (original coordinates) are mapped onto the processed
    timeline via adjust_timestamp with pass1_cuts, matching the coordinate
    space of verification_ads_processed.

    Returns (surviving_processed, surviving_original, conflicts) with an
    empty conflicts list when there are no kept markers.
    """
    if not pass1_kept_markers:
        return verification_ads_processed, verification_ads_original, []
    kept_spans_processed = [
        (marker['start'], marker['end'])
        for marker in _pass2_keep_barriers_processed(
            pass1_kept_markers, pass1_cuts)
    ]
    surviving_processed = []
    surviving_original = []
    conflicts = []
    for ad, orig_ad in zip(verification_ads_processed, verification_ads_original, strict=True):
        overlap = next(
            (span for span in kept_spans_processed
             if ranges_overlap(ad['start'], ad['end'], span[0], span[1])),
            None)
        if overlap is not None:
            # This runs before validation, so screen against the user's
            # false-positive rejections here: a span the user already ruled
            # out must not resurface in the review queue as a kept-conflict.
            if _matches_false_positive_correction(
                    orig_ad, false_positive_corrections):
                audio_logger.info(
                    f"Pass-2 finding {orig_ad['start']:.1f}s-"
                    f"{orig_ad['end']:.1f}s overlaps a kept span but matches "
                    f"a user false-positive rejection; dropping it"
                )
                continue
            audio_logger.info(
                f"Pass-2 finding {ad['start']:.1f}s-{ad['end']:.1f}s "
                f"(processed) contradicts kept span {overlap[0]:.1f}s-"
                f"{overlap[1]:.1f}s: holding for review instead of cutting"
            )
            orig_ad['held_for_review'] = True
            orig_ad['was_cut'] = False
            orig_ad['hold_reason'] = HOLD_REASON_VERIFICATION_KEPT_CONFLICT
            conflicts.append(orig_ad)
            continue
        surviving_processed.append(ad)
        surviving_original.append(orig_ad)
    return surviving_processed, surviving_original, conflicts


def _gate_verification_ads_by_confidence(verification_ads_processed,
                                          verification_ads_original,
                                          min_cut_confidence,
                                          pass1_held_markers=None,
                                          verification_miss_hold_min_confidence=None,
                                          verification_miss_autocut_min_confidence=None):
    """Confidence gate pass-2 ads.

    Returns (v_ads_to_cut, v_ads_for_ui, v_ads_held, corroborated_count).

    Held ads (held_for_review=True) divert to v_ads_held as original-coord
    twins with was_cut=False. They must NOT enter v_ads_for_ui: that list
    feeds all_cuts_for_assets (transcript/chapter mapping) and the pass-2
    reviewer's accepted pool. Contamination would corrupt both.

    ``pass1_held_markers`` are pass-1 marker dicts held for review (original
    coordinates). A pass-2 cut overlapping one is ALWAYS dropped -- cutting
    would destroy audio the hold protects, and a second held marker would
    double-count pending_review_count. When the dropped ad corroborates a
    releasable hold (see _corroborates_hold), the hold's
    marker dict is stamped pass2_corroborated in place so
    _file_corroborated_hold_approvals can approve it through the standard
    human-approval path, before the run finalizes, and the run's own recut
    cuts it. corroborated_count is the number of newly stamped markers (they
    need a re-save).

    Note: verification ads can never carry cue evidence (snap is pass-1 only),
    so on a cue-gated feed every pass-2 proposal is held -- intended
    conservative behavior, documented here.

    A standalone miss (below min_cut_confidence, overlapping no pass-1
    marker) used to be silently discarded. It now either auto-cuts (when
    ``verification_miss_autocut_min_confidence`` is enabled, i.e. > 0, and
    the ad clears it -- routed into v_ads_to_cut exactly like a gated cut
    ad) or, failing that, is held for review when it clears
    ``verification_miss_hold_min_confidence`` (HOLD_REASON_VERIFICATION_MISS,
    diverted to v_ads_held same as any other held ad). Below both floors it
    is still discarded, now with a log line naming what was dropped and why.
    Missing kwargs fall back to the settings-registry defaults so direct
    callers (tests, ad-hoc gate invocations) get the same behavior as an
    unconfigured install.
    """
    if verification_miss_hold_min_confidence is None:
        verification_miss_hold_min_confidence = registry_get_default(
            'verification_miss_hold_min_confidence')
    if verification_miss_autocut_min_confidence is None:
        verification_miss_autocut_min_confidence = registry_get_default(
            'verification_miss_autocut_min_confidence')
    pass1_held_markers = pass1_held_markers or []
    v_ads_to_cut = []
    v_ads_for_ui = []
    v_ads_held = []
    corroborated_count = 0
    for ad, orig_ad in zip(verification_ads_processed, verification_ads_original, strict=True):
        # Held ads divert to the held list; never cut, never enter the UI/reviewer pool.
        # Checked before the pass-1 overlap below so a held ad can never
        # corroborate a hold into auto-approval; one that repeats a pass-1
        # span folds into it at the marker merge seam instead.
        if ad.get('held_for_review'):
            orig_ad['was_cut'] = False
            orig_ad['held_for_review'] = True
            orig_ad['hold_reason'] = ad.get('hold_reason')
            v_ads_held.append(orig_ad)
            continue
        confidence = ad.get('validation', {}).get('adjusted_confidence', ad.get('confidence', 1.0))
        overlapping = [m for m in pass1_held_markers
                       if ranges_overlap(orig_ad['start'], orig_ad['end'],
                                         m['start'], m['end'])]
        if overlapping:
            # A pass-2 cut overlapping a pass-1 held span would destroy the
            # audio the hold protects; drop it (never cut). The pass-1 held
            # marker already represents the region, so no second held marker
            # either -- that would double-count pending_review_count.
            if _corroborates_hold(overlapping, orig_ad,
                                   confidence, min_cut_confidence):
                hold = overlapping[0]
                if not hold.get('pass2_corroborated'):
                    hold['pass2_corroborated'] = True
                    # The corroborated sub-span: what pass 2 actually attested
                    # as ad, clamped inside the hold (or, when the reviewer's
                    # own proposed span is what agreed, clamped inside that
                    # instead). The auto-approve confirm is trimmed to this,
                    # so hold padding the detection excluded is never cut on
                    # the detection's authority.
                    hold['pass2_corroborated_span'] = _corroborated_span(
                        hold, orig_ad)
                    flags = hold.setdefault('validation', {}).setdefault('flags', [])
                    flags.append('INFO: Pass-2 independently re-detected this span as an ad')
                    corroborated_count += 1
                audio_logger.info(
                    f"Pass-2 ad {orig_ad['start']:.1f}s-{orig_ad['end']:.1f}s "
                    f"corroborates {hold.get('hold_reason')} hold "
                    f"{hold['start']:.1f}s-{hold['end']:.1f}s: stamping it "
                    f"for auto-approval")
            else:
                audio_logger.info(
                    f"Dropping pass-2 cut {orig_ad['start']:.1f}s-{orig_ad['end']:.1f}s: "
                    f"overlaps a pass-1 held span")
            ad['was_cut'] = False
            orig_ad['was_cut'] = False
            continue
        if confidence >= min_cut_confidence:
            ad['was_cut'] = True
            ad['detection_stage'] = 'verification'
            v_ads_to_cut.append(ad)
            orig_ad['was_cut'] = True
            orig_ad['detection_stage'] = 'verification'
            v_ads_for_ui.append(orig_ad)
            continue
        # Standalone miss: below min_cut_confidence, overlaps no pass-1
        # marker. Deliberately re-reads raw confidence rather than reusing
        # ``confidence`` (which prefers the validator's adjusted_confidence)
        # -- this bucketing is a distinct, more conservative decision from
        # the primary cut gate above.
        conf = float(ad.get('confidence') or 0.0)
        if (verification_miss_autocut_min_confidence > 0
                and conf >= verification_miss_autocut_min_confidence):
            ad['was_cut'] = True
            ad['detection_stage'] = 'verification_miss'
            v_ads_to_cut.append(ad)
            orig_ad['was_cut'] = True
            orig_ad['detection_stage'] = 'verification_miss'
            v_ads_for_ui.append(orig_ad)
        elif conf >= verification_miss_hold_min_confidence:
            ad['was_cut'] = False
            orig_ad['was_cut'] = False
            orig_ad['held_for_review'] = True
            orig_ad['hold_reason'] = HOLD_REASON_VERIFICATION_MISS
            orig_ad['detection_stage'] = 'verification_miss'
            v_ads_held.append(orig_ad)
        else:
            ad['was_cut'] = False
            audio_logger.info(
                f"Dropping standalone pass-2 miss {orig_ad['start']:.1f}s-"
                f"{orig_ad['end']:.1f}s (sponsor={orig_ad.get('sponsor')!r}, "
                f"confidence={conf:.2f}, below verification-miss hold floor "
                f"{verification_miss_hold_min_confidence:.2f})"
            )
    return v_ads_to_cut, v_ads_for_ui, v_ads_held, corroborated_count


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
                                     verification_ads_original, strict=True)}
    # Action reconciliation can replace a candidate with split copies after
    # validation. Prefer the final cut/UI pairing so a short split fragment
    # filtered by AudioProcessor also removes its exact UI marker. Not
    # strict: a cut can legitimately lack a UI twin (e.g. merged spans).
    twin.update({id(p): o for p, o in zip(v_ads_to_cut, v_ads_for_ui,
                                          strict=False)})
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
