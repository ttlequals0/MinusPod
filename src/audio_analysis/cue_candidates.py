"""Merge cue-template candidates: within-episode recurrence + cross-episode intro/outro.

Pure -- no Flask/DB imports -- so the candidate-scan worker and unit tests use it
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

    Returns ``(hits, start_hits, end_hits)``. An occurrence near both a start
    and an end boundary counts as one hit but as neither start- nor end-only,
    so the phase fractions stay honest. Shared by the within-episode annotator
    and the sibling-fallback path so "hit" means the same thing in both.
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

    For each candidate, count how many of its occurrences land within
    tolerance_s of any ad start or end boundary (one hit per occurrence even
    if near both). affinity = hits / count. Typing requires ad_spans non-empty,
    affinity >= min_fraction, and hits >= 2. Phase classification (start vs end
    vs boundary) uses the fraction of start-only hits among all hits.

    Annotates adBoundaryHits and boundaryAffinity on each candidate, strips
    occurrences (internal field), and re-sorts by (-affinity, -count).

    No ad history (empty ad_spans) -> suggestedType=None unchanged.

    Note: past cuts may have been snapped to these same cue sounds, so this
    annotator has a mild self-confirmation bias -- acceptable given the
    alternative is no typing at all.
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

    ``recurring`` items are ``{start,end,count}`` (ad-break stings that repeat
    within the episode). ``cross_episode`` items are
    ``{start,end,kind:'intro'|'outro',episodeMatches}`` (segments that recur across
    sibling episodes). ``templated_spans`` is ``[(start,end), ...]`` of cues already
    captured as active templates (matched on this episode); a candidate overlapping
    one is dropped so the scan only surfaces NEW cues. Returns typed candidates
    carrying a ``suggestedType`` so the capture tool preselects the cue type.
    The result is non-overlapping: cross-episode hits are considered first
    (high-value, typed), then recurring (already in descending recurrence order);
    any candidate overlapping one already kept -- a long shared segment can yield
    several near-duplicate runs -- is dropped.
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
    """Stamp dismissed/dismissalId on candidates that match a dismissed sound.

    ``dismissals`` carry decoded raw fingerprint ints; each is located in the
    episode's full fingerprint (``target_fp``) and any candidate whose span
    overlaps an occurrence is marked in place. Candidates are kept, not
    dropped, so the UI can render them in a collapsed group. Returns the
    number of candidates marked.
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
                # first-match-wins: already stamped by an earlier dismissal
                continue
            if any(ranges_overlap(c['start'], c['end'], occ_s, occ_e)
                   for occ_s, occ_e in occurrences):
                c['dismissed'] = True
                c['dismissalId'] = d['id']
                marked += 1
    return marked
