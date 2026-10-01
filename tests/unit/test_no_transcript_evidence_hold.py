"""LLM spans with no category or an audio-only reason need ad language in their transcript (#807)."""
import pytest

from tests.app_bootstrap import bootstrap

bootstrap('no_transcript_evidence_test_')

from unittest.mock import MagicMock

from ad_validator import AdValidator, Decision
from utils.text import pattern_offsets, word_boundary_re

START, END = 1250.0, 1300.0
ECHO = 'DAI transition pair and volume anomaly indicate ad boundary'
CHAT = "and that's how we ended up moving the studio across town last spring"


def _validate(text=CHAT, analysis=None, sponsor_service=None, **ad_fields):
    ad = {'start': START, 'end': END, 'confidence': 0.95, 'reason': ECHO,
          'detection_stage': 'claude'}
    ad.update(ad_fields)
    kwargs = {k: ad.pop(k) for k in ('cue_gate_enabled', 'confirmed_corrections') if k in ad}
    segments = [{'start': START, 'end': END, 'text': text}]
    validator = AdValidator(3600.0, segments, episode_description='',
                            sponsor_service=sponsor_service, **kwargs)
    return validator.validate([ad], audio_analysis=analysis).ads[0]


def _held(ad):
    return (ad['validation']['decision'] == Decision.REVIEW.value
            and ad.get('hold_reason') == 'no_transcript_evidence')


def _cut(ad):
    return ad['validation']['decision'] == Decision.ACCEPT.value and not ad.get('held_for_review')


EDGE_PAIR = {'signals': [{'signal_type': 'dai_transition_pair', 'start': START - 1.0, 'end': START + 1.0}]}


@pytest.mark.parametrize('stage', ['claude', 'verification'])
def test_an_uncategorised_echo_span_with_edge_audio_is_held(stage, caplog):
    with caplog.at_level('INFO'):
        assert _held(_validate(analysis=EDGE_PAIR, detection_stage=stage))
    assert 'no ad language in the span transcript' in caplog.text


def test_an_audio_only_reason_is_held_even_with_a_category():
    assert _held(_validate(category='sponsor', reason='splice_1261.3'))


def test_edge_only_splice_events_do_not_exempt():
    analysis = {'splice_evidence': {'version': 1, 'calibration': {'status': 'calibrated'}, 'events': [
        {'time': START - 0.5, 'end_time': START, 'type': 'digital_silence'},
        {'time': END, 'end_time': END + 0.5, 'type': 'digital_silence'}]}}
    assert _held(_validate(analysis=analysis,
                           reason='Splice evidence: digital silence at both edges'))


@pytest.mark.parametrize('text', [
    'and you can use code ACME for 20 percent off your first box',
    'this episode is brought to you by our friends at the shop',
    'go to acme dot com slash show to learn more about the plan',
])
def test_ad_language_in_the_transcript_keeps_the_cut(text):
    assert _cut(_validate(text=text))


def test_a_registry_brand_in_the_transcript_keeps_the_cut():
    registry = MagicMock()
    registry.brand_mention_offsets.side_effect = (
        lambda text: pattern_offsets(text, {'Acme Tools': word_boundary_re(['Acme Tools'])}))
    registry.mentions_brand.return_value = False
    registry.get_sponsors.return_value = []
    assert _cut(_validate(text='we spent the week with Acme Tools in the garage',
                          sponsor_service=registry))


def test_a_span_mostly_inside_a_dai_core_is_cut():
    assert _cut(_validate(dai_core_spans=[{'start': START, 'end': END}]))


def test_a_span_under_half_covered_by_a_dai_core_is_held():
    assert _held(_validate(dai_core_spans=[{'start': START, 'end': START + 20.0}]))


def test_a_measured_fingerprint_member_exempts():
    member = {'start': START, 'end': END, 'stage': 'fingerprint', 'pattern_id': 4,
              'fingerprint_match_start': START, 'fingerprint_match_end': END}
    assert _cut(_validate(merged_member_spans=[member], merged_protected_start=START,
                          merged_protected_end=END))


def test_a_cue_snapped_edge_exempts():
    assert _cut(_validate(cue_snap={'start': {'source': 'template', 'cue_start': 1248.0,
                                              'cue_end': 1249.95}}))


def test_a_cross_fetch_differential_exempts():
    analysis = {'dai_differential': {'regions': [
        {'kind': 'differential', 'corr': 0.0, 'start_s': START, 'end_s': END}]}}
    assert _cut(_validate(analysis=analysis))


def test_a_categorised_prose_span_without_evidence_stays_cut():
    """R1: the non-English shape (25.9 s, category sponsor, prose reason) is not in scope."""
    ad = _validate(category='sponsor', reason='Non-English language segment (likely DAI ad)',
                   end=START + 25.9)
    assert _cut(ad)


def test_a_categorised_summary_reason_is_not_held():
    """R1 pins the narrow trigger: a category plus a prose reason is not gated, even with no evidence."""
    assert _cut(_validate(category='sponsor', reason='A summary of the discussion so far'))


def test_an_uncategorised_summary_reason_is_held():
    assert _held(_validate(reason='A summary of the discussion so far'))


@pytest.mark.parametrize('stage', ['dai_differential', 'fingerprint', 'text_pattern', 'cue_pair', 'manual'])
def test_other_stages_are_not_gated(stage):
    assert _validate(detection_stage=stage).get('hold_reason') != 'no_transcript_evidence'


def test_the_hold_takes_precedence_over_the_cue_gate():
    assert _held(_validate(cue_gate_enabled=True))


def test_the_hold_takes_precedence_over_the_splice_veto():
    analysis = {'splice_evidence': {'version': 1, 'events': [],
                                    'calibration': {'status': 'calibrated'}}}
    ad = _validate(analysis=analysis, end=START + 90.0)
    assert _held(ad)


def test_a_user_confirmed_span_is_never_held():
    corrections = [{'start': START, 'end': END}]
    assert _cut(_validate(confirmed_corrections=corrections))


def test_pass2_never_auto_releases_the_hold():
    """R3: a re-detection by the same model on the same audio is not independent evidence."""
    from config import (HOLD_REASON_NO_TRANSCRIPT_EVIDENCE, PASS2_AUTOAPPROVE_HOLD_REASONS,
                        PASS2_REVIEWED_RELEASE_HOLD_REASONS)
    assert HOLD_REASON_NO_TRANSCRIPT_EVIDENCE not in PASS2_AUTOAPPROVE_HOLD_REASONS
    assert HOLD_REASON_NO_TRANSCRIPT_EVIDENCE not in PASS2_REVIEWED_RELEASE_HOLD_REASONS
