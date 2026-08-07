"""tests/unit/test_estimated_span_advisory.py"""
from tests.app_bootstrap import bootstrap
bootstrap('estimated_span_advisory_test_')

from ad_detector import AdDetector

SEGMENTS = [{'start': s, 'end': s + 5.0, 'text': 'words ' * 12}
            for s in range(1970, 2100, 5)]


def test_estimated_pattern_span_does_not_clear_hold():
    # start=2040 (< last['end']=2043) forces a true overlap so the merge
    # reaches the stage-set corroboration check instead of the separate
    # (#541) adjacency-is-not-corroboration bypass for touching spans.
    ads = [
        {'start': 1976.0, 'end': 2043.0, 'confidence': 0.94,
         'detection_stage': 'text_pattern', 'pattern_id': 600,
         'span_estimated': True, 'sponsor': 'ShowOutro',
         'reason': 'ShowOutro (pattern #600)', 'category': 'sponsor'},
        {'start': 2040.0, 'end': 2084.0, 'confidence': 0.95,
         'detection_stage': 'dai_differential',
         'differential_uncorroborated': True, 'held_for_review': True,
         'reason': 'Audio differs across fetches', 'category': 'sponsor'},
    ]
    d = AdDetector.__new__(AdDetector)
    merged = d._merge_detection_results(ads, SEGMENTS, action_map=None)
    held = [m for m in merged if m.get('held_for_review')]
    assert held, "estimated span must not corroborate the differential hold"
