"""Build per-cue detection telemetry from a finished first-pass (#350 follow-up).

Pure function over the final ad list and the audio analysis result. For every
template cue the matcher surfaced it records the match score and how detection
used the cue: ``pair`` (it bracketed a synthesized ad), ``snap`` (it moved an ad
edge), or ``none`` (it was surfaced but did not affect the cut). Spectral cues
are not recorded -- they carry no template identity to review or promote.

Phase 6 adds three diagnostics that make an ``outcome='none'`` cut explainable:
sub-threshold ``below_threshold`` advisory rows (from the matcher's near-miss
list), ``edge_distance_s`` (signed distance from an above-threshold cue to the
nearest pre-snap LLM ad edge on its eligible side), and ``unused_reason`` (a
taxonomy for why a surfaced cue did nothing). None of these ever changes a cut.
"""
from typing import Dict, List, Optional

from config import (
    AUDIO_CUE_SOURCE_TEMPLATE,
    AUDIO_CUE_SNAP_CONFIDENCE,
    AUDIO_CUE_ROLE_DEFAULT,
    AUDIO_CUE_ROLE_NON_AD,
    AUDIO_CUE_START_EDGE_ROLES,
    AUDIO_CUE_END_EDGE_ROLES,
    is_template_cue,
)
from ad_detector.cue_boundary_snap import (
    DEFAULT_SNAP_LEAD_SECONDS,
    DEFAULT_SNAP_LAG_SECONDS,
)


def cue_key(template_id, start):
    """Stable (template_id, start) key for matching a cue to a pair/snap record.

    Rounds to 3 decimals -- the same precision cue_pair/cue_snap store cue_start
    at -- so the match is exact and two near-adjacent cues of the same template
    do not collapse into one key.
    """
    try:
        return (template_id, round(float(start), 3))
    except (TypeError, ValueError):
        return (template_id, None)


_cue_key = cue_key


def build_cue_detection_records(
    ads: List[Dict],
    audio_analysis_result,
    pre_snap_ads: Optional[List[Dict]] = None,
    pair_skip_diagnostics: Optional[Dict] = None,
    snap_confidence: float = AUDIO_CUE_SNAP_CONFIDENCE,
    snap_lead_s: float = DEFAULT_SNAP_LEAD_SECONDS,
    snap_lag_s: float = DEFAULT_SNAP_LAG_SECONDS,
) -> List[Dict]:
    """Return one telemetry record per template cue in ``audio_analysis_result``.

    Args:
        ads: The live ad list after cue-pair synthesis and boundary snap.
        audio_analysis_result: analyzer result; ``None`` yields no records.
        pre_snap_ads: The LLM ad list BEFORE boundary snap (edge_distance is
            measured against these). Falls back to ``ads`` when omitted.
        pair_skip_diagnostics: (template_id, round(start,3)) -> pair-skip reason
            from ``synthesize_ads_from_cue_pairs``; used for unused_reason when
            cue-pair synthesis ran.
        snap_confidence: The live snap confidence floor.
        snap_lead_s / snap_lag_s: The live snap window either side of an ad edge.
    """
    if not audio_analysis_result:
        return []
    try:
        cues = audio_analysis_result.get_signals_by_type('audio_cue')
    except AttributeError:
        return []

    pre_snap_ads = pre_snap_ads if pre_snap_ads is not None else (ads or [])
    pair_skip_diagnostics = pair_skip_diagnostics or {}
    ad_starts = sorted(_edge_floats(pre_snap_ads, 'start'))
    ad_ends = sorted(_edge_floats(pre_snap_ads, 'end'))
    ad_spans = _ad_spans(pre_snap_ads)

    paired = set()
    snapped = set()
    for ad in ads or []:
        for side in ('start', 'end'):
            sub = (ad.get('cue_pair') or {}).get(side)
            if sub:
                paired.add(_cue_key(sub.get('template_id'), sub.get('cue_start')))
            snap = (ad.get('cue_snap') or {}).get(side)
            if snap:
                snapped.add(_cue_key(snap.get('template_id'), snap.get('cue_start')))

    records = []
    # Above-threshold template cues: real signals the pipeline could act on.
    for c in cues:
        details = c.details or {}
        if not is_template_cue(details):
            continue
        role = details.get('role', AUDIO_CUE_ROLE_DEFAULT)
        key = _cue_key(details.get('template_id'), c.start)
        if key in paired:
            outcome = 'pair'
        elif key in snapped:
            outcome = 'snap'
        else:
            outcome = 'none'
        edge_distance = _edge_distance(c.start, c.end, role, ad_starts, ad_ends)
        unused_reason = None
        if outcome == 'none':
            unused_reason = _unused_reason(
                c, role, ad_spans, edge_distance, snap_confidence,
                snap_lead_s, snap_lag_s, pair_skip_diagnostics,
                details.get('template_id'),
            )
        records.append({
            'template_id': details.get('template_id'),
            'label': details.get('label'),
            'cue_type': details.get('cue_type'),
            'role': role,
            'source': AUDIO_CUE_SOURCE_TEMPLATE,
            'start_s': round(float(c.start), 3),
            'end_s': round(float(c.end), 3),
            'match_score': _as_float(details.get('score')),
            'confidence': round(float(c.confidence), 3),
            'outcome': outcome,
            'edge_distance_s': edge_distance,
            'unused_reason': unused_reason,
        })

    # Sub-threshold near-misses: advisory rows, never signals. They carry no
    # outcome beyond 'below_threshold' and no edge_distance/unused_reason -- the
    # pipeline never saw them, so there is nothing to explain.
    for nm in getattr(audio_analysis_result, 'cue_near_misses', None) or []:
        records.append({
            'template_id': nm.get('template_id'),
            'label': nm.get('label'),
            'cue_type': nm.get('cue_type'),
            'role': nm.get('role'),
            'source': AUDIO_CUE_SOURCE_TEMPLATE,
            'start_s': round(float(nm['start_s']), 3),
            'end_s': round(float(nm['end_s']), 3),
            'match_score': _as_float(nm.get('score')),
            'confidence': None,
            'outcome': 'below_threshold',
            'edge_distance_s': None,
            'unused_reason': None,
        })
    return records


def _edge_distance(cue_start, cue_end, role, ad_starts, ad_ends):
    """Signed distance from a cue to the nearest pre-snap LLM ad edge.

    start-role cues can only move an ad START, so they are measured from the
    cue's END to the nearest ad start; end-role cues from the cue's START to the
    nearest ad end; boundary cues take whichever of the two is closer; non_ad
    cues never move an edge, so they carry no distance. Sign is edge - cue, so
    a positive value means the ad edge sits after the cue.
    """
    if role == AUDIO_CUE_ROLE_NON_AD:
        return None
    candidates = []
    if role in AUDIO_CUE_START_EDGE_ROLES:
        d = _nearest_signed(ad_starts, float(cue_end))
        if d is not None:
            candidates.append(d)
    if role in AUDIO_CUE_END_EDGE_ROLES:
        d = _nearest_signed(ad_ends, float(cue_start))
        if d is not None:
            candidates.append(d)
    if not candidates:
        return None
    return round(min(candidates, key=abs), 3)


def _nearest_signed(edges, ref):
    """Signed (edge - ref) distance to the nearest edge, or None if no edges."""
    if not edges:
        return None
    return min((e - ref for e in edges), key=abs)


def _unused_reason(cue, role, ad_spans, edge_distance, snap_confidence,
                   snap_lead_s, snap_lag_s, pair_skip_diagnostics, template_id):
    """Explain why an above-threshold cue with outcome='none' did nothing.

    Precedence (most-defining first): a non_ad cue is 'advisory_role' wherever it
    sits; a cue inside an LLM ad span is 'covered'; a cue below the snap
    confidence floor is 'below_snap_confidence'. When cue-pair synthesis ran and
    recorded why this cue did not pair, that specific reason wins next -- it is
    the authoritative pairing explanation. Otherwise an eligible in-confidence
    cue whose nearest edge is beyond the live snap window is 'out_of_reach', and
    anything left is 'unpaired'.
    """
    if role == AUDIO_CUE_ROLE_NON_AD:
        return 'advisory_role'
    if cue.confidence < snap_confidence:
        return 'below_snap_confidence'
    pair_reason = pair_skip_diagnostics.get(_cue_key(template_id, cue.start))
    if pair_reason:
        return pair_reason
    # Role-aware reach check mirrors _pick_cue_for_start/_pick_cue_for_end.
    # sign convention: d = edge - cue_ref (from _nearest_signed).
    #   start-role: cue.end vs ad_start -> reachable iff d in [-lag, +lead]
    #   end-role:   cue.start vs ad_end -> reachable iff d in [-lead, +lag]
    #   boundary:   either window suffices (picks whichever edge is closer).
    # Reach is checked before 'covered' so that a directional cue outside its
    # snap window reports out_of_reach even when it happens to fall inside the
    # LLM span.  Boundary cues keep 'covered' first because a span-interior
    # boundary cue has no un-covered edge to act on regardless of distance.
    in_reach = edge_distance is not None and _in_reach(
        role, edge_distance, snap_lead_s, snap_lag_s
    )
    is_directional = (
        role in AUDIO_CUE_START_EDGE_ROLES) != (role in AUDIO_CUE_END_EDGE_ROLES
    )
    if is_directional and not in_reach:
        return 'out_of_reach'
    if _inside_any_span(cue.start, cue.end, ad_spans):
        return 'covered'
    if not in_reach:
        return 'out_of_reach'
    return 'unpaired'


def _in_reach(role, edge_distance, snap_lead_s, snap_lag_s):
    """True if edge_distance falls within the role-specific snap window.

    Mirrors _pick_cue_for_start and _pick_cue_for_end from cue_boundary_snap:
      start-role: cue.end in [ad_start-lead, ad_start+lag]  -> d in [-lag, +lead]
      end-role:   cue.start in [ad_end-lag, ad_end+lead]    -> d in [-lead, +lag]
      boundary:   either side is sufficient.
    """
    d = edge_distance
    start_ok = -snap_lag_s <= d <= snap_lead_s
    end_ok = -snap_lead_s <= d <= snap_lag_s
    if role in AUDIO_CUE_START_EDGE_ROLES and role not in AUDIO_CUE_END_EDGE_ROLES:
        return start_ok
    if role in AUDIO_CUE_END_EDGE_ROLES and role not in AUDIO_CUE_START_EDGE_ROLES:
        return end_ok
    # boundary or unknown: reachable if either window applies
    return start_ok or end_ok


def _inside_any_span(start, end, ad_spans):
    """True if the cue midpoint sits inside any LLM ad span."""
    mid = (float(start) + float(end)) / 2.0
    for a_start, a_end in ad_spans:
        if a_start <= mid <= a_end:
            return True
    return False


def _edge_floats(ads, key):
    """All float values for ``key`` across ``ads`` that parse cleanly."""
    out = []
    for ad in ads or []:
        try:
            out.append(float(ad[key]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _ad_spans(ads):
    """List of (start, end) float spans for ``ads`` that parse cleanly."""
    spans = []
    for ad in ads or []:
        try:
            spans.append((float(ad['start']), float(ad['end'])))
        except (KeyError, TypeError, ValueError):
            continue
    return spans


def _as_float(value) -> Optional[float]:
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None
