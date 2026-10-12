"""Cut carving, hold carving, terminal snaps and final cut state."""

import logging
from ad_detector import extend_ad_boundaries_by_content

from ad_detector.boundaries import (
    snap_extended_ad_tails_to_splice,
    snap_terminal_ad_to_splice,
    transition_pair_silence_events,
)

from audio_processor import get_replacement_duration
from utils.markers import (EDGE_TOLERANCE, carve_fragment,
                           ensure_hold_id,
                           carve_partly_cut, is_carved,
                           clip_merge_spans, merge_runs, reviewer_edge_locked, subtract_spans)

from utils.time import (
    merge_cut_spans, overlap_ratio, overlap_seconds,
    ranges_overlap,
)

from config import (
    MIN_AD_DURATION, MIN_AD_DURATION_FOR_REMOVAL,
    CORRECTION_MATCH_MIN_COVERAGE,
    is_pending_review, resolve_silence_snap_tunables,
    TERMINAL_SNAP_WINDOW_SECONDS,
)

from splice_calibration import SPLICE_EVENTS_CALIBRATED_STATUSES

from main_app.pipeline import review
from main_app.pipeline.shared import (db, storage)

audio_logger = logging.getLogger('podcast.audio')


def _find_master(all_ads, ad):
    """Return ``ad``'s entry in the master list, matched by identity or by
    (start, end) span (reviewer adjustments rebuild dicts, so identity alone
    is not enough). None when nothing matches."""
    for master in all_ads:
        if master is ad:
            return master
    for master in all_ads:
        if master.get('start') == ad.get('start') and master.get('end') == ad.get('end'):
            return master
    return None


def _cut_groups(cuts):
    """Rendered cuts merged into contiguous [start, end] runs."""
    return merge_runs([(c['start'], c['end']) for c in cuts], gap=EDGE_TOLERANCE)


def _clamped_span(marker, duration):
    start = max(0.0, marker['start'])
    return start, (min(marker['end'], duration) if duration else marker['end'])


def _covering_group(groups, marker, duration):
    start, end = _clamped_span(marker, duration)
    return next((g for g in groups
                 if g[0] - EDGE_TOLERANCE <= start and end <= g[1] + EDGE_TOLERANCE), None)


def _unrendered_hold(marker):
    """A hold the render must not cut into (not is_pending_review, which counts a fresh hold as cut)."""
    return bool(marker.get('held_for_review') and not marker.get('was_cut'))


def _keep_stamps_inside(piece):
    """Drop a hold piece's pass-2 approval stamps whose span lies outside it."""
    def inside(span):
        return (span['start'] >= piece['start'] - EDGE_TOLERANCE
                and span['end'] <= piece['end'] + EDGE_TOLERANCE)
    released = [s for s in piece.get('pass2_released_spans') or [] if inside(s)]
    if released:
        piece['pass2_released_spans'] = released
    else:
        piece.pop('pass2_released_spans', None)
    for key in ('pass2_reviewed_release', 'pass2_corroborated_span'):
        if piece.get(key) and not inside(piece[key]):
            piece.pop(key)
            if released:
                piece[key] = dict(released[0])
    if not piece.get('pass2_corroborated_span'):
        piece.pop('pass2_corroborated', None)
    return piece


def _carve_holds_against_cuts(all_ads, groups, skip_ids, tag=''):
    """Shrink pending holds to what the applied cut groups leave; the render cut the rest."""
    replaced = {}
    for m in all_ads:
        if (not _unrendered_hold(m) or id(m) in skip_ids
                or not any(overlap_seconds(m['start'], m['end'], a, b) > EDGE_TOLERANCE
                           for a, b in groups)):
            continue
        ensure_hold_id(m)
        rest = [(a, b) for a, b in subtract_spans([(m['start'], m['end'])], groups)
                if b - a > EDGE_TOLERANCE]
        # A remainder under MIN_AD_DURATION stays uncut without a marker, as validator remainders do.
        pieces = [_keep_stamps_inside(carve_fragment(m, a, b))
                  for a, b in rest if b - a >= MIN_AD_DURATION]
        dropped = [f"{a:.1f}s-{b:.1f}s" for a, b in rest if b - a < MIN_AD_DURATION]
        replaced[id(m)] = pieces
        kept = ', '.join(f"{p['start']:.1f}s-{p['end']:.1f}s" for p in pieces)
        audio_logger.info(
            f"{tag} Hold {m['start']:.1f}s-{m['end']:.1f}s "
            + (f"shrinks to {kept}" if pieces else "is removed")
            + ": the applied cuts took the rest"
            + (f"; short remainder {', '.join(dropped)} left uncut" if dropped else ""))
    if replaced:
        all_ads[:] = [p for m in all_ads for p in replaced.get(id(m), [m])]


def _finalize_cut_state(all_ads, ads_to_remove, applied_cuts, duration, tag=''):
    """Set was_cut from the rendered cuts, carving partly cut markers; returns the cut groups."""
    groups = _cut_groups(applied_cuts)
    masters = [_find_master(all_ads, ad) for ad in ads_to_remove]
    requested = {id(ad) for ad in [*ads_to_remove, *masters] if ad is not None}
    # Pending holds only bar merges in the render, so a cut overlapping one takes its audio.
    _carve_holds_against_cuts(all_ads, groups, requested, tag)
    markers = {id(m): m for m in [*all_ads, *ads_to_remove]}
    carved = {}
    for marker in markers.values():
        is_requested = id(marker) in requested
        # A pass-1 remainder is re-carved when a later pass cuts part of it.
        eligible = is_requested or is_carved(marker)
        marker['was_cut'] = (eligible
                             and _covering_group(groups, marker, duration) is not None)
        # Holds stamped outside the validator get their identity before anything can file it.
        if is_pending_review(marker):
            ensure_hold_id(marker)
        if not eligible or marker['was_cut']:
            continue
        start, end = _clamped_span(marker, duration)
        fragments = carve_partly_cut(marker, start, end, [
            (max(start, g[0]), min(end, g[1])) for g in groups])
        if not fragments:
            if not is_requested:
                continue
            audio_logger.info(
                f"{tag} Marker {marker['start']:.1f}s-{marker['end']:.1f}s is not fully "
                f"inside the rendered cuts; marking as not cut")
            continue
        carved[id(marker)] = fragments
        n_cut = sum(1 for f in fragments if f['was_cut'])
        audio_logger.info(
            f"{tag} Marker {marker['start']:.1f}s-{marker['end']:.1f}s partly rendered; "
            f"carved into {n_cut} cut and {len(fragments) - n_cut} uncut fragment(s)")
    if carved:
        # Mutated in place: pass 1's ads_to_remove list reaches the final call.
        all_ads[:] = [f for m in all_ads for f in carved.get(id(m), [m])]
        ads_to_remove[:] = [f for m in ads_to_remove
                            for f in carved.get(id(m), [m]) if f['was_cut'] or id(m) not in carved]
    return groups


def _rendered_cuts_covering(groups, markers, duration):
    """Number of rendered cut groups holding at least one cut marker from ``markers``."""
    hit = {id(group) for m in markers if m.get('was_cut')
           and (group := _covering_group(groups, m, duration)) is not None}
    return len(hit)


def _record_cut_seconds(run_stats, cuts, original_duration, new_duration):
    """Store net seconds_removed plus the source and replacement seconds behind it."""
    if run_stats is None:
        return
    source, replacement = _cut_seconds(cuts)
    run_stats['source_seconds_removed'] = source
    run_stats['replacement_seconds_added'] = replacement
    if original_duration and new_duration:
        run_stats['seconds_removed'] = round(original_duration - new_duration, 2)
    else:
        # Duration probe failed; estimate net removed from source minus replacement.
        run_stats['seconds_removed'] = round(source - replacement, 2)


def _cut_seconds(cuts):
    """(source seconds removed, replacement seconds added) for rendered cuts."""
    groups = merge_cut_spans(cuts, default_replacement=get_replacement_duration())
    return (round(sum(end - start for start, end, *_ in groups), 2),
            round(sum(replacement for *_, replacement in groups), 2))


def _carve_cuts_around(ads_to_remove, all_ads, ranges, *, user_trim=False, tag=''):
    """Split cuts around ranges in original time, replacing their masters in all_ads.

    user_trim fragments drop the confirm and skip learning; else short untrusted ones go."""
    barriers = [(start, end) for start, end, *_ in merge_cut_spans(ranges)]
    if not barriers:
        return ads_to_remove
    carved = []
    for ad in ads_to_remove:
        whole = (ad['start'], ad['end'])
        spans = subtract_spans([whole], barriers)
        if spans == [whole]:
            carved.append(ad)
            continue
        master = _find_master(all_ads, ad)
        if user_trim:
            pieces = [carve_fragment(ad, start, end) for start, end in spans]
            for piece in pieces:
                piece['_skip_pattern_learning'] = True
                piece['validation'] = dict(ad.get('validation') or {})
                piece['validation'].pop('confirmed_span', None)
                piece['validation'].pop('user_confirmed', None)
            # The saved list holds the cut fragments themselves.
            master_pieces = pieces
            if master is None:
                all_ads.extend(pieces)
        else:
            trusted = (ad.get('_measured_split_fragment')
                       or ad['end'] - ad['start'] >= MIN_AD_DURATION_FOR_REMOVAL)
            spans = [(start, end) for start, end in spans
                     if trusted or end - start >= MIN_AD_DURATION]
            audio_logger.info(
                f"{tag} Pass 1 cut {ad['start']:.1f}s-{ad['end']:.1f}s "
                f"split around kept audio into {len(spans)} removable fragment(s)")
            pieces = [carve_fragment(ad, start, end) for start, end in spans]
            master_pieces = ([carve_fragment(master, start, end) for start, end in spans]
                             if master is not None else [])
            # Saved fragments carry the stamp too, so a recut keeps a short trusted piece cut.
            for piece in [*pieces, *master_pieces]:
                if trusted:
                    piece['_measured_split_fragment'] = True
        carved.extend(pieces)
        if master is None:
            continue
        index = next(i for i, marker in enumerate(all_ads) if marker is master)
        if spans:
            all_ads[index:index + 1] = master_pieces
        else:
            master['was_cut'] = False
    if user_trim:
        all_ads.sort(key=lambda ad: ad['start'])
    return carved


def _keep_locked_edges(before, after):
    """Undo any sweep result that widened a reviewer-locked edge."""
    return [old if ((new['start'] < old['start'] and reviewer_edge_locked(old, 'start'))
                    or (new['end'] > old['end'] and reviewer_edge_locked(old, 'end')))
            else new
            for old, new in zip(before, after, strict=True)]


def _snap_terminal_starts(slug, episode_id, ads_to_remove, all_ads_with_validation,
                          segments, audio_analysis_result, episode_duration,
                          podcast_name=None):
    """Pull a terminal cut's start back to the strongest splice event, leaving reviewer-locked starts alone."""
    if not ads_to_remove or not episode_duration:
        return ads_to_remove
    splice = getattr(audio_analysis_result, 'splice_evidence', None) or {}
    events = list(splice.get('events') or [])
    silence_tunables = getattr(
        audio_analysis_result, 'silence_tunables', None)
    if silence_tunables is None:
        silence_tunables = resolve_silence_snap_tunables(db)
    events.extend(transition_pair_silence_events(
        getattr(audio_analysis_result, 'signals', None) or [],
        getattr(audio_analysis_result, 'silence_spans', None) or [],
        max_distance_s=silence_tunables['max_distance_seconds'],
        min_silence_s=silence_tunables['min_duration_seconds'],
    ))
    if not events:
        return ads_to_remove
    window_s = db.get_setting_float('terminal_snap_window_seconds',
                                    TERMINAL_SNAP_WINDOW_SECONDS)
    # Only markers that will actually be removed may make speech safe to
    # cross. Rejected, held, and kept markers are explicit barriers, including
    # when their audio is untranscribed.
    coverage_ads = list(ads_to_remove)
    cut_marker_ids = {id(marker) for marker in coverage_ads}
    for marker in coverage_ads:
        master = _find_master(all_ads_with_validation, marker)
        if master is not None:
            cut_marker_ids.add(id(master))
    blocking_ads = [m for m in all_ads_with_validation
                    if id(m) not in cut_marker_ids and not m.get('was_cut')]
    snapped = snap_terminal_ad_to_splice(
        ads_to_remove, segments, events, episode_duration, window_s,
        coverage_ads=coverage_ads, blocking_ads=blocking_ads,
        podcast_name=podcast_name,
    )
    snapped = _keep_locked_edges(ads_to_remove, snapped)
    changed = False
    for old, new in zip(ads_to_remove, snapped, strict=True):
        if new['start'] >= old['start']:
            continue
        changed = True
        audio_logger.info(
            f"[{slug}:{episode_id}] Terminal snap: cut start "
            f"{old['start']:.1f}s -> {new['start']:.1f}s "
            f"(-{old['start'] - new['start']:.1f}s)"
        )
        master = _find_master(all_ads_with_validation, old)
        if master is not None:
            master['start'] = new['start']
            master['terminal_snap'] = new['terminal_snap']
    if not changed:
        return ads_to_remove
    storage.save_combined_ads(slug, episode_id, all_ads_with_validation)
    return snapped


def _complete_cut_tails(slug, episode_id, ads_to_remove, all_ads_with_validation,
                        segments, podcast_name=None):
    """Re-run content end extension after the reviewer, never past a reviewer-locked end."""
    if not ads_to_remove or not segments:
        return ads_to_remove
    if not review._ad_review_enabled(db):
        return ads_to_remove

    # barriers: never extend a cut into the next detected ad; overlapping
    # spans in combined_ads.json double-subtract in timestamp mapping.
    extended = extend_ad_boundaries_by_content(
        ads_to_remove, segments, extend_start=False, podcast_name=podcast_name,
        barriers=all_ads_with_validation,
    )
    extended = _keep_locked_edges(ads_to_remove, extended)

    changed = False
    for old, new in zip(ads_to_remove, extended, strict=True):
        if new['end'] <= old['end']:
            continue
        changed = True
        audio_logger.info(
            f"[{slug}:{episode_id}] Tail completion: cut end "
            f"{old['end']:.1f}s -> {new['end']:.1f}s "
            f"(+{new['end'] - old['end']:.1f}s, {new.get('sponsor', 'unknown')})"
        )
        master = _find_master(all_ads_with_validation, old)
        if master is not None:
            master['end'] = new['end']
            master['tail_completed'] = True
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


def _snap_completed_cut_tails_to_splice(
        slug, episode_id, ads_to_remove, all_ads_with_validation,
        segments, audio_analysis_result, podcast_name=None):
    """Extend a recovered spoken tail through a nearby untranscribed logo."""
    if not ads_to_remove:
        return ads_to_remove
    splice = getattr(audio_analysis_result, 'splice_evidence', None) or {}
    if not isinstance(splice, dict):
        return ads_to_remove
    calibration = splice.get('calibration') or {}
    if (not isinstance(calibration, dict)
            or calibration.get('status') not in SPLICE_EVENTS_CALIBRATED_STATUSES):
        # Cold-start splice events may corroborate another detector, but they
        # are not calibrated well enough to extend a destructive cut.
        return ads_to_remove
    events = splice.get('events') or []
    if not isinstance(events, list) or not events:
        return ads_to_remove

    snapped = snap_extended_ad_tails_to_splice(
        ads_to_remove, segments, events,
        coverage_ads=all_ads_with_validation,
        podcast_name=podcast_name,
    )
    snapped = _keep_locked_edges(ads_to_remove, snapped)
    changed = False
    for old, new in zip(ads_to_remove, snapped, strict=True):
        if new['end'] <= old['end']:
            continue
        changed = True
        audio_logger.info(
            f"[{slug}:{episode_id}] Tail splice snap: cut end "
            f"{old['end']:.1f}s -> {new['end']:.1f}s "
            f"(+{new['end'] - old['end']:.1f}s)"
        )
        master = _find_master(all_ads_with_validation, old)
        if master is not None:
            master['end'] = new['end']
            master['tail_splice_snap'] = new['tail_splice_snap']
        else:
            audio_logger.warning(
                f"[{slug}:{episode_id}] Tail splice snap: no master ad matched "
                f"{old['start']:.1f}s-{old['end']:.1f}s; UI list will not show "
                f"the extension"
            )

    if changed:
        storage.save_combined_ads(slug, episode_id, all_ads_with_validation)
    return snapped


def _finalize_user_confirmed_bounds(
        slug, episode_id, ads_to_remove, all_ads_with_validation,
        confirmed_corrections=None, episode_duration=None):
    """Make a human-approved trim the final authority before audio cutting.

    Validation normally applies ``confirmed_span`` already, but later pipeline
    stages rebuild marker dicts and mutate boundaries. Re-assert the approved
    span after every reviewer/tail operation and synchronize the master marker
    so the rendered cut and UI audit trail cannot diverge.
    """
    if not ads_to_remove:
        return ads_to_remove
    if confirmed_corrections is None:
        podcast = db.get_podcast_by_slug(slug)
        corrections = (db.get_confirmed_corrections(podcast['id'], episode_id)
                       if podcast else [])
    else:
        corrections = confirmed_corrections
    def matching_correction(marker):
        for corr in corrections or []:
            ratio = overlap_ratio(
                corr['start'], corr['end'], marker['start'], marker['end'])
            if ratio >= CORRECTION_MATCH_MIN_COVERAGE:
                return corr
        return None

    def approved_span(marker):
        validation = marker.get('validation') or {}
        if not validation.get('user_confirmed'):
            return None
        carried = validation.get('confirmed_span')
        if isinstance(carried, dict):
            return carried
        correction = matching_correction(marker)
        if correction is None:
            return None
        return correction.get('confirmed_span')

    def apply_span(marker, approved):
        target_start = max(0.0, float(approved['start']))
        target_end = float(approved['end'])
        if episode_duration is not None and episode_duration > 0:
            target_end = min(target_end, float(episode_duration))
        if target_end <= target_start:
            return False
        old_start, old_end = marker['start'], marker['end']
        missing = object()
        old_metadata = (
            marker.get('end_extended_by_content', missing),
            marker.get('tail_splice_snap', missing),
            marker.get('dai_core_spans', missing),
            marker.get('merged_member_spans', missing),
            marker.get('merged_protected_start'),
            marker.get('merged_protected_end'),
        )
        # Final human authority supersedes how an automatic tail happened to
        # reach even the same edge; do not let that stale provenance enable a
        # later expansion on reload.
        marker.pop('end_extended_by_content', None)
        marker.pop('tail_splice_snap', None)
        marker['start'], marker['end'] = target_start, target_end
        clip_merge_spans(marker, target_start, target_end)
        flags = (marker.get('validation') or {}).get('flags')
        note_added = False
        if isinstance(flags, list):
            note = 'INFO: Finalized to user-approved span'
            if note not in flags:
                flags.append(note)
                note_added = True
        new_metadata = (
            marker.get('end_extended_by_content', missing),
            marker.get('tail_splice_snap', missing),
            marker.get('dai_core_spans', missing),
            marker.get('merged_member_spans', missing),
            marker.get('merged_protected_start'),
            marker.get('merged_protected_end'),
        )
        return (
            (old_start, old_end) != (target_start, target_end)
            or old_metadata != new_metadata
            or note_added
        )

    changed = False
    for ad in ads_to_remove:
        approved = approved_span(ad)
        if approved is None:
            continue
        # Resolve the master before changing the cut-list key. A separate copy
        # may already have drifted, so fall back to the same carried approval.
        master = _find_master(all_ads_with_validation, ad)
        if master is None:
            master = next((
                candidate for candidate in all_ads_with_validation
                if approved_span(candidate) == approved
                and ranges_overlap(
                    candidate['start'], candidate['end'],
                    ad['start'], ad['end'])
            ), None)
        changed = apply_span(ad, approved) or changed
        if master is not None and master is not ad:
            changed = apply_span(master, approved) or changed

    if changed:
        storage.save_combined_ads(slug, episode_id, all_ads_with_validation)
    return ads_to_remove
