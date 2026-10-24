"""Emit a cut marker for each matched audio cue whose template cuts on its own (#874)."""
from __future__ import annotations

import logging

from config import (
    AUDIO_CUE_TYPE_DEFAULT,
    CUE_CUT_CATEGORY,
    CUE_MATCH_STAGE,
    cue_cuts_matches,
    is_template_cue,
)
from ad_detector.cue_pair_ads import (
    SKIP_BELOW_CONFIDENCE,
    SKIP_COVERED,
)
from ad_detector.cue_telemetry import cue_key as _diag_key

logger = logging.getLogger('podcast.claude.cue_cut')

def _overlaps_existing_ad(ads: list[dict], start: float, end: float) -> bool:
    return any(a['start'] < end and a['end'] > start for a in ads)


def synthesize_ads_from_cue_matches(
    ads: list[dict],
    audio_analysis_result,
    cue_settings_cut_matches: bool,
    min_confidence: float,
):
    """Return ``(ads, skip_diagnostics)``: input plus one marker per cutting cue, sorted."""
    skip_diagnostics: dict = {}
    if not audio_analysis_result:
        return list(ads), skip_diagnostics
    cues = sorted(
        (c for c in audio_analysis_result.get_signals_by_type('audio_cue')
         if is_template_cue(c.details)
         and cue_cuts_matches(c.details.get('cue_type', AUDIO_CUE_TYPE_DEFAULT),
                              c.details.get('cut_matches'), cue_settings_cut_matches)),
        key=lambda c: c.start,
    )
    new_ads = list(ads)
    for c in cues:
        details = c.details or {}
        key = _diag_key(details.get('template_id'), c.start)
        if c.confidence < min_confidence:
            skip_diagnostics[key] = SKIP_BELOW_CONFIDENCE
            continue
        if _overlaps_existing_ad(new_ads, float(c.start), float(c.end)):
            skip_diagnostics[key] = SKIP_COVERED
            continue
        cue_type = details.get('cue_type', AUDIO_CUE_TYPE_DEFAULT)
        confidence = round(float(c.confidence), 3)
        new_ads.append({
            'start': round(float(c.start), 3),
            'end': round(float(c.end), 3),
            'confidence': confidence,
            'reason': 'audio_cue_match',
            'detection_stage': CUE_MATCH_STAGE,
            'category': CUE_CUT_CATEGORY[cue_type],
            'cue_match': {
                'template_id': details.get('template_id'),
                'label': details.get('label'),
                'cue_type': cue_type,
                'cue_start': round(float(c.start), 3),
            },
        })
        logger.info(
            f"Cue cut marker: {c.start:.1f}s-{c.end:.1f}s {details.get('label')!r} "
            f"({cue_type}, conf={confidence:.2f})"
        )
    new_ads.sort(key=lambda a: a.get('start', 0.0))
    return new_ads, skip_diagnostics
