"""Pass-2 verification reconciliation: validating, gating, and recutting
pass-2 ad candidates against pass-1 output."""
import functools
import inspect
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


class Pass2Ledger:
    """The final outcome of each pass-2 span; recording a span again replaces its outcome."""

    def __init__(self):
        self._entries = {}
        self._superseded = {}

    def record(self, span, outcome):
        """Record outcome for the original-coordinate span dict, keyed by identity."""
        self._entries[id(span)] = (span, span['start'], span['end'], outcome)

    def supersede(self, span):
        """Mark a span whose outcome other entries already describe."""
        self._entries.pop(id(span), None)
        self._superseded[id(span)] = span

    def record_carved(self, original, labelled_spans):
        """Record each part of original inside labelled_spans, earlier labels first."""
        taken = []
        for src in labelled_spans or []:
            lo, hi = max(original['start'], src['start']), min(original['end'], src['end'])
            if hi > lo:
                for a, b in subtract_spans([(lo, hi)], taken):
                    self.record({'start': a, 'end': b}, src['label'])
            taken.append((src['start'], src['end']))

    def fail(self, *groups):
        """Record every candidate without an outcome yet as dropped by the failed pass."""
        for group in groups:
            for ad in group or []:
                if id(ad) not in self._entries and id(ad) not in self._superseded:
                    self.record(ad, 'dropped:pass_failed')

    def settle(self, cut, held, kept):
        """Record the markers the pass ends with."""
        for ad in cut:
            self.record(ad, 'cut')
        for ad in held:
            self.record(ad, f"held:{ad.get('hold_reason') or 'unknown'}")
        for ad in kept:
            self.record(ad, 'kept')

    def emit(self, slug=None, episode_id=None, run_stats=None):
        """Log one line per span and count the outcomes into run_stats."""
        prefix = f"[{slug}:{episode_id}] " if slug else ''
        counts = {}
        for start, end, outcome in sorted(
                (start, end, outcome) for _span, start, end, outcome in self._entries.values()):
            audio_logger.info(f"{prefix}Pass-2 span {start:.1f}s-{end:.1f}s: {outcome}")
            counts[outcome] = counts.get(outcome, 0) + 1
        if run_stats is not None:
            run_stats['pass2_outcomes'] = counts


def owns_ledger_when_absent(func):
    """Give a caller that passes no ledger its own, emitted when func returns."""
    signature = inspect.signature(func)

    @functools.wraps(func)
    def wrapper(*args, ledger=None, **kwargs):
        if ledger is not None:
            return func(*args, ledger=ledger, **kwargs)
        ledger = Pass2Ledger()
        result = func(*args, ledger=ledger, **kwargs)
        bound = signature.bind_partial(*args, **kwargs).arguments
        ctx = bound.get('ctx')
        ledger.emit(bound.get('slug', getattr(ctx, 'slug', None)),
                    bound.get('episode_id', getattr(ctx, 'episode_id', None)))
        return result
    return wrapper


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


def _merged_barriers_processed(markers, cuts):
    """Markers on the pass-1 timeline, merged into {start, end} barrier spans."""
    return [{'start': start, 'end': end} for start, end, *_ in merge_cut_spans(
        _pass2_keep_barriers_processed(markers, cuts))]


def _unzip(pairs):
    """Split (processed, original) pairs into two parallel lists."""
    return [p for p, _ in pairs], [o for _, o in pairs]


def _drop_matching(processed, original, matches, outcome, ledger):
    """Record each pair whose original matches as outcome; return the rest."""
    kept = []
    for proc, orig in zip(processed, original, strict=True):
        if matches(orig):
            ledger.record(orig, outcome)
        else:
            kept.append((proc, orig))
    return _unzip(kept)


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


@owns_ledger_when_absent
def _split_pass2_candidates_around_spans(processed_ads, original_ads,
                                          barriers_processed, pass1_cuts,
                                          barrier_label, timestamp_map=None,
                                          ledger=None, carved_labels=None,
                                          fragment_policy=None, conflicts=None):
    """Split paired pass-2 candidates around protected processed spans.

    ``carved_labels`` are original-time {start, end, label} spans naming the
    outcome of each carved-off part; without them carved parts are not recorded.
    ``fragment_policy`` 'keep' turns a fragment inside a beep into a conflict hold
    appended to ``conflicts``, 'hold' drops it; both drop short unmeasured fragments.
    """
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
        ledger.record_carved(original, carved_labels)
        # Its fragments and carved parts carry its outcome now.
        ledger.supersede(original)
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
            if fragment_policy is not None and not _fragment_survives(
                    fragment_processed, fragment_original, fragment_policy, pass1_cuts,
                    ledger, conflicts):
                continue
            surviving_processed.append(fragment_processed)
            surviving_original.append(fragment_original)

    return surviving_processed, surviving_original


def _fragment_survives(processed, original, policy, pass1_cuts, ledger, conflicts):
    """Apply a split policy to one carved fragment; False when it is held or dropped."""
    # Keep splits test only the inverted mapping, as before; a hold also owns its cut region.
    if original['end'] <= original['start'] or (
            policy == 'hold' and _covered_by_cuts(original, pass1_cuts, tolerance=0)):
        if policy == 'hold':
            ledger.record(original, 'dropped:beep_interior')
            return False
        original['start'], original['end'] = original['end'], original['start']
        original['held_for_review'] = True
        original['was_cut'] = False
        original['hold_reason'] = HOLD_REASON_VERIFICATION_KEPT_CONFLICT
        conflicts.append(original)
        return False
    if (processed['end'] - processed['start'] < MIN_AD_DURATION
            and not processed.get('_measured_split_fragment')):
        ledger.record(original, 'dropped:short_fragment')
        return False
    return True


@owns_ledger_when_absent
def _exclude_kept_spans_from_verification(verification_ads_processed,
                                           verification_ads_original,
                                           pass1_kept_markers, pass1_cuts, ledger=None):
    """Drop or split pass-2 findings over kept spans; returns (processed, original, conflicts)."""
    if not pass1_kept_markers:
        return verification_ads_processed, verification_ads_original, []
    keep_barriers = _merged_barriers_processed(pass1_kept_markers, pass1_cuts)
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
            ledger.record(orig_ad, 'dropped:inside_kept')
            continue
        if timestamp_map is None:
            timestamp_map = _build_timestamp_map(pass1_cuts)
        processed, original = _split_pass2_candidates_around_spans(
            [ad], [orig_ad], keep_barriers, pass1_cuts, 'kept audio',
            timestamp_map=timestamp_map, ledger=ledger,
            carved_labels=labelled_spans(pass1_kept_markers, 'kept:pass1_keep'),
            fragment_policy='keep', conflicts=conflicts)
        surviving_processed.extend(processed)
        surviving_original.extend(original)
    return surviving_processed, surviving_original, conflicts


@owns_ledger_when_absent
def _split_pass2_candidates_around_holds(parents, holds, pass1_cuts, ledger=None):
    """Carve hold-overlapping pass-2 findings into their parts outside the holds."""
    if not parents:
        return [], []
    # The gate already recorded the in-hold parts as covered:pass1_hold.
    processed, original = _split_pass2_candidates_around_spans(
        *_unzip(parents), _merged_barriers_processed(holds, pass1_cuts), pass1_cuts,
        'held audio', ledger=ledger, fragment_policy='hold')
    for fragment in (*processed, *original):
        for key in [*_HOLD_SPLIT_DROPPED_KEYS,
                    *(k for k in fragment if k.startswith('reviewer_'))]:
            fragment.pop(key, None)
        fragment['split_from_hold'] = True
    return processed, original


@dataclass
class HoldSplitFragments:
    """Outside-hold fragments after validation, and how the gate routed them."""
    processed: list = field(default_factory=list)
    original: list = field(default_factory=list)
    to_cut: list = field(default_factory=list)
    for_ui: list = field(default_factory=list)
    held: list = field(default_factory=list)


def _reaches_hold(slug, episode_id, orig, holds):
    """True, with a log line, when validation moved a fragment into a hold."""
    hold = next((h for h in holds
                 if overlap_seconds(orig['start'], orig['end'], h['start'], h['end'])
                 > EDGE_TOLERANCE), None)
    if hold is not None:
        audio_logger.info(
            f"[{slug}:{episode_id}] Pass-2 fragment {orig['start']:.1f}s-"
            f"{orig['end']:.1f}s reaches into hold {hold['start']:.1f}s-"
            f"{hold['end']:.1f}s after validation")
    return hold is not None


@owns_ledger_when_absent
def _run_candidate_stages(slug, episode_id, processed, original, barriers, protection,
                          validate, gate, fp=(), holds=None, hold_overlaps=None,
                          ledger=None):
    """FP check, hard split, validation, gate; returns (processed, original, gate result).

    With holds=None the gate sees no holds, so a fragment validation moved into one is dropped.
    """
    processed, original = _drop_matching(
        processed, original, lambda o: _matches_false_positive_correction(o, fp),
        'rejected:fp_correction', ledger)
    processed, original = _split_pass2_candidates_around_spans(
        processed, original, protection.hard_proc, protection.pass1_cuts,
        'protected audio', ledger=ledger, carved_labels=protection.hard_sources)
    processed, original = validate(processed, original, barriers)
    if holds is None:
        processed, original = _drop_matching(
            processed, original,
            lambda o: _reaches_hold(slug, episode_id, o, protection.holds_orig),
            'dropped:reaches_hold', ledger)
    return processed, original, gate(processed, original, holds, hold_overlaps)


@owns_ledger_when_absent
def _gate_hold_split_fragments(slug, episode_id, parents, protection, fp, validate, gate,
                               ledger=None):
    """Run the parts of hold-overlapping findings outside the holds through the pass-2 checks."""
    processed, original = _split_pass2_candidates_around_holds(
        parents, protection.holds_orig, protection.pass1_cuts, ledger=ledger)
    if not processed:
        return HoldSplitFragments()
    try:
        # Holds were decided on the full findings; the fragment gate sees none.
        processed, original, (to_cut, for_ui, held, _count, _candidates) = (
            _run_candidate_stages(
                slug, episode_id, processed, original, protection.barriers_proc(),
                protection, validate, gate, fp=fp, ledger=ledger))
    except Exception:
        # The carved parents are superseded, so only these fragments can carry the failure.
        ledger.fail(original)
        raise
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


@owns_ledger_when_absent
def _gate_verification_ads_by_confidence(verification_ads_processed,
                                          verification_ads_original,
                                          min_cut_confidence,
                                          pass1_held_markers=None,
                                          verification_miss_hold_min_confidence=None,
                                          verification_miss_autocut_min_confidence=None,
                                          hard_barriers_orig=None, segments=None,
                                          cue_gate_enabled=False, hold_overlaps=None,
                                          ledger=None):
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
    ``ledger`` records the parts inside holds as covered and below-floor misses as dropped.
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
            # Only a finding reaching past its holds has parts left to cut.
            if hold_overlaps is not None and subtract_spans(
                    [(orig_ad['start'], orig_ad['end'])],
                    [(h['start'], h['end']) for h in overlapping]):
                hold_overlaps.append((dict(ad), dict(orig_ad)))
            ledger.record_carved(orig_ad, labelled_spans(overlapping, 'covered:pass1_hold'))
            ledger.supersede(orig_ad)
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
            # A resurrect verdict may still cut it; the ledger keeps the last outcome.
            ledger.record(orig_ad, 'dropped:below_miss_floor')
            audio_logger.info(
                f"Standalone pass-2 miss {orig_ad['start']:.1f}s-"
                f"{orig_ad['end']:.1f}s (sponsor={orig_ad.get('sponsor')!r}, "
                f"confidence={conf:.2f}) is below verification-miss hold floor "
                f"{verification_miss_hold_min_confidence:.2f}"
            )
    # A later finding may have fast-path corroborated a hold after it got a candidate.
    candidates = [(sub, hold) for hold, subs in release_by_hold.values()
                  if not hold.get('pass2_corroborated') for sub in subs]
    return (v_ads_to_cut, v_ads_for_ui, v_ads_held, corroborated_count, candidates)


def labelled_spans(markers, label):
    """Original-time {start, end, label} spans for record_carved."""
    return [{'start': m['start'], 'end': m['end'], 'label': label} for m in markers or []]


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


@owns_ledger_when_absent
def _drop_uncovered_pass2_ads(slug, episode_id, v_ads_to_cut, v_ads_for_ui,
                               recut_applied, verification_ads_processed,
                               verification_ads_original, total_duration=None,
                               ledger=None):
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
            ledger.record(ui_ad, 'dropped:recut_filtered')
