"""Ad reviewer runs for pass 1 and pass 2, including hold release."""

import logging
from ad_reviewer import (
    AdReviewer, abstain_hold_reason, is_contradiction_hold, mark_reviewer_hold,
    split_resurrection_pool, stamp_reviewer_fields,
)

from audio_processor import get_replacement_duration
from utils.markers import (EDGE_TOLERANCE, carve_fragment,
                           clip_merge_spans, invalidate_tail_provenance,
                           merge_runs, set_reviewer_locks, spans_match, subtract_spans)

from utils.time import (
    adjust_timestamp, overlap_seconds,
    ranges_overlap, span_inside_any_cut,
)

from config import (
    MIN_AD_DURATION, HOLD_REASON_REVIEWER_CONTRADICTION,
    HOLD_REASON_REVIEWER_BOUNDARY_CONFLICT,
)

from llm_capabilities import (
    PASS_REVIEWER_1, PASS_REVIEWER_2,
    clear_fallback,
)

from main_app.verification_reconciliation import Pass2Ledger
from main_app.pipeline import common
from main_app.pipeline.shared import (db, storage, ad_detector)

audio_logger = logging.getLogger('podcast.audio')


def _build_reviewer(db, ad_detector) -> AdReviewer:
    # No llm_client: the reviewer resolves its own route (possibly a
    # different provider than detection) per review() call.
    return AdReviewer(
        db=db,
        sponsor_service=getattr(ad_detector, 'sponsor_service', None),
        sponsor_history_provider=ad_detector._build_known_pattern_hint,
    )


def _build_episode_meta(slug, episode_id, podcast_id, podcast_name,
                        episode_title, podcast_description, episode_description,
                        audio_analysis=None, effective_category_actions=None,
                        hard_barriers=None, min_cut_confidence=None,
                        protected_spans=None):
    return {
        'podcast_name': podcast_name,
        'episode_title': episode_title,
        'episode_description': episode_description,
        'podcast_description': podcast_description,
        'slug': slug,
        'episode_id': episode_id,
        'podcast_id': podcast_id,
        # Optional: the reviewer renders any audio_cue signals near an ad's
        # boundaries into the per-ad user prompt. Pass-2 leaves this None
        # because its analysis is in processed-audio coordinates that do not
        # align with the original-audio ad spans the reviewer sees (#350).
        'audio_analysis': audio_analysis,
        'effective_category_actions': effective_category_actions,
        # Kept audio in the reviewer's coordinates; a widened edge stops at it.
        'hard_barriers': hard_barriers,
        # Confidence a transcript member needs before its precise edge softens a fingerprint's.
        'min_cut_confidence': min_cut_confidence,
        # Labelled keeps and user rejections the reviewer prompt lists as hard limits.
        'protected_spans': protected_spans,
    }


def _labelled_protected_spans(keeps, user_rejects):
    """Hard protection sources labelled for the reviewer prompt, original time."""
    return ([{'start': k['start'], 'end': k['end'], 'kind': 'keep',
              'category': k.get('category')} for k in keeps or []]
            + [{'start': r['start'], 'end': r['end'], 'kind': 'user_reject'}
               for r in user_rejects or []])


def _log_reviewer_verdicts(slug, episode_id, pass_num, verdicts):
    """Log the per-verdict counts for a reviewer pass."""
    audio_logger.info(
        f"[{slug}:{episode_id}] Reviewer pass {pass_num} verdicts: "
        f"{sum(1 for v in verdicts if v.verdict == 'confirmed')} confirmed, "
        f"{sum(1 for v in verdicts if v.verdict == 'adjust')} adjusted, "
        f"{sum(1 for v in verdicts if v.verdict == 'reject')} rejected, "
        f"{sum(1 for v in verdicts if v.verdict == 'resurrect')} resurrected, "
        f"{sum(1 for v in verdicts if v.verdict == 'inconclusive')} inconclusive, "
        f"{sum(1 for v in verdicts if v.verdict == 'failure')} failed"
    )


def _hold_pass2_verdict(v, proc_ad, held_ad, v_ads_to_cut, v_ads_for_ui, v_ads_held,
                        apply=None):
    """Pull a pass-2 ad from the cuts and file its original-time marker as held; False when none."""
    if proc_ad in v_ads_to_cut:
        v_ads_to_cut.remove(proc_ad)
    if proc_ad is not None:
        proc_ad['was_cut'] = False
        stamp_reviewer_fields(proc_ad, v)
    if held_ad is None:
        return False
    (apply or _apply_reviewer_verdict_to_ad)(held_ad, v)
    if held_ad in v_ads_for_ui:
        v_ads_for_ui.remove(held_ad)
    if held_ad not in v_ads_held:
        v_ads_held.append(held_ad)
    return True


def _apply_pass2_reviewer(ctx, v_ads_to_cut, v_ads_for_ui, v_ads_held,
                           verification_ads_processed, verification_ads_original,
                           original_segments, min_cut_confidence,
                           cue_gate_enabled=False, pass1_cuts=None,
                           protected_original_ranges=None, segment_actions=None,
                           protected_spans=None, ledger=None, hard_barriers=None):
    """Run the reviewer on pass 2 results, in original transcript coordinates.

    Mutates ``v_ads_to_cut``, ``v_ads_for_ui`` and ``v_ads_held`` in place.
    Maps adjustments only to surviving, unprotected pass-1 output; redundant
    spans are dropped and ambiguous proposals become holds.

    Contradiction holds (verdict confirmed/adjust whose reasoning denies the
    ad exists) divert the ad out of the cut list into ``v_ads_held`` as an
    original-coordinate pending-review marker, the same shape pass-1 holds
    take via _apply_reviewer_verdict_to_ad. The accepted pool the reviewer
    sees IS ``v_ads_for_ui`` (original coords), so no processed-to-original
    mapping is needed for the held marker.

    ``cue_gate_enabled``: when True, resurrection is suppressed entirely. A
    resurrected non-held reject would become a cue-less auto-cut, violating the
    gate's guarantee.
    """
    ledger = ledger or Pass2Ledger()
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

    if cue_gate_enabled:
        eligible_originals = []
    else:
        eligible_originals = split_resurrection_pool(
            verification_ads_original, accepted_originals, min_cut_confidence
        )
    if not accepted_originals and not eligible_originals:
        return

    common._publish_status('update_job_stage', slug, episode_id, "pass2:reviewing", 90)

    podcast_id = ctx.podcast_id

    audio_logger.info(
        f"[{slug}:{episode_id}] Reviewer pass 2: "
        f"{len(accepted_originals)} accepted + {len(eligible_originals)} resurrection-eligible"
    )

    reviewer = _build_reviewer(db, ad_detector)
    episode_meta = _build_episode_meta(
        slug, episode_id, podcast_id, podcast_name,
        episode_title, podcast_description, episode_description,
        effective_category_actions=segment_actions,
        hard_barriers=hard_barriers,
        min_cut_confidence=min_cut_confidence,
        protected_spans=protected_spans,
    )
    pass2_model = ad_detector.get_verification_model()
    pass2_provider = ad_detector.get_verification_provider()
    result = reviewer.review(
        accepted_ads=accepted_originals,
        resurrection_eligible=eligible_originals,
        segments=original_segments or [],
        episode_meta=episode_meta,
        pass_num=2,
        pass_model=pass2_model,
        pass_provider=pass2_provider,
    )

    # Index by (start, end) so verdict application is O(V), not O(V*N).
    original_to_processed = {
        (orig.get('start'), orig.get('end')): proc
        for orig, proc in zip(verification_ads_original, verification_ads_processed, strict=True)
    }
    ui_by_key = {(a.get('start'), a.get('end')): a for a in v_ads_for_ui}
    original_by_key = {(a.get('start'), a.get('end')): a for a in verification_ads_original}

    for v in result.verdicts:
        key = (v.original_start, v.original_end)
        proc_ad = original_to_processed.get(key)
        ui_ad = ui_by_key.get(key)

        held_ad = ui_ad or original_by_key.get(key)
        lists = (v_ads_to_cut, v_ads_for_ui, v_ads_held)
        if v.inconclusive_hold:
            _hold_pass2_verdict(v, proc_ad, held_ad, *lists)
            continue

        if v.boundary_conflict:
            if not _hold_pass2_verdict(v, proc_ad, held_ad, *lists):
                audio_logger.warning(
                    f"[{slug}:{episode_id}] Pass 2 reviewer boundary conflict @ "
                    f"{v.original_start:.1f}s has no original marker"
                )
            continue

        # Checked before the adjust->confirmed coercion so a held adjust is not
        # coerced into a full-span cut.
        if v.pool == 'accepted' and is_contradiction_hold(
                v.verdict, v.reasoning, v.structured_is_ad):
            if not _hold_pass2_verdict(v, proc_ad, held_ad, *lists):
                audio_logger.warning(
                    f"[{slug}:{episode_id}] Pass 2 contradiction hold @ "
                    f"{v.original_start:.1f}s has no original twin; "
                    f"removed from cut list without a held marker"
                )
            continue

        if v.verdict == 'adjust':
            adjusted_start, adjusted_end = v.adjusted_start, v.adjusted_end
            if (adjusted_start is None or adjusted_end is None
                    or adjusted_end <= adjusted_start):
                adjusted_start, adjusted_end = None, None
            cuts = pass1_cuts or []
            if (adjusted_start is not None
                    and span_inside_any_cut(adjusted_start, adjusted_end, cuts)):
                if proc_ad in v_ads_to_cut:
                    v_ads_to_cut.remove(proc_ad)
                if proc_ad is not None:
                    proc_ad['was_cut'] = False
                if ui_ad in v_ads_for_ui:
                    v_ads_for_ui.remove(ui_ad)
                if held_ad is not None:
                    ledger.record(held_ad, 'covered')
                continue
            crosses_cut = adjusted_start is None or any(
                ranges_overlap(adjusted_start, adjusted_end, cut['start'], cut['end'])
                              for cut in cuts)
            # An edge clamped onto a protected range touches it without taking its audio.
            crosses_protected = (adjusted_start is not None and any(
                overlap_seconds(adjusted_start, adjusted_end,
                                protected['start'], protected['end']) > 0
                for protected in (protected_original_ranges or [])))
            if crosses_cut or crosses_protected or proc_ad is None or ui_ad is None:
                def hold_crossing(ad, v, lo=adjusted_start, hi=adjusted_end):
                    stamp_reviewer_fields(ad, v)
                    ad.update(was_cut=False, held_for_review=True,
                              hold_reason=HOLD_REASON_REVIEWER_CONTRADICTION)
                    if lo is not None:
                        ad.update(reviewer_proposed_start=lo, reviewer_proposed_end=hi)
                _hold_pass2_verdict(v, proc_ad, held_ad, *lists, apply=hold_crossing)
                continue
            beep = get_replacement_duration()
            proc_ad['start'] = adjust_timestamp(adjusted_start, cuts, beep)
            proc_ad['end'] = adjust_timestamp(adjusted_end, cuts, beep)
            _apply_reviewer_verdict_to_ad(ui_ad, v)
            stamp_reviewer_fields(proc_ad, v)
            invalidate_tail_provenance(proc_ad, proc_ad['end'])
            continue

        if v.verdict == 'reject':
            if proc_ad in v_ads_to_cut:
                v_ads_to_cut.remove(proc_ad)
            if ui_ad is not None:
                stamp_reviewer_fields(ui_ad, v)
                ui_ad['was_cut'] = False
                ui_ad['source'] = 'reviewer'
                v_ads_for_ui.remove(ui_ad)
                ledger.record(ui_ad, 'rejected:reviewer')
            # Evidence floor (ad_reviewer.reject_hold_evidence): a reject over
            # measured evidence is held for a human, not dropped.
            if v.reject_hold_reason:
                _hold_pass2_verdict(v, proc_ad, held_ad, *lists)
            continue

        if v.verdict == 'resurrect':
            if (original_by_key.get(key) or {}).get('held_for_review'):
                continue
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
                stamp_reviewer_fields(proc_ad, v)
                v_ads_to_cut.append(proc_ad)
            orig_ad = original_by_key.get(key)
            if orig_ad is not None:
                orig_ad['was_cut'] = True
                orig_ad['detection_stage'] = 'verification'
                orig_ad['source'] = 'reviewer'
                stamp_reviewer_fields(orig_ad, v)
                if orig_ad not in v_ads_for_ui:
                    v_ads_for_ui.append(orig_ad)
            continue

        # confirmed or failure: stamp reviewer fields without mutating cuts.
        if proc_ad is not None:
            stamp_reviewer_fields(proc_ad, v)
        if ui_ad is not None:
            stamp_reviewer_fields(ui_ad, v)

    _log_reviewer_verdicts(slug, episode_id, 2, result.verdicts)


def _released_span(v, sub, hold, barriers):
    """(released span, outside pieces) for a successful review of a hold subspan, or None."""
    if (not v.success or v.inconclusive_hold or v.boundary_conflict
            or is_contradiction_hold(v.verdict, v.reasoning, v.structured_is_ad)):
        return None
    if v.verdict == 'confirmed':
        lo, hi = sub['start'], sub['end']
    elif v.verdict == 'adjust' and v.adjusted_start is not None and v.adjusted_end is not None:
        lo, hi = v.adjusted_start, v.adjusted_end
    else:
        return None
    span, outside = (lo, hi), []
    if any(b - a > EDGE_TOLERANCE for a, b in subtract_spans(
            [(lo, hi)], [(hold['start'], hold['end']), (sub['start'], sub['end'])])):
        # A covering adjust releases its part inside the hold; the rest is a new candidate.
        if (lo > sub['start'] + EDGE_TOLERANCE or hi < sub['end'] - EDGE_TOLERANCE
                or overlap_seconds(lo, hi, hold['start'], hold['end']) <= EDGE_TOLERANCE):
            return None
        span = (max(lo, hold['start']), min(hi, hold['end']))
        outside = [(a, b) for a, b in subtract_spans([(lo, hi)], [(hold['start'], hold['end'])])
                   if b - a > EDGE_TOLERANCE]
    if span[1] - span[0] < MIN_AD_DURATION:
        return None
    # The whole reviewed read, outside pieces included, must stay clear of barriers.
    if any(overlap_seconds(lo, hi, b['start'], b['end']) > EDGE_TOLERANCE
           for b in barriers):
        return None
    return {'start': span[0], 'end': span[1]}, outside


def _outside_candidates(pieces, pass1_cuts, covered, ledger):
    """Pass-2 (processed, original) pairs for (start, end, verdict, sub) pieces outside holds."""
    spans = [(c['start'], c['end']) for c in [*pass1_cuts, *covered]]
    parts = sorted([(a, b, v, sub) for lo, hi, v, sub in pieces
                    for a, b in subtract_spans([(lo, hi)], spans) if b - a > EDGE_TOLERANCE],
                   key=lambda part: part[:2])
    beep = get_replacement_duration()
    pairs = []
    # Overlapping pieces from covering adjusts on different holds become one candidate.
    for a, b in merge_runs([part[:2] for part in parts], gap=EDGE_TOLERANCE):
        _a, _b, v, sub = next(part for part in parts if part[0] == a)
        orig = carve_fragment(sub, a, b)
        orig.pop('held_for_review', None)
        orig.update(source='reviewer', reason=v.reasoning or sub.get('reason'))
        if b - a < MIN_AD_DURATION:
            ledger.record(orig, 'dropped:short_fragment')
            continue
        pairs.append((carve_fragment(orig, adjust_timestamp(a, pass1_cuts, beep),
                                     adjust_timestamp(b, pass1_cuts, beep)), orig))
    return pairs


def _review_hold_release_candidates(ctx, candidates, original_segments,
                                    protection, segment_actions=None,
                                    min_cut_confidence=None, pass1_cuts=None,
                                    covered=(), ledger=None):
    """Review pass-2 subspans inside holds; returns (released count, outside-hold candidate pairs)."""
    if not candidates or not _ad_review_enabled(db):
        return 0, []
    ledger = ledger or Pass2Ledger()
    pass1_cuts = pass1_cuts or []
    reviewer = _build_reviewer(db, ad_detector)
    episode_meta = _build_episode_meta(
        ctx.slug, ctx.episode_id, ctx.podcast_id, ctx.podcast_name,
        ctx.episode_title, ctx.podcast_description, ctx.episode_description,
        effective_category_actions=segment_actions,
        hard_barriers=protection.hard_orig, min_cut_confidence=min_cut_confidence,
        protected_spans=protection.hard_labelled)
    result = reviewer.review(
        accepted_ads=[orig_sub for orig_sub, _hold in candidates],
        resurrection_eligible=[],
        segments=original_segments or [],
        episode_meta=episode_meta,
        pass_num=2,
        pass_model=ad_detector.get_verification_model(),
        pass_provider=ad_detector.get_verification_provider(),
    )
    by_key = {(sub['start'], sub['end']): (sub, hold) for sub, hold in candidates}
    released = 0
    released_by_hold = {}
    outside_pieces = []
    for v in result.verdicts:
        sub, hold = by_key.get((v.original_start, v.original_end), (None, None))
        if hold is None:
            continue
        prior = released_by_hold.get(id(hold), [])
        first = not prior
        # A fast-path corroboration already owns the hold's approved span.
        if first and (hold.get('pass2_reviewed_release') or hold.get('pass2_corroborated')):
            continue
        result_span = _released_span(v, sub, hold, [
            *protection.barriers_orig(exclude=[hold]), *prior])
        if result_span is None:
            reason = v.reasoning or f"Review returned {v.verdict}"
            # Diagnostic only: no reviewer_verdict, source or reviewer_reasoning on the hold.
            if first:
                hold['pass2_hold_review'] = {
                    'span': [float(sub['start']), float(sub['end'])],
                    'verdict': v.verdict, 'reason': reason}
                outcome = 'the hold stays whole'
            else:
                outcome = (f"subspan {sub['start']:.1f}s-{sub['end']:.1f}s of hold "
                           f"{hold['start']:.1f}s-{hold['end']:.1f}s stays held")
            audio_logger.info(
                f"[{ctx.slug}:{ctx.episode_id}] Review of {sub['start']:.1f}s-"
                f"{sub['end']:.1f}s inside hold {hold['start']:.1f}s-"
                f"{hold['end']:.1f}s returned {v.verdict}; {outcome}. "
                f"Reason: {reason}")
            continue
        span, outside = result_span
        if first:
            hold['pass2_reviewed_release'] = span
            hold['pass2_corroborated'] = True
            hold['pass2_corroborated_span'] = dict(span)
            hold.setdefault('validation', {}).setdefault('flags', []).append(
                'INFO: Pass-2 subspan confirmed by review')
        hold.pop('pass2_hold_review', None)
        released_by_hold.setdefault(id(hold), []).append(span)
        hold['pass2_released_spans'] = sorted(
            released_by_hold[id(hold)], key=lambda s: s['start'])
        released += 1
        outside_pieces.extend((a, b, v, sub) for a, b in outside)
        note = ''
        if outside:
            note = "; outside the hold: " + ', '.join(f"{a:.1f}s-{b:.1f}s" for a, b in outside)
        audio_logger.info(
            f"[{ctx.slug}:{ctx.episode_id}] Review released {span['start']:.1f}s-"
            f"{span['end']:.1f}s of hold {hold['start']:.1f}s-{hold['end']:.1f}s{note}")
    outside_pairs = _outside_candidates(outside_pieces, pass1_cuts, covered, ledger)
    if outside_pieces:
        # Parts already cut in pass 1 or found by pass 2 are not sent again.
        sent = ', '.join(f"{o['start']:.1f}s-{o['end']:.1f}s" for _p, o in outside_pairs)
        audio_logger.info(
            f"[{ctx.slug}:{ctx.episode_id}] Outside-hold parts sent as pass-2 "
            f"candidates: {sent or 'none'}")
    return released, outside_pairs


def _hold_adjustments_crossing_final_holds(processed_ads, original_ads, held_ads):
    """Hold adjusted pass-2 cuts that overlap a sibling hold from the same batch."""
    changed = True
    while changed:
        changed = False
        for processed, original in list(zip(processed_ads, original_ads, strict=False)):
            if not original.get('reviewer_moved') or not any(
                    ranges_overlap(original['start'], original['end'],
                                   held['start'], held['end'])
                    for held in held_ads):
                continue
            proposed_start, proposed_end = original['start'], original['end']
            original['start'] = original.get('reviewer_original_start', proposed_start)
            original['end'] = original.get('reviewer_original_end', proposed_end)
            original['reviewer_proposed_start'] = proposed_start
            original['reviewer_proposed_end'] = proposed_end
            original['was_cut'] = False
            original['held_for_review'] = True
            original['hold_reason'] = HOLD_REASON_REVIEWER_CONTRADICTION
            processed['was_cut'] = False
            processed_ads.remove(processed)
            original_ads.remove(original)
            held_ads.append(original)
            changed = True


def _ad_review_enabled(db) -> bool:
    """Read the opt-in flag for the LLM ad reviewer."""
    try:
        value = db.get_setting('enable_ad_review')
    except Exception:
        return False
    return str(value or '').strip().lower() == 'true'


def _apply_reviewer_verdict_to_ad(ad, v):
    """Merge a single reviewer verdict into the master ad dict, in place."""
    if v.inconclusive_hold:
        mark_reviewer_hold(ad, v, abstain_hold_reason(v))
        return
    if v.boundary_conflict:
        mark_reviewer_hold(ad, v, HOLD_REASON_REVIEWER_BOUNDARY_CONFLICT)
        ad['reviewer_boundary_conflict'] = True
        ad['reviewer_proposed_start'] = v.adjusted_start
        ad['reviewer_proposed_end'] = v.adjusted_end
        return
    stamp_reviewer_fields(ad, v)
    if is_contradiction_hold(v.verdict, v.reasoning, v.structured_is_ad):
        # Contradiction guard (spec 1.4): hold for a human, never auto-reject.
        # Boundaries stay at the pass-1 values; an "adjust" whose reasoning
        # denies the ad exists is not a boundary correction to trust.
        ad['was_cut'] = False
        ad['held_for_review'] = True
        ad['hold_reason'] = HOLD_REASON_REVIEWER_CONTRADICTION
        ad['source'] = 'reviewer'
        ad['reviewer_contradiction'] = True
        # Preserve the reviewer's proposed trim so the review UI can offer
        # approving the trimmed span instead of all-or-nothing. Adjust
        # verdicts carry adjusted_* natively; confirmed verdicts carry them
        # only when the trim-recovery follow-up call reconstructed the
        # sub-span from the reasoning text (ad_reviewer._recover_contradiction_trim).
        if (v.adjusted_start is not None
                and v.adjusted_end is not None):
            ad['reviewer_proposed_start'] = v.adjusted_start
            ad['reviewer_proposed_end'] = v.adjusted_end
        return
    if v.verdict == 'adjust':
        ad['reviewer_moved'] = True
        ad['reviewer_original_start'] = v.original_start
        ad['reviewer_original_end'] = v.original_end
        invalidate_tail_provenance(ad, v.adjusted_end)
        ad['start'] = v.adjusted_start
        ad['end'] = v.adjusted_end
        set_reviewer_locks(ad, v.locked_edges)
        clip_merge_spans(ad, v.adjusted_start, v.adjusted_end)
    elif v.verdict == 'reject':
        ad['was_cut'] = False
        ad['source'] = 'reviewer'
        # Evidence floor (ad_reviewer.reject_hold_evidence): a reject over
        # measured evidence is a disagreement a human settles, not a drop.
        if v.reject_hold_reason:
            ad['held_for_review'] = True
            ad['hold_reason'] = v.reject_hold_reason
    elif v.verdict == 'resurrect':
        ad['was_cut'] = True
        ad['source'] = 'reviewer'


def _closest_unclaimed_marker(markers, start, end, claimed):
    """The marker nearest [start, end] within bounds tolerance that no verdict
    has claimed yet. Two snapped twins sit inside one tolerance window, so the
    first match is not necessarily the right one."""
    matches = [m for m in markers
               if id(m) not in claimed
               and spans_match(m.get('start'), m.get('end'), start, end)]
    if not matches:
        return None
    return min(matches, key=lambda m: (abs(m['start'] - start)
                                       + abs(m['end'] - end)))


def _merge_reviewer_result(result, all_ads_with_validation):
    """Apply reviewer verdicts to the master ad list and append any newly
    resurrected ads. Mutates ``all_ads_with_validation`` in place.
    """
    # Index by (start, end) so the verdict loop is O(V), not O(V*N).
    master_by_key = {(a.get('start'), a.get('end')): a for a in all_ads_with_validation}
    claimed = set()
    pairs = []
    unmatched = []
    # Exact keys first whatever the verdict order: a tolerance-only verdict
    # sits in the same window and would take the marker an exact key owns.
    for v in result.verdicts:
        ad = master_by_key.get((v.original_start, v.original_end))
        if ad is None or id(ad) in claimed:
            unmatched.append(v)
            continue
        claimed.add(id(ad))
        pairs.append((v, ad))
    for v in unmatched:
        # A boundary snap between the review call and here shifts the key by a
        # fraction of a second; matching on tolerance keeps the verdict (a
        # reject hold especially) attached to its marker.
        ad = _closest_unclaimed_marker(
            all_ads_with_validation, v.original_start, v.original_end, claimed)
        if ad is None:
            continue
        claimed.add(id(ad))
        pairs.append((v, ad))

    # Applied only once every claim is settled: an adjust verdict moves a
    # marker's bounds, and the tolerance pass reads those bounds live.
    for v, ad in pairs:
        _apply_reviewer_verdict_to_ad(ad, v)

    for ad in result.resurrected:
        key = (ad.get('start'), ad.get('end'))
        if key not in master_by_key:
            all_ads_with_validation.append(ad)
            master_by_key[key] = ad


def _pass1_user_rejects(fp_corrections, trim_ranges):
    """User FP rejections plus saved trim exclusions, original time."""
    return [*(fp_corrections or []), *trim_ranges]


def _run_ad_reviewer(slug, episode_id, podcast_id, ads_to_remove,
                     all_ads_with_validation, segments, podcast_name,
                     episode_title, episode_description, podcast_description,
                     min_cut_confidence, pass_num, pass_model, pass_provider=None,
                     audio_analysis=None, cue_gate_enabled=False,
                     segment_actions=None, keep_ads=(), user_rejects=()):
    """Run the LLM ad reviewer over the cut list and resurrection-eligible
    rejects. Returns updated ``(ads_to_remove, all_ads_with_validation)``.

    ``keep_ads`` and ``user_rejects`` are the hard barriers the reviewer may not cross.

    A reviewer failure holds unsupported cuts as reviewer_failed. Skips
    entirely when ``enable_ad_review`` is false.

    ``cue_gate_enabled``: when True, resurrection is suppressed. A resurrected
    non-held reject would become a cue-less auto-cut, violating the gate's
    guarantee.
    """
    clear_fallback(episode_id, PASS_REVIEWER_1 if pass_num == 1 else PASS_REVIEWER_2)

    if not _ad_review_enabled(db):
        return ads_to_remove, all_ads_with_validation

    if cue_gate_enabled:
        eligible = []
    else:
        eligible = split_resurrection_pool(
            all_ads_with_validation, ads_to_remove, min_cut_confidence
        )
    if not ads_to_remove and not eligible:
        return ads_to_remove, all_ads_with_validation

    common._publish_status('update_job_stage', slug, episode_id,
                    f"pass{pass_num}:reviewing", 75)

    audio_logger.info(
        f"[{slug}:{episode_id}] Reviewer pass {pass_num}: "
        f"{len(ads_to_remove)} accepted + {len(eligible)} resurrection-eligible"
    )

    reviewer = _build_reviewer(db, ad_detector)
    episode_meta = _build_episode_meta(
        slug, episode_id, podcast_id, podcast_name,
        episode_title, podcast_description, episode_description,
        audio_analysis=audio_analysis,
        effective_category_actions=segment_actions,
        hard_barriers=[*keep_ads, *user_rejects],
        min_cut_confidence=min_cut_confidence,
        protected_spans=_labelled_protected_spans(keep_ads, user_rejects),
    )
    result = reviewer.review(
        accepted_ads=ads_to_remove,
        resurrection_eligible=eligible,
        segments=segments,
        episode_meta=episode_meta,
        pass_num=pass_num,
        pass_model=pass_model,
        pass_provider=pass_provider,
    )

    new_ads_to_remove = list(result.accepted_after_review)

    # Merge reviewer fields into the master list (in-place), and pull in any
    # resurrected ads that weren't there before. Index by (start, end) so the
    # verdict loop is O(V), not O(V*N).
    _merge_reviewer_result(result, all_ads_with_validation)

    _log_reviewer_verdicts(slug, episode_id, pass_num, result.verdicts)

    # Persist the reviewer's mutations. The downstream save in process_episode
    # is gated on v_ads_for_ui being non-empty, so a pass-2 reviewer that
    # rejects everything will skip that save and lose pass-1 reviewer fields.
    storage.save_combined_ads(slug, episode_id, all_ads_with_validation)

    return new_ads_to_remove, all_ads_with_validation
