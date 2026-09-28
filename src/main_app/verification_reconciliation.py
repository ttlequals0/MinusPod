"""Pass-2 verification reconciliation: validating, gating, and recutting
pass-2 ad candidates against pass-1 output."""
import logging
from dataclasses import dataclass, field

from audio_processor import get_replacement_duration
from config import (
    CORRECTION_MATCH_MIN_COVERAGE,
    HOLD_REASON_ESTIMATED_PATTERN,
    HOLD_REASON_VERIFICATION_KEPT_CONFLICT,
    HOLD_REASON_VERIFICATION_MISS,
    MIN_AD_DURATION,
    MIN_AD_DURATION_FOR_REMOVAL,
    PASS2_AUTOAPPROVE_HOLD_REASONS,
    PASS2_AUTOAPPROVE_PROPOSED_IOU,
    PASS2_AUTOAPPROVE_TRIM_SLACK_S,
    PASS2_COVERAGE_ONLY_HOLD_REASONS,
    PASS2_DIFFERENTIAL_AUTOAPPROVE_MIN_AD_INSIDE,
    PASS2_DIFFERENTIAL_AUTOAPPROVE_MIN_HOLD_COVERAGE,
    PASS2_ESTIMATED_AUTOAPPROVE_MIN_AD_INSIDE,
    PASS2_REVIEWED_RELEASE_HOLD_REASONS,
)
from database.settings import registry_get_default
from utils.markers import (
    COVERAGE_GAP_TOLERANCE, EDGE_TOLERANCE, carve_fragment, measured_member_spans, subtract_spans,
)
from utils.time import (
    adjust_timestamp, merge_cut_spans, overlap_ratio, overlap_seconds,
    ranges_overlap,
)
from verification_pass import _build_timestamp_map, _map_to_original

audio_logger = logging.getLogger('podcast.audio')

# How much of a pass-2 finding a kept span must cover before the keep settles
# it. Below this the finding reaches well past the keep, and dropping it whole
# would discard audio the operator never ruled on.
KEPT_SPAN_CONTAINMENT_MIN = 0.9

# Parent verdict state a fragment outside a hold must not inherit.
_HOLD_SPLIT_DROPPED_KEYS = (
    'held_for_review', 'was_cut', 'hold_reason', 'validation', 'pass2_corroborated',
    'pass2_corroborated_span', '_hold_release_of', 'detection_stage', 'user_confirmed',
)


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
    the (padded) hold either one covers. Reasons in
    PASS2_COVERAGE_ONLY_HOLD_REASONS never take this path."""
    if hold.get('hold_reason') in PASS2_COVERAGE_ONLY_HOLD_REASONS:
        return False
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
    inside it (an estimated hold needs only containment), or agrees with the
    reviewer's own proposed sub-span (see _proposed_span_agrees). The ad is
    still dropped (pending audio is never cut mid-pipeline); the hold is
    stamped for auto-approval instead."""
    if (confidence < min_cut_confidence
            or len(overlapping) != 1
            or overlapping[0].get('hold_reason')
            not in PASS2_AUTOAPPROVE_HOLD_REASONS):
        return False
    hold = overlapping[0]
    ad_inside = overlap_ratio(hold['start'], hold['end'],
                              orig_ad['start'], orig_ad['end'])
    # An estimated hold's extent is a guess, so coverage of it proves nothing.
    if (hold.get('hold_reason') == HOLD_REASON_ESTIMATED_PATTERN
            and ad_inside >= PASS2_ESTIMATED_AUTOAPPROVE_MIN_AD_INSIDE):
        return True
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


def _inside_word_edge(segments, value, edge):
    """Move an edge inward off any timed word it splits."""
    for seg in segments or []:
        for word in seg.get('words') or []:
            lo, hi = word.get('start'), word.get('end')
            if lo is None or hi is None:
                continue
            if lo < value - EDGE_TOLERANCE and hi > value + EDGE_TOLERANCE:
                return hi if edge == 'start' else lo
    return value


def _hold_release_span(hold, orig_ad, min_cut_confidence, other_holds,
                       hard_barriers_orig, segments):
    """Narrowest pass-2-supported (start, end) inside a hold, original time, or None."""
    lo = max(orig_ad['start'], hold['start'])
    hi = min(orig_ad['end'], hold['end'])
    # Same slack the auto-approve trim ignores, so no sliver of hold is left behind.
    if lo - hold['start'] <= PASS2_AUTOAPPROVE_TRIM_SLACK_S:
        lo = hold['start']
    if hold['end'] - hi <= PASS2_AUTOAPPROVE_TRIM_SLACK_S:
        hi = hold['end']
    measured = measured_member_spans(orig_ad, min_cut_confidence)
    if measured:
        runs = []
        for a, b in sorted((max(a, lo), min(b, hi)) for a, b, _ in measured):
            if b <= a:
                continue
            if runs and a - runs[-1][1] <= COVERAGE_GAP_TOLERANCE:
                runs[-1][1] = max(runs[-1][1], b)
            else:
                runs.append([a, b])
        if not runs:
            return None
        # Longest run of the member union, so an unmeasured gap is never included.
        lo, hi = max(runs, key=lambda run: run[1] - run[0])
    if _proposed_span_agrees(hold, orig_ad):
        agreed = _corroborated_span(hold, orig_ad)
        lo, hi = max(lo, agreed['start']), min(hi, agreed['end'])
    pieces = subtract_spans([(lo, hi)], [(h['start'], h['end']) for h in other_holds])
    if not pieces:
        return None
    lo, hi = max(pieces, key=lambda piece: piece[1] - piece[0])
    lo = _inside_word_edge(segments, lo, 'start')
    hi = _inside_word_edge(segments, hi, 'end')
    if hi - lo < MIN_AD_DURATION:
        return None
    if any(overlap_seconds(lo, hi, b['start'], b['end']) > EDGE_TOLERANCE
           for b in hard_barriers_orig or []):
        return None
    return lo, hi


def _pass2_keep_barriers_processed(pass1_kept_markers, pass1_cuts):
    """Collect every keep marker on the pass-1 processed timeline."""
    replacement_duration = get_replacement_duration()
    return [
        dict(
            marker,
            start=adjust_timestamp(
                marker['start'], pass1_cuts, replacement_duration),
            end=adjust_timestamp(
                marker['end'], pass1_cuts, replacement_duration),
        )
        for marker in pass1_kept_markers or []
    ]


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


def _split_pass2_candidates_around_spans(processed_ads, original_ads,
                                          barriers_processed, pass1_cuts,
                                          barrier_label, timestamp_map=None):
    """Split paired pass-2 candidates around protected processed spans."""
    if not barriers_processed:
        return processed_ads, original_ads
    if len(processed_ads) != len(original_ads):
        raise ValueError(
            'Pass-2 processed/original marker lists must stay paired')

    barriers = [(marker['start'], marker['end']) for marker in barriers_processed]
    if timestamp_map is None:
        timestamp_map = _build_timestamp_map(pass1_cuts)
    replacement_duration = get_replacement_duration()
    surviving_processed = []
    surviving_original = []

    for processed, original in zip(processed_ads, original_ads, strict=True):
        whole = (processed['start'], processed['end'])
        fragments = subtract_spans([whole], barriers)
        if fragments == [whole]:
            surviving_processed.append(processed)
            surviving_original.append(original)
            continue

        audio_logger.info(
            f"Pass-2 candidate {processed['start']:.1f}s-"
            f"{processed['end']:.1f}s split around {barrier_label} into "
            f"{len(fragments)} removable fragment(s)")
        # The parent cleared the renderer's duration floor before a protected
        # span carved it; validation still decides whether each piece is a cut.
        trusted_fragment = (
            processed.get('_measured_split_fragment')
            or processed['end'] - processed['start']
            >= MIN_AD_DURATION_FOR_REMOVAL
        )
        for start, end in fragments:
            fragment_processed = carve_fragment(processed, start, end)
            fragment_original = carve_fragment(
                original,
                _map_to_original(start, timestamp_map, replacement_duration),
                _map_to_original(end, timestamp_map, replacement_duration),
            )
            if trusted_fragment:
                fragment_processed['_measured_split_fragment'] = True
                fragment_original['_measured_split_fragment'] = True
            surviving_processed.append(fragment_processed)
            surviving_original.append(fragment_original)

    return surviving_processed, surviving_original


def _exclude_kept_spans_from_verification(verification_ads_processed,
                                           verification_ads_original,
                                           pass1_kept_markers, pass1_cuts):
    """Drop or split pass-2 findings over kept spans; returns (processed, original, conflicts)."""
    if not pass1_kept_markers:
        return verification_ads_processed, verification_ads_original, []
    keep_barriers = [{'start': start, 'end': end} for start, end, *_ in merge_cut_spans(
        _pass2_keep_barriers_processed(pass1_kept_markers, pass1_cuts))]
    surviving_processed = []
    surviving_original = []
    conflicts = []
    timestamp_map = None
    for ad, orig_ad in zip(verification_ads_processed, verification_ads_original, strict=True):
        covered = sum(overlap_seconds(barrier['start'], barrier['end'],
                                      ad['start'], ad['end'])
                      for barrier in keep_barriers)
        if covered <= 0:
            surviving_processed.append(ad)
            surviving_original.append(orig_ad)
            continue
        span = ad['end'] - ad['start']
        inside = min(1.0, covered / span) if span > 0 else 0.0
        if inside >= KEPT_SPAN_CONTAINMENT_MIN:
            audio_logger.info(
                f"Pass-2 finding {ad['start']:.1f}s-{ad['end']:.1f}s "
                f"(processed) lies inside a span the category action keeps; "
                f"dropping it"
            )
            continue
        if timestamp_map is None:
            timestamp_map = _build_timestamp_map(pass1_cuts)
        fragments = list(zip(*_split_pass2_candidates_around_spans(
            [ad], [orig_ad], keep_barriers, pass1_cuts, 'kept audio',
            timestamp_map=timestamp_map), strict=True))
        for proc, orig in fragments:
            if orig['end'] <= orig['start']:
                # A fragment inside a replacement beep has no original audio to cut.
                orig['start'], orig['end'] = orig['end'], orig['start']
                orig['held_for_review'] = True
                orig['was_cut'] = False
                orig['hold_reason'] = HOLD_REASON_VERIFICATION_KEPT_CONFLICT
                conflicts.append(orig)
                continue
            if (proc['end'] - proc['start'] < MIN_AD_DURATION
                    and not proc.get('_measured_split_fragment')):
                audio_logger.info(
                    f"Pass-2 fragment {proc['start']:.1f}s-{proc['end']:.1f}s "
                    f"(processed) left beside kept audio is too short; dropping it"
                )
                continue
            surviving_processed.append(proc)
            surviving_original.append(orig)
    return surviving_processed, surviving_original, conflicts


def _split_pass2_candidates_around_holds(parents, pass1_held_markers, pass1_cuts):
    """Carve hold-overlapping pass-2 findings into their parts outside the holds."""
    if not parents:
        return [], []
    barriers = [{'start': start, 'end': end} for start, end, *_ in merge_cut_spans(
        _pass2_keep_barriers_processed(pass1_held_markers, pass1_cuts))]
    carved = _split_pass2_candidates_around_spans(
        [p for p, _ in parents], [o for _, o in parents], barriers, pass1_cuts,
        'held audio')
    surviving_processed = []
    surviving_original = []
    for proc, orig in zip(*carved, strict=True):
        if orig['end'] <= orig['start'] or any(
                c['start'] <= orig['start'] and orig['end'] <= c['end']
                for c in pass1_cuts or []):
            audio_logger.info(
                f"Pass-2 fragment {proc['start']:.1f}s-{proc['end']:.1f}s "
                f"(processed) beside held audio lies inside a replacement beep; dropping it")
            continue
        if (proc['end'] - proc['start'] < MIN_AD_DURATION
                and not proc.get('_measured_split_fragment')):
            audio_logger.info(
                f"Pass-2 fragment {proc['start']:.1f}s-{proc['end']:.1f}s "
                f"(processed) left beside held audio is too short; dropping it")
            continue
        for fragment in (proc, orig):
            for key in [*_HOLD_SPLIT_DROPPED_KEYS,
                        *(k for k in fragment if k.startswith('reviewer_'))]:
                fragment.pop(key, None)
            fragment['split_from_hold'] = True
        surviving_processed.append(proc)
        surviving_original.append(orig)
    return surviving_processed, surviving_original


@dataclass
class HoldSplitFragments:
    """Outside-hold fragments after validation, and how the gate routed them."""
    processed: list = field(default_factory=list)
    original: list = field(default_factory=list)
    to_cut: list = field(default_factory=list)
    for_ui: list = field(default_factory=list)
    held: list = field(default_factory=list)


def _gate_hold_split_fragments(slug, episode_id, parents, pass1_held_markers,
                               pass1_cuts, false_positive_corrections, protection,
                               validate, gate):
    """Run the parts of hold-overlapping findings outside the holds through the pass-2 checks."""
    # validate and gate take (processed, original); gate must not see the pass-1 holds.
    processed, original = _split_pass2_candidates_around_holds(
        parents, pass1_held_markers, pass1_cuts)
    pairs = []
    for proc, orig in zip(processed, original, strict=True):
        if _matches_false_positive_correction(orig, false_positive_corrections):
            audio_logger.info(
                f"[{slug}:{episode_id}] Pass-2 fragment {orig['start']:.1f}s-"
                f"{orig['end']:.1f}s matches a user false-positive rejection; dropping it")
            continue
        pairs.append((proc, orig))
    if not pairs:
        return HoldSplitFragments()
    processed, original = _split_pass2_candidates_around_spans(
        [p for p, _ in pairs], [o for _, o in pairs], protection.hard_proc,
        pass1_cuts, 'protected audio')
    processed, original = validate(processed, original)
    pairs = []
    for proc, orig in zip(processed, original, strict=True):
        hold = next((h for h in pass1_held_markers or []
                     if overlap_seconds(orig['start'], orig['end'], h['start'], h['end'])
                     > EDGE_TOLERANCE), None)
        if hold is not None:
            audio_logger.info(
                f"[{slug}:{episode_id}] Pass-2 fragment {orig['start']:.1f}s-"
                f"{orig['end']:.1f}s reaches into hold {hold['start']:.1f}s-"
                f"{hold['end']:.1f}s after validation; dropping it")
            continue
        pairs.append((proc, orig))
    if not pairs:
        return HoldSplitFragments()
    processed, original = [p for p, _ in pairs], [o for _, o in pairs]
    to_cut, for_ui, held, _count, _candidates = gate(processed, original)
    audio_logger.info(
        f"[{slug}:{episode_id}] {len(processed)} pass-2 fragment(s) outside held "
        f"spans: {len(to_cut)} cut, {len(held)} held")
    return HoldSplitFragments(processed, original, to_cut, for_ui, held)


def _add_release_candidate(release_by_hold, orig_ad, hold, overlapping,
                           min_cut_confidence, hard_barriers_orig, segments):
    """Record the finding's supported span in hold for review, longest of overlapping ones; True if recorded."""
    if (hold.get('hold_reason') not in PASS2_REVIEWED_RELEASE_HOLD_REASONS
            or hold.get('pass2_corroborated') or hold.get('pass2_reviewed_release')):
        return False
    span = _hold_release_span(
        hold, orig_ad, min_cut_confidence,
        [h for h in overlapping if h is not hold], hard_barriers_orig, segments)
    if span is None:
        return False
    _hold, subs = release_by_hold.setdefault(id(hold), (hold, []))
    overlapped = {id(sub): sub for sub in subs
                  if overlap_seconds(sub['start'], sub['end'], *span) > 0}
    if any(sub['end'] - sub['start'] >= span[1] - span[0] for sub in overlapped.values()):
        return False
    orig_sub = carve_fragment(orig_ad, *span)
    orig_sub['held_for_review'] = True
    orig_sub['_hold_release_of'] = (hold['start'], hold['end'])
    subs[:] = sorted([*(sub for sub in subs if id(sub) not in overlapped), orig_sub],
                     key=lambda sub: sub['start'])
    audio_logger.info(
        f"Pass-2 ad {orig_ad['start']:.1f}s-{orig_ad['end']:.1f}s supports "
        f"{span[0]:.1f}s-{span[1]:.1f}s of {hold.get('hold_reason')} hold "
        f"{hold['start']:.1f}s-{hold['end']:.1f}s: sending it to review")
    return True


def _gate_verification_ads_by_confidence(verification_ads_processed,
                                          verification_ads_original,
                                          min_cut_confidence,
                                          pass1_held_markers=None,
                                          verification_miss_hold_min_confidence=None,
                                          verification_miss_autocut_min_confidence=None,
                                          hard_barriers_orig=None, segments=None,
                                          cue_gate_enabled=False, hold_overlaps=None):
    """Confidence gate pass-2 ads.

    Returns (v_ads_to_cut, v_ads_for_ui, v_ads_held, corroborated_count,
    hold_release_candidates). Each candidate is (original_sub, hold): a
    pass-2-supported span inside a hold the fast path could not corroborate,
    for a review that may release only that span. A hold can have several.

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

    ``hold_overlaps``, when a list, receives a pre-gate copy of each hold-overlapping
    (processed, original) pair so the caller can keep its parts outside the hold.
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
    release_by_hold = {}
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
            if hold_overlaps is not None:
                hold_overlaps.append((dict(ad), dict(orig_ad)))
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
                sent = False
                if confidence >= min_cut_confidence and not cue_gate_enabled:
                    for hold in overlapping:
                        sent |= _add_release_candidate(
                            release_by_hold, orig_ad, hold, overlapping,
                            min_cut_confidence, hard_barriers_orig, segments)
                if sent:
                    audio_logger.info(
                        f"Sent pass-2 span {orig_ad['start']:.1f}s-{orig_ad['end']:.1f}s "
                        f"to hold review")
                else:
                    audio_logger.info(
                        f"Dropping pass-2 cut {orig_ad['start']:.1f}s-{orig_ad['end']:.1f}s: "
                        f"overlaps a pass-1 held span")
            ad['was_cut'] = False
            orig_ad['was_cut'] = False
            # Held so the resurrection pool can never cut audio a hold protects.
            orig_ad['held_for_review'] = True
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
    # A later finding may have fast-path corroborated a hold after it got a candidate.
    candidates = [(sub, hold) for hold, subs in release_by_hold.values()
                  if not hold.get('pass2_corroborated') for sub in subs]
    return (v_ads_to_cut, v_ads_for_ui, v_ads_held, corroborated_count, candidates)


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
