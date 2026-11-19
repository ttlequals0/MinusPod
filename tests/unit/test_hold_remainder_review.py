"""What a pass-2 release leaves of a transcript-gap hold is reviewed once and released on a confirm."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('hold_remainder_review_test_')

from ad_validator import Decision
from config import HOLD_REASON_TRANSCRIPT_DIFFERENTIAL as GAP, is_pending_review
from ad_reviewer import ReviewResult
from llm_client import ProviderRateLimitedError
from main_app.pipeline import review as review_stage, verify
from tests.unit.pass2_test_utils import (
    _ctx, _filed_confirms, _hold, _recut_validate, _release_confirm, _spans, _verdict, drive_verification_pass,
)

HOLD = (2599.12, 2659.28)
FIRST = (2599.12, 2629.36)
SECOND = (2629.36, 2659.28)
FINDING = (2634.8, 2659.2, 0.95)
FLAG = 'INFO: Hold remainder confirmed by review'


def _gap_hold(start=HOLD[0], end=HOLD[1], reason=GAP):
    return dict(_hold(start, end, reason), confidence=0.95, reason='Acme sponsor read',
                detection_stage='claude')


def _release_verdict():
    return _verdict('adjust', FINDING[0], HOLD[1], adjusted=SECOND)


def _run(holds, verdicts, findings=(FINDING,), **kwargs):
    return drive_verification_pass(list(findings), holds=holds, hold_verdicts=verdicts, **kwargs)


def _flags(hold):
    return hold.get('validation', {}).get('flags', [])


def test_confirmed_remainder_is_released_beside_the_pass2_release(monkeypatch, caplog):
    hold = _gap_hold()
    with caplog.at_level('INFO', logger='podcast.audio'):
        run = _run([hold], [_release_verdict(), _verdict('confirmed', *FIRST)])

    assert run.hold_reviews == [[(FINDING[0], HOLD[1])], [FIRST]]
    assert hold['pass2_released_spans'] == [
        {'start': FIRST[0], 'end': FIRST[1]}, {'start': SECOND[0], 'end': SECOND[1]}]
    assert hold['hold_reason'] == GAP
    assert FLAG in _flags(hold)
    assert any('Hold remainder confirmed by review' in r.getMessage() for r in caplog.records)
    assert run.output[7] == 2

    filed = _filed_confirms(monkeypatch, [hold])
    assert [f['corrected_bounds'] for f in filed] == [
        {'start': FIRST[0], 'end': FIRST[1]}, {'start': SECOND[0], 'end': SECOND[1]}]
    assert {f['origin'] for f in filed} == {'auto_pass2'}


def test_released_result_is_not_reheld_on_revalidation_or_recut():
    hold = _gap_hold()
    confirms = [_release_confirm(HOLD, SECOND, GAP, hold_id='h1'),
                _release_confirm(HOLD, FIRST, GAP, hold_id='h1')]
    ads = _recut_validate([dict(hold, hold_id='h1')], confirms)
    assert sorted((a['start'], a['end'], a['validation']['decision']) for a in ads) == [
        (*FIRST, Decision.ACCEPT.value), (*SECOND, Decision.ACCEPT.value)]
    assert not any(a.get('held_for_review') or a.get('pass2_hold_remainder') for a in ads)

    saved = [{k: v for k, v in a.items() if k != 'validation'} | {'was_cut': True} for a in ads]
    again = _recut_validate(saved, confirms)
    assert sorted((a['start'], a['end'], a['validation']['decision']) for a in again) == [
        (*FIRST, Decision.ACCEPT.value), (*SECOND, Decision.ACCEPT.value)]
    assert not any(a.get('held_for_review') for a in again)


@pytest.mark.parametrize('verdict', [
    _verdict('reject', *FIRST),
    _verdict('inconclusive', *FIRST),
    _verdict('failure', *FIRST),
    _verdict('confirmed', *FIRST, boundary_conflict=True),
    _verdict('adjust', *FIRST, adjusted=(FIRST[0] - 20.0, FIRST[1] - 10.0)),
])
def test_unconfirmed_remainder_stays_held(verdict):
    hold = _gap_hold()
    run = _run([hold], [_release_verdict(), verdict])

    assert len(run.hold_reviews) == 2
    assert hold['pass2_released_spans'] == [{'start': SECOND[0], 'end': SECOND[1]}]
    assert hold['hold_reason'] == GAP
    assert FLAG not in _flags(hold)
    assert is_pending_review(hold)


def test_rate_limit_from_the_remainder_review_propagates():
    calls = []

    def review(**kwargs):
        calls.append(kwargs)
        if len(calls) == 2:
            raise ProviderRateLimitedError('resets in 900s', retry_after_seconds=900.0)
        return ReviewResult(verdicts=[_release_verdict()])

    with pytest.raises(ProviderRateLimitedError):
        drive_verification_pass(
            [FINDING], holds=[_gap_hold()], hold_verdicts=[],
            extra_patches=(patch.object(review_stage, '_build_reviewer',
                                        lambda db, det: SimpleNamespace(review=review)),))
    assert len(calls) == 2


@pytest.mark.parametrize('hold_kwargs, verdict_span, adjusted, released', [
    ({'reason': 'reviewer_inconclusive_bounds'}, (FINDING[0], HOLD[1]), SECOND,
     [{'start': SECOND[0], 'end': SECOND[1]}]),
    ({}, (FINDING[0], HOLD[1]), None, None),
    ({}, (FINDING[0], HOLD[1]), (2603.0, HOLD[1]), [{'start': 2603.0, 'end': HOLD[1]}]),
], ids=['other_reason', 'no_release', 'remainder_too_short'])
def test_no_remainder_call_when_nothing_qualifies(hold_kwargs, verdict_span, adjusted, released):
    hold = _gap_hold(**hold_kwargs)
    kind = 'adjust' if adjusted else 'reject'
    run = _run([hold], [_verdict(kind, *verdict_span, adjusted=adjusted)])
    assert len(run.hold_reviews) == 1
    assert hold.get('pass2_released_spans') == released


def test_review_disabled_makes_no_remainder_call():
    calls = []
    hold = _gap_hold()
    hold.update(pass2_corroborated=True, pass2_corroborated_span={'start': SECOND[0], 'end': SECOND[1]})
    with patch.object(review_stage, '_ad_review_enabled', lambda db: False), \
            patch.object(review_stage, '_build_reviewer',
                         lambda db, det: SimpleNamespace(review=lambda **k: calls.append(k))):
        protection = verify.build_protection(
            kept=[], category_kept=[], user_trims=[], fp_corrections=[],
            holds=[hold], pass1_cuts=[])
        assert review_stage._review_hold_remainders(_ctx(), [hold], [], protection) == 0
    assert calls == []


def test_remainder_shorter_than_min_ad_duration_is_not_sent():
    hold = _gap_hold(2599.12, 2659.28)
    run = _run([hold], [_verdict('adjust', FINDING[0], HOLD[1], adjusted=(2603.0, 2659.28))])
    assert len(run.hold_reviews) == 1
    assert hold['pass2_released_spans'] == [{'start': 2603.0, 'end': 2659.28}]


def test_remainder_over_a_kept_span_is_not_released():
    hold = _gap_hold()
    run = _run([hold], [_release_verdict(), _verdict('confirmed', *FIRST)],
               kept=[{'start': 2610.0, 'end': 2615.0, 'action_applied': 'keep'}])
    assert len(run.hold_reviews) == 2
    assert hold['pass2_released_spans'] == [{'start': SECOND[0], 'end': SECOND[1]}]
    assert FLAG not in _flags(hold)


def test_remainders_of_two_holds_share_one_reviewer_call():
    other = (2700.0, 2760.0)
    holds = [_gap_hold(), _gap_hold(*other)]
    verdicts = [
        _release_verdict(), _verdict('confirmed', *FIRST),
        _verdict('adjust', 2735.0, 2759.0, adjusted=(2730.0, 2760.0)),
        _verdict('confirmed', 2700.0, 2730.0)]
    run = _run(holds, verdicts, findings=(FINDING, (2735.0, 2759.0, 0.95)))

    assert len(run.hold_reviews) == 2
    assert sorted(run.hold_reviews[1]) == [FIRST, (2700.0, 2730.0)]
    assert all(FLAG in _flags(h) for h in holds)


def _reviewed(start, end, hold):
    return [({'start': start, 'end': end}, hold)]


def _remainder_calls(hold, reviewed):
    calls = []
    protection = verify.build_protection(
        kept=[], category_kept=[], user_trims=[], fp_corrections=[],
        holds=[hold], pass1_cuts=[])
    with patch.object(review_stage, '_ad_review_enabled', lambda db: True), \
            patch.object(review_stage.ad_detector, 'get_verification_model', lambda: 'test-model'), \
            patch.object(review_stage.ad_detector, 'get_verification_provider', lambda: None), \
            patch.object(review_stage, '_build_reviewer', lambda db, det: SimpleNamespace(
                review=lambda **k: calls.append(_spans(k['accepted_ads'])) or ReviewResult(
                    verdicts=[]))):
        review_stage._review_hold_remainders(_ctx(), [hold], [], protection, reviewed=reviewed)
    return calls


def test_remainder_covered_by_a_rejected_subspan_is_not_sent_again():
    hold = _gap_hold()
    hold.update(pass2_corroborated=True, pass2_released_spans=[
        {'start': SECOND[0], 'end': SECOND[1]}])
    assert _remainder_calls(hold, _reviewed(*FIRST, hold)) == []


def test_only_the_unreviewed_part_of_the_remainder_is_sent():
    hold = _gap_hold()
    hold.update(pass2_corroborated=True, pass2_released_spans=[
        {'start': SECOND[0], 'end': SECOND[1]}])
    assert _remainder_calls(hold, _reviewed(2614.0, FIRST[1], hold)) == [[(FIRST[0], 2614.0)]]
