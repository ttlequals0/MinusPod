"""Stage-set corroboration and paired label-by-coverage in
_merge_detection_results."""
from tests.app_bootstrap import bootstrap
bootstrap('merge_stage_set_test_')

from ad_detector import AdDetector

SEGMENTS = [{'start': s, 'end': s + 5.0, 'text': 'words ' * 12}
            for s in range(1970, 2100, 5)]


def _merge(detector, ads):
    return detector._merge_detection_results(ads, SEGMENTS, action_map=None)


def _detector():
    return AdDetector.__new__(AdDetector)


def test_hold_cleared_by_earlier_pattern_member_after_stage_overwrite():
    # pattern folds in first, then an uncorroborated differential; the
    # stage-priority rule flips the merged stage to dai_differential, but the
    # pattern member must still corroborate.
    ads = [
        {'start': 2000.0, 'end': 2040.0, 'confidence': 0.94,
         'detection_stage': 'text_pattern', 'pattern_id': 600,
         'reason': 'Acme (pattern #600)', 'sponsor': 'Acme', 'category': 'sponsor'},
        {'start': 2010.0, 'end': 2074.0, 'confidence': 0.98,
         'detection_stage': 'claude', 'reason': 'Acme then WidgetCo reads',
         'sponsor': 'Acme', 'category': 'sponsor'},
        {'start': 2045.0, 'end': 2084.0, 'confidence': 0.95,
         'detection_stage': 'dai_differential',
         'differential_uncorroborated': True, 'held_for_review': True,
         'reason': 'Audio differs across fetches', 'category': 'sponsor'},
    ]
    merged = _merge(_detector(), ads)
    assert len(merged) == 1
    assert not merged[0].get('differential_uncorroborated')
    assert not merged[0].get('held_for_review')


def test_label_goes_to_covering_member_not_longest_reason():
    ads = [
        {'start': 2000.0, 'end': 2074.0, 'confidence': 0.98,
         'detection_stage': 'claude', 'sponsor': 'Acme',
         'reason': 'Acme sponsor read', 'category': 'sponsor'},
        {'start': 2000.0, 'end': 2010.0, 'confidence': 0.94,
         'detection_stage': 'text_pattern', 'pattern_id': 600, 'sponsor': 'ShowOutro',
         'reason': 'ShowOutro (pattern #600, outro "a much much longer reason string '
                   'that would win under the old longest-reason rule entirely")',
         'category': 'sponsor'},
    ]
    merged = _merge(_detector(), ads)
    assert merged[0]['sponsor'] == 'Acme'
    assert merged[0]['reason'] == 'Acme sponsor read'


def test_hold_cleared_when_corroborator_folded_before_stage_overwrite():
    # Stage priority overwrites last's stage away from 'text_pattern' before
    # the uncorroborated differential arrives; the accumulated stage set must
    # still see 'text_pattern' and clear the hold.
    ads = [
        {'start': 2000.0, 'end': 2020.0, 'confidence': 0.9,
         'detection_stage': 'text_pattern', 'pattern_id': 600,
         'reason': 'Acme (pattern #600)', 'sponsor': 'Acme', 'category': 'sponsor'},
        {'start': 2005.0, 'end': 2050.0, 'confidence': 0.95,
         'detection_stage': 'dai_differential',
         'reason': 'Dynamically inserted: audio differs across fetches '
                   '(corroborated by overlapping ad marker)',
         'sponsor': None, 'category': 'sponsor'},
        {'start': 2045.0, 'end': 2090.0, 'confidence': 0.95,
         'detection_stage': 'dai_differential',
         'differential_uncorroborated': True, 'held_for_review': True,
         'reason': 'Audio differs across fetches; no other ad signal -- review',
         'sponsor': None, 'category': 'sponsor'},
    ]
    merged = _merge(_detector(), ads)
    assert len(merged) == 1
    assert not merged[0].get('differential_uncorroborated')
    assert not merged[0].get('held_for_review')


def test_pattern_defined_survives_fold_into_claude_first_marker():
    # A defined-pattern member folding into a claude-first marker must still
    # stamp pattern_defined on the merged result: the keep partition only
    # ever sees the merged marker, not the individual members.
    ads = [
        {'start': 100.0, 'end': 150.0, 'confidence': 0.9,
         'detection_stage': 'claude', 'category': 'cross_promo'},
        {'start': 110.0, 'end': 145.0, 'confidence': 0.85,
         'detection_stage': 'text_pattern', 'pattern_id': 600,
         'pattern_defined': True, 'category': 'cross_promo'},
    ]
    merged = _merge(_detector(), ads)
    assert len(merged) == 1
    assert merged[0].get('pattern_defined') is True


def test_member_stages_and_label_span_are_stripped_before_return():
    ads = [
        {'start': 2000.0, 'end': 2040.0, 'confidence': 0.9,
         'detection_stage': 'text_pattern', 'reason': 'Acme (pattern #1)',
         'sponsor': 'Acme', 'category': 'sponsor'},
        {'start': 2010.0, 'end': 2060.0, 'confidence': 0.95,
         'detection_stage': 'claude', 'reason': 'Acme sponsor read',
         'sponsor': 'Acme', 'category': 'sponsor'},
    ]
    merged = _merge(_detector(), ads)
    assert '_member_stages' not in merged[0]
    assert '_label_span' not in merged[0]
