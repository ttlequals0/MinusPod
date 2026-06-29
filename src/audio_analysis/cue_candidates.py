"""Merge cue-template candidates: within-episode recurrence + cross-episode intro/outro.

Pure -- no Flask/DB imports -- so the candidate-scan worker and unit tests use it
directly.
"""
from config import AUDIO_CUE_TYPE_SHOW_INTRO, AUDIO_CUE_TYPE_SHOW_OUTRO
from utils.time import ranges_overlap

_SUGGESTED_TYPE = {
    'intro': AUDIO_CUE_TYPE_SHOW_INTRO,
    'outro': AUDIO_CUE_TYPE_SHOW_OUTRO,
}


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
            'count': c.get('count'), 'suggestedType': None,
        })
    return merged
