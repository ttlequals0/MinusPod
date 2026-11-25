"""The validator merge and the cross-pass planner bridge a gap beside a reviewer lock under one rule."""
import pytest

from tests.app_bootstrap import bootstrap

bootstrap('locked_gap_agreement_test_')

from ad_validator import AdValidator, ValidationResult
from main_app.pipeline import verify

WORD_IN_GAP = {'start': 120.5, 'end': 121.0, 'text': 'and', 'words': [{'start': 120.5, 'end': 121.0}]}
BEFORE = {'start': 100.0, 'end': 119.8, 'text': 'before',
          'words': [{'start': 100.0, 'end': 119.8}]}
AFTER = {'start': 122.0, 'end': 140.0, 'text': 'after',
         'words': [{'start': 122.0, 'end': 140.0}]}


def _validator_bridges(segments, gap_end, locked):
    left = {'start': 100.0, 'end': 120.0}
    right = {'start': gap_end, 'end': 140.0, **({'reviewer_locked_start': gap_end} if locked else {})}
    validator = AdValidator(episode_duration=1000.0, segments=segments)
    return len(validator._merge_close_ads([left, right], ValidationResult(ads=[]))) == 1


def _planner_bridges(segments, gap_end, locked):
    pass1 = [{'start': 100.0, 'end': 120.0, 'action_applied': 'remove', 'replacement_duration': 1.0}]
    pass2 = [{'start': gap_end, 'end': 140.0, 'action_applied': 'remove',
              **({'reviewer_locked_start': gap_end} if locked else {})}]
    return verify._crosspass_cut_plan(pass1, list(pass1), pass2, segments, [], 12.0) is not None


CASES = [
    ('wordless 1.9s gap', [BEFORE, AFTER], 121.9, True),
    ('word in the gap', [BEFORE, WORD_IN_GAP, AFTER], 121.9, False),
    ('6s wordless gap', [BEFORE, {**AFTER, 'start': 126.0, 'words': [{'start': 126.0, 'end': 140.0}]}],
     126.0, False),
    ('no transcript', [], 121.9, False),
]


@pytest.mark.parametrize('name,segments,gap_end,bridges', CASES, ids=[c[0] for c in CASES])
def test_planner_and_validator_agree_beside_a_pass2_lock(name, segments, gap_end, bridges):
    assert _validator_bridges(segments, gap_end, True) is bridges
    assert _planner_bridges(segments, gap_end, True) is bridges
