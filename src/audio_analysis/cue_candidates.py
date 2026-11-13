"""Merge cue-template candidates: within-episode recurrence + cross-episode intro/outro.

Pure (no Flask/DB imports) so the candidate-scan worker and unit tests use it
directly.
"""
import numpy as np

from audio_fingerprinter import enumerate_window_occurrences
from config import (
    AUDIO_CUE_TYPE_SHOW_INTRO, AUDIO_CUE_TYPE_SHOW_OUTRO,
    AUDIO_CUE_TYPE_CONTENT_TRANSITION,
)
from utils.time import ranges_overlap

_SUGGESTED_TYPE = {
    'intro': AUDIO_CUE_TYPE_SHOW_INTRO,
    'outro': AUDIO_CUE_TYPE_SHOW_OUTRO,
}


def count_ad_boundary_hits(occurrences, ad_spans, tolerance_s):
    """Count occurrence positions within ``tolerance_s`` of any ad start/end.

    Returns ``(hits, start_hits, end_hits)``. An occurrence near both boundaries is one hit but
    neither start- nor end-only, so the phase fractions stay honest.
    """
    hits = start_hits = end_hits = 0
    for occ in occurrences:
        near_start = any(abs(occ - span['start']) <= tolerance_s for span in ad_spans)
        near_end = any(abs(occ - span['end']) <= tolerance_s for span in ad_spans)
        if near_start or near_end:
            hits += 1
            if near_start and not near_end:
                start_hits += 1
            elif near_end and not near_start:
                end_hits += 1
    return hits, start_hits, end_hits


def annotate_recurring_with_ad_affinity(
    recurring,
    ad_spans,
    *,
    tolerance_s,
    min_fraction,
    phase_fraction,
):
    """Type and re-rank recurring candidates using ad-boundary history.

    affinity = hits / count, where a hit is an occurrence within tolerance_s of any ad start or
    end. Typing needs non-empty ad_spans, affinity >= min_fraction and hits >= 2; phase (start,
    end, boundary) comes from the fraction of start-only hits. Sets adBoundaryHits and
    boundaryAffinity, strips the internal occurrences field and re-sorts by (-affinity, -count).
    Empty ad_spans leaves suggestedType None. Past cuts may have snapped to these same cues, so
    this has a mild self-confirmation bias.
    """
    if not ad_spans:
        for c in recurring:
            c.pop('occurrences', None)
            c['adBoundaryHits'] = None
            c['boundaryAffinity'] = None
            c['affinitySource'] = None
            c['suggestedType'] = None
        return recurring

    for c in recurring:
        occurrences = c.pop('occurrences', None) or []
        count = c.get('count') or len(occurrences)
        if not occurrences or count == 0:
            c['adBoundaryHits'] = None
            c['boundaryAffinity'] = None
            c['affinitySource'] = None
            continue
        hits, start_hits, end_hits = count_ad_boundary_hits(
            occurrences, ad_spans, tolerance_s)
        affinity = hits / count
        c['adBoundaryHits'] = hits
        c['boundaryAffinity'] = round(affinity, 3)
        c['affinitySource'] = None  # caller sets 'episode' or 'siblings'
        if hits >= 2 and affinity >= min_fraction:
            start_fraction = start_hits / hits if hits > 0 else 0
            end_fraction = end_hits / hits if hits > 0 else 0
            if start_fraction >= phase_fraction:
                c['suggestedType'] = 'ad_break_start'
            elif end_fraction >= phase_fraction:
                c['suggestedType'] = 'ad_break_end'
            else:
                c['suggestedType'] = 'ad_break_boundary'
        else:
            c['suggestedType'] = AUDIO_CUE_TYPE_CONTENT_TRANSITION

    recurring.sort(key=lambda c: (-(c.get('boundaryAffinity') or 0), -(c.get('count') or 0)))
    return recurring


def merge_cue_candidates(recurring, cross_episode, templated_spans=()):
    """Combine within-episode recurrence hits with cross-episode intro/outro hits.

    ``recurring`` items are ``{start,end,count}``; ``cross_episode`` items are
    ``{start,end,kind:'intro'|'outro',episodeMatches}``. ``templated_spans`` is
    ``[(start,end), ...]`` of cues already captured as templates; overlapping candidates are
    dropped so the scan only surfaces new cues. Returns non-overlapping typed candidates with
    ``suggestedType``: cross-episode hits are considered first, then recurring, and anything
    overlapping a kept candidate is dropped.
    """
    def _templated(start, end):
        return any(ranges_overlap(start, end, s, e) for s, e in templated_spans)

    merged = []

    def _keep(start, end, item):
        if _templated(start, end):
            return
        if any(ranges_overlap(start, end, m['start'], m['end']) for m in merged):
            return
        merged.append(item)

    for c in cross_episode:
        _keep(c['start'], c['end'], {
            'start': c['start'], 'end': c['end'], 'kind': c['kind'],
            'episodeMatches': c.get('episodeMatches'),
            'suggestedType': _SUGGESTED_TYPE.get(c['kind']),
        })
    for c in recurring:
        _keep(c['start'], c['end'], {
            'start': c['start'], 'end': c['end'], 'kind': 'recurring',
            'count': c.get('count'),
            'suggestedType': c.get('suggestedType'),
            'adBoundaryHits': c.get('adBoundaryHits'),
            'boundaryAffinity': c.get('boundaryAffinity'),
            'affinitySource': c.get('affinitySource'),
        })
    return merged


def mark_dismissed_candidates(candidates, dismissals, target_fp, similarity):
    """Stamp dismissed/dismissalId on candidates matching a dismissed sound; returns the count marked.

    Each dismissal's raw fingerprint ints are located in the episode's full fingerprint
    (``target_fp``). Candidates are marked in place, not removed, so cached scans stay reconcilable.
    """
    raw_ints, duration = target_fp
    ep_arr = np.asarray(raw_ints, dtype=np.uint32)
    marked = 0
    for d in dismissals:
        window = np.asarray(d.get('raw_ints') or [], dtype=np.uint32)
        if window.size == 0:
            continue
        occurrences = enumerate_window_occurrences(
            window, ep_arr, duration, similarity)
        if not occurrences:
            continue
        for c in candidates:
            if c.get('dismissed'):
                continue
            if any(ranges_overlap(c['start'], c['end'], occ_s, occ_e)
                   for occ_s, occ_e in occurrences):
                c['dismissed'] = True
                c['dismissalId'] = d['id']
                marked += 1
        if marked == len(candidates):
            break
    return marked
