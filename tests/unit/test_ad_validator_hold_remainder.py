"""A held remainder keeps a reviewer trim only when it overlaps the remainder."""
from tests.app_bootstrap import bootstrap

bootstrap('hold_remainder_proposal_test_')

from ad_validator import AdValidator
from tests.unit.pass2_test_utils import NO_SPLICE


def _remainders(proposal):
    ad = {'start': 2086.0, 'end': 2203.1, 'confidence': 0.9,
          'reviewer_proposed_start': proposal[0], 'reviewer_proposed_end': proposal[1]}
    span = {'start': 2093.69, 'end': 2203.1}
    return AdValidator(episode_duration=3600.0)._held_remainders(ad, span, 2086.0, 2203.1, NO_SPLICE)


def test_disjoint_proposal_is_dropped_from_the_remainder():
    pieces = _remainders((2158.3, 2203.1))
    assert [(p['start'], p['end']) for p in pieces] == [(2086.0, 2093.69)]
    assert 'reviewer_proposed_start' not in pieces[0]
    assert 'reviewer_proposed_end' not in pieces[0]


def test_overlapping_proposal_is_clamped_to_the_remainder():
    pieces = _remainders((2080.0, 2090.0))
    assert pieces[0]['reviewer_proposed_start'] == 2086.0
    assert pieces[0]['reviewer_proposed_end'] == 2090.0
