"""Reviewer verdict/reasoning contradiction (spec 1.4).

ROOT CAUSE: _review_single derives confirmed/adjust purely from boundary
arithmetic (src/ad_reviewer.py:698-720) and never inspects the model's
reason text (kept.get("reason") at :648 is stored, not evaluated). A model
response that returns the ad object with unchanged boundaries but a reason
of "no advertisement content" therefore ships verdict=confirmed and the ad
is cut (Dillon "Vrbo" 90s false cut; 4 similar TWiT contradictions).
This is a parser/derivation gap, not malformed model output.
"""
import os
import sys
import tempfile

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='contradiction_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

import main_app.processing as processing
from ad_reviewer import AdReviewer, ReviewVerdict, reasoning_contradicts_cut, reasoning_affirms_ad
from config import HOLD_REASON_REVIEWER_CONTRADICTION


def _mock_segments():
    return [
        {'start': 0.0, 'end': 60.0, 'text': 'show content'},
        {'start': 60.0, 'end': 120.0, 'text': 'before ad'},
        {'start': 120.0, 'end': 180.0, 'text': 'ad sponsor pitch'},
        {'start': 180.0, 'end': 240.0, 'text': 'after ad'},
        {'start': 240.0, 'end': 300.0, 'text': 'more show content'},
    ]


def _mock_episode_meta():
    return {
        'podcast_name': 'Test Podcast', 'episode_title': 'Test Episode',
        'episode_description': 'desc', 'podcast_description': 'pod desc',
        'slug': 'test-pod', 'episode_id': 'ep1', 'podcast_id': 'p1',
    }


def _build_reviewer(db_settings=None):
    db_settings = db_settings or {}
    db = MagicMock()
    db.get_setting.side_effect = lambda key: db_settings.get(key)
    db.get_connection.return_value = MagicMock()
    llm_client = MagicMock()
    return AdReviewer(db=db, llm_client=llm_client, sponsor_service=None)


@dataclass
class _LLMResp:
    content: str
    model: str = "test-model"


def _resp(body: str) -> _LLMResp:
    return _LLMResp(content=body)


NEGATIVE_REASONS = [
    'This segment contains no advertisement content whatsoever',
    'This is NOT AN AD, it is host conversation',
    'Window has no ad content at these timestamps',
    'The span contains no ad and should not be cut',
    # Verbatim prod reasonings the original four literal substrings missed
    # while the spans were cut anyway (monday-morning-podcast 39098646c82c):
    'The candidate boundaries (299.2s-395.4s) contain no advertising content '
    'whatsoever. This is a false positive from the text pattern matcher.',
    'The content within the candidate boundaries is not advertising -- it is '
    "Bill Burr's own comedic riff on insurance",
    # Artifact/editorial family seen on TWiT-network episodes:
    "The window contains only the words 'and many more.'",
    'This span is a transcription artifact, not advertising content',
    'This is entirely organic conversation between the hosts',
]


# Reasonings that AFFIRM the cut while mentioning the same nouns the
# contradiction patterns key on. Broad noun-phrase patterns matched these
# (negation-blind) and falsely held confirmed ads, shipping them uncut.
POSITIVE_REASONS = [
    'This span is a clear sponsor read, not a false positive',
    'adjusted end ensures no advertising remains after the cut',
    'boundaries capture the transition from organic conversation into the ad read',
    'Confirmed sponsor read for BetterHelp',
]


@pytest.mark.parametrize('reason', NEGATIVE_REASONS)
def test_reasoning_contradicts_cut_matches_negative_patterns(reason):
    assert reasoning_contradicts_cut(reason) is True


@pytest.mark.parametrize('reason', POSITIVE_REASONS)
def test_reasoning_affirming_cut_is_not_held(reason):
    assert reasoning_contradicts_cut(reason) is False


def test_reasoning_contradicts_cut_ignores_normal_reasons():
    assert reasoning_contradicts_cut('Confirmed sponsor read for BetterHelp') is False
    assert reasoning_contradicts_cut(None) is False


def test_confirmed_with_contradictory_reasoning_is_held_not_cut():
    """Pre-guard, this exact response yielded verdict=confirmed and the ad
    STAYED in accepted_after_review (see module docstring for root cause)."""
    reviewer = _build_reviewer({
        'review_prompt': 'review', 'resurrect_prompt': 'resurrect',
        'review_max_boundary_shift': '60',
    })
    reviewer._llm_client.messages_create.return_value = _resp(
        '[{"start": 120.0, "end": 180.0, "confidence": 0.9, '
        '"reason": "This segment contains no advertisement content whatsoever"}]'
    )
    ad = {'start': 120.0, 'end': 180.0, 'confidence': 0.9}
    result = reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )
    assert result.verdicts[0].verdict == 'confirmed'
    assert result.accepted_after_review == []
    held = result.held_by_contradiction[0]
    assert held['held_for_review'] is True
    assert held['was_cut'] is False
    assert held['hold_reason'] == HOLD_REASON_REVIEWER_CONTRADICTION
    assert held['source'] == 'reviewer'
    assert held.get('reviewer_contradiction') is True


def test_adjust_with_contradictory_reasoning_is_held_not_cut():
    reviewer = _build_reviewer({
        'review_prompt': 'review', 'resurrect_prompt': 'resurrect',
        'review_max_boundary_shift': '60',
    })
    reviewer._llm_client.messages_create.return_value = _resp(
        '[{"start": 115.0, "end": 185.0, "confidence": 0.6, '
        '"reason": "Not an ad - continuous host monologue"}]'
    )
    ad = {'start': 120.0, 'end': 180.0, 'confidence': 0.9}
    result = reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )
    assert result.verdicts[0].verdict == 'adjust'
    assert result.accepted_after_review == []
    held = result.held_by_contradiction[0]
    assert held['held_for_review'] is True
    assert held['was_cut'] is False
    assert held.get('reviewer_contradiction') is True


def test_confirmed_with_normal_reasoning_still_cut():
    reviewer = _build_reviewer({
        'review_prompt': 'review', 'resurrect_prompt': 'resurrect',
        'review_max_boundary_shift': '60',
    })
    reviewer._llm_client.messages_create.return_value = _resp(
        '[{"start": 120.0, "end": 180.0, "confidence": 0.95, '
        '"reason": "Confirmed sponsor read"}]'
    )
    ad = {'start': 120.0, 'end': 180.0, 'confidence': 0.9}
    result = reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )
    assert result.accepted_after_review == [ad]
    assert result.held_by_contradiction == []


def test_failure_verdict_keeps_pass1_cut_decision():
    """Documented fallback (spec 1.4): reviewer unavailable -> trust the
    pass-1 decision. The ad stays in the cut list and is NOT held."""
    reviewer = _build_reviewer({
        'review_prompt': 'review', 'resurrect_prompt': 'resurrect',
    })
    with patch('ad_reviewer.call_llm_for_window',
               return_value=(None, RuntimeError('boom'))):
        ad = {'start': 120.0, 'end': 180.0, 'confidence': 0.9}
        result = reviewer.review(
            accepted_ads=[ad], resurrection_eligible=[],
            segments=_mock_segments(), episode_meta=_mock_episode_meta(),
            pass_num=1, pass_model='claude-test',
        )
    assert result.verdicts[0].verdict == 'failure'
    assert result.accepted_after_review == [ad]
    assert result.held_by_contradiction == []
    assert 'held_for_review' not in ad


def test_apply_reviewer_verdict_contradiction_holds_master_ad():
    ad = {'start': 120.0, 'end': 180.0, 'was_cut': True}
    v = ReviewVerdict(
        pool='accepted', pass_num=1, verdict='confirmed',
        original_start=120.0, original_end=180.0,
        reasoning='Contains no ad content', confidence=0.9, model_used='m',
    )
    processing._apply_reviewer_verdict_to_ad(ad, v)
    assert ad['held_for_review'] is True
    assert ad['was_cut'] is False
    assert ad['hold_reason'] == HOLD_REASON_REVIEWER_CONTRADICTION
    assert ad['reviewer_verdict'] == 'confirmed'
    assert ad.get('reviewer_contradiction') is True


def test_apply_reviewer_verdict_contradicted_adjust_keeps_boundaries():
    ad = {'start': 120.0, 'end': 180.0, 'was_cut': True}
    v = ReviewVerdict(
        pool='accepted', pass_num=1, verdict='adjust',
        original_start=120.0, original_end=180.0,
        adjusted_start=115.0, adjusted_end=185.0,
        reasoning='window contains no advertisement', model_used='m',
    )
    processing._apply_reviewer_verdict_to_ad(ad, v)
    assert ad['held_for_review'] is True
    assert ad['start'] == 120.0
    assert ad['end'] == 180.0
    assert ad.get('reviewer_contradiction') is True


def test_contradicted_adjust_preserves_proposed_bounds():
    # The hold keeps pass-1 boundaries but must carry the reviewer's proposed
    # trim so the review UI can offer approving just the ad portion.
    ad = {'start': 120.0, 'end': 180.0, 'was_cut': True}
    v = ReviewVerdict(
        pool='accepted', pass_num=1, verdict='adjust',
        original_start=120.0, original_end=180.0,
        adjusted_start=150.0, adjusted_end=180.0,
        reasoning='the first 30s is not an ad, it is the show outro',
        model_used='m',
    )
    processing._apply_reviewer_verdict_to_ad(ad, v)
    assert ad['held_for_review'] is True
    assert ad['start'] == 120.0 and ad['end'] == 180.0  # unchanged
    assert ad['reviewer_proposed_start'] == 150.0
    assert ad['reviewer_proposed_end'] == 180.0


def test_apply_reviewer_verdict_confirmed_without_bounds_has_none():
    # A confirmed contradiction hold with no recovered trim carries no
    # proposed bounds (extraction failed or reasoning named no sub-span).
    ad = {'start': 120.0, 'end': 180.0, 'was_cut': True}
    v = ReviewVerdict(
        pool='accepted', pass_num=1, verdict='confirmed',
        original_start=120.0, original_end=180.0,
        reasoning='window has no ad content', model_used='m',
    )
    processing._apply_reviewer_verdict_to_ad(ad, v)
    assert ad['held_for_review'] is True
    assert 'reviewer_proposed_start' not in ad
    assert 'reviewer_proposed_end' not in ad


def test_apply_reviewer_verdict_confirmed_with_recovered_bounds():
    # Trim recovery stashes the sub-span in adjusted_* on the confirmed
    # verdict; the master-ad merge must surface it as reviewer_proposed_*
    # while leaving the pass-1 boundaries and the hold untouched.
    ad = {'start': 120.0, 'end': 180.0, 'was_cut': True}
    v = ReviewVerdict(
        pool='accepted', pass_num=1, verdict='confirmed',
        original_start=120.0, original_end=180.0,
        adjusted_start=120.0, adjusted_end=150.0,
        reasoning='the ad ends at 150.0s; the rest is not an ad',
        model_used='m',
    )
    processing._apply_reviewer_verdict_to_ad(ad, v)
    assert ad['held_for_review'] is True
    assert ad['was_cut'] is False
    assert ad['start'] == 120.0 and ad['end'] == 180.0
    assert ad['reviewer_proposed_start'] == 120.0
    assert ad['reviewer_proposed_end'] == 150.0


# ---------- Trim-bounds recovery on confirmed-verdict contradiction holds ----------
# Prod incident (the-brilliant-idiots 79eedd7bf2a7): reviewer returned
# 0.0-87.8s unchanged (verdict derived 'confirmed') while its reasoning said
# the ad ends at ~65.8s and the tail must be trimmed. The hold fired but
# carried no bounds the UI could one-tap approve. A follow-up LLM call now
# recovers {"ad_start", "ad_end"} from the reasoning text.

TRIM_REASONING = (
    'The ad content ends at 150.0s; the closing sentence is show content, '
    'is not an ad, and must be trimmed off the end'
)


def _run_confirmed_hold(followup):
    """Run one review whose main call yields a confirmed contradiction hold
    with trim language, and whose follow-up call yields ``followup`` (an
    _LLMResp or an exception instance). Returns (result, llm mock)."""
    reviewer = _build_reviewer({
        'review_prompt': 'review', 'resurrect_prompt': 'resurrect',
        'review_max_boundary_shift': '60',
    })
    main = _resp(
        '[{"start": 120.0, "end": 180.0, "confidence": 0.9, '
        f'"reason": "{TRIM_REASONING}"}}]'
    )
    reviewer._llm_client.messages_create.side_effect = [main, followup]
    ad = {'start': 120.0, 'end': 180.0, 'confidence': 0.9}
    result = reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )
    return result, reviewer._llm_client


def test_contradicted_confirmed_recovers_proposed_bounds():
    result, llm = _run_confirmed_hold(_resp('{"ad_start": 120.0, "ad_end": 150.0}'))
    assert llm.messages_create.call_count == 2
    assert result.verdicts[0].verdict == 'confirmed'
    # Enrichment only: still held, never cut, boundaries untouched.
    assert result.accepted_after_review == []
    held = result.held_by_contradiction[0]
    assert held['held_for_review'] is True
    assert held['was_cut'] is False
    assert held['start'] == 120.0 and held['end'] == 180.0
    assert held['reviewer_proposed_start'] == 120.0
    assert held['reviewer_proposed_end'] == 150.0
    # Stashed on the verdict so _apply_reviewer_verdict_to_ad mirrors it
    # onto the master ad.
    assert result.verdicts[0].adjusted_start == 120.0
    assert result.verdicts[0].adjusted_end == 150.0


def test_contradicted_confirmed_extraction_null_yields_no_bounds():
    # Model says the reasoning names no sub-span -> hold without bounds,
    # exactly the pre-recovery behavior.
    result, llm = _run_confirmed_hold(_resp('null'))
    assert llm.messages_create.call_count == 2
    held = result.held_by_contradiction[0]
    assert held['held_for_review'] is True
    assert 'reviewer_proposed_start' not in held
    assert 'reviewer_proposed_end' not in held
    assert result.accepted_after_review == []


def test_contradicted_confirmed_out_of_range_recovery_rejected():
    # Recovered start is 20s before the original span: hard-reject, hold
    # without bounds rather than trust bad numbers.
    result, _ = _run_confirmed_hold(_resp('{"ad_start": 100.0, "ad_end": 150.0}'))
    held = result.held_by_contradiction[0]
    assert 'reviewer_proposed_start' not in held
    assert 'reviewer_proposed_end' not in held


def test_contradicted_confirmed_tiny_trim_rejected():
    # Recovered ad portion is 2s inside a 60s span, under the
    # MIN_AD_DURATION_FOR_REMOVAL floor: a trim that claims almost none of a
    # span the model confirmed as an ad is more likely a hallucination than a
    # real trim. Hold without bounds rather than pre-fill a bad one-tap trim.
    result, _ = _run_confirmed_hold(_resp('{"ad_start": 120.0, "ad_end": 122.0}'))
    held = result.held_by_contradiction[0]
    assert held['held_for_review'] is True
    assert held['was_cut'] is False
    assert 'reviewer_proposed_start' not in held
    assert 'reviewer_proposed_end' not in held
    assert result.accepted_after_review == []


def test_contradicted_confirmed_full_span_recovery_rejected():
    # Recovering the entire original span is not a trim; offering it as a
    # "trimmed" approval would cut the full span including the show content.
    result, _ = _run_confirmed_hold(_resp('{"ad_start": 120.0, "ad_end": 180.0}'))
    held = result.held_by_contradiction[0]
    assert 'reviewer_proposed_start' not in held
    assert 'reviewer_proposed_end' not in held


def test_contradicted_confirmed_recovery_llm_failure_falls_back():
    result, _ = _run_confirmed_hold(RuntimeError('boom'))
    held = result.held_by_contradiction[0]
    assert held['held_for_review'] is True
    assert held['was_cut'] is False
    assert 'reviewer_proposed_start' not in held
    assert result.accepted_after_review == []


def test_legacy_merged_ad_contradiction_skips_trim_recovery():
    # Regression R3c: _review_single blocks inward shrink on
    # merged_distinct_ads, but trim recovery could still propose a sub-span
    # that drops a still-confirmed sub-ad. A legacy merged ad (flag set,
    # predates member tracking, so no merged_protected_start/end keys) must
    # hold WITHOUT proposed bounds and never spend the recovery LLM call.
    reviewer = _build_reviewer({
        'review_prompt': 'review', 'resurrect_prompt': 'resurrect',
        'review_max_boundary_shift': '60',
    })
    main = _resp(
        '[{"start": 120.0, "end": 180.0, "confidence": 0.9, '
        f'"reason": "{TRIM_REASONING}"}}]'
    )
    followup = _resp('{"ad_start": 120.0, "ad_end": 150.0}')
    reviewer._llm_client.messages_create.side_effect = [main, followup]
    ad = {'start': 120.0, 'end': 180.0, 'confidence': 0.9,
          'merged_distinct_ads': True}
    result = reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )
    assert reviewer._llm_client.messages_create.call_count == 1, (
        "legacy merged ad must not spend the recovery call"
    )
    held = result.held_by_contradiction[0]
    assert held['held_for_review'] is True
    assert held['was_cut'] is False
    assert 'reviewer_proposed_start' not in held
    assert 'reviewer_proposed_end' not in held
    assert result.accepted_after_review == []


def test_merged_ad_with_null_protection_recovers_trim():
    # Tracked merge of differential regions only (both protected keys null):
    # recovery runs and its bounds pass through unclamped, since there is no
    # transcript-anchored member to protect.
    reviewer = _build_reviewer({
        'review_prompt': 'review', 'resurrect_prompt': 'resurrect',
        'review_max_boundary_shift': '60',
    })
    main = _resp(
        '[{"start": 120.0, "end": 180.0, "confidence": 0.9, '
        f'"reason": "{TRIM_REASONING}"}}]'
    )
    followup = _resp('{"ad_start": 120.0, "ad_end": 150.0}')
    reviewer._llm_client.messages_create.side_effect = [main, followup]
    ad = {'start': 120.0, 'end': 180.0, 'confidence': 0.9,
          'merged_distinct_ads': True, 'merged_protected_start': None,
          'merged_protected_end': None}
    result = reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )
    assert reviewer._llm_client.messages_create.call_count == 2
    assert result.verdicts[0].verdict == 'confirmed'
    # Enrichment only: still held, never cut, boundaries untouched.
    assert result.accepted_after_review == []
    held = result.held_by_contradiction[0]
    assert held['held_for_review'] is True
    assert held['was_cut'] is False
    assert held['start'] == 120.0 and held['end'] == 180.0
    assert held['reviewer_proposed_start'] == 120.0
    assert held['reviewer_proposed_end'] == 150.0
    # Stashed on the verdict so _apply_reviewer_verdict_to_ad mirrors it
    # onto the master ad.
    assert result.verdicts[0].adjusted_start == 120.0
    assert result.verdicts[0].adjusted_end == 150.0


def test_merged_ad_recovery_clamped_to_protected_union():
    # Tracked merge with a transcript-anchored member protected at
    # 120.0-170.0 inside span 100.0-200.0. Recovery proposes a sub-span
    # (130.0, 150.0) that would sever the protected member; the recovered
    # bounds stored on the hold must widen back out to the protected union
    # (120.0, 170.0) rather than offer a one-tap trim that drops it.
    reviewer = _build_reviewer({
        'review_prompt': 'review', 'resurrect_prompt': 'resurrect',
        'review_max_boundary_shift': '60',
    })
    main = _resp(
        '[{"start": 100.0, "end": 200.0, "confidence": 0.9, '
        f'"reason": "{TRIM_REASONING}"}}]'
    )
    followup = _resp('{"ad_start": 130.0, "ad_end": 150.0}')
    reviewer._llm_client.messages_create.side_effect = [main, followup]
    ad = {'start': 100.0, 'end': 200.0, 'confidence': 0.9,
          'merged_distinct_ads': True, 'merged_protected_start': 120.0,
          'merged_protected_end': 170.0}
    result = reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )
    assert reviewer._llm_client.messages_create.call_count == 2
    assert result.verdicts[0].verdict == 'confirmed'
    # Enrichment only: still held, never cut, boundaries untouched.
    assert result.accepted_after_review == []
    held = result.held_by_contradiction[0]
    assert held['held_for_review'] is True
    assert held['was_cut'] is False
    assert held['start'] == 100.0 and held['end'] == 200.0
    assert held['reviewer_proposed_start'] == 120.0
    assert held['reviewer_proposed_end'] == 170.0
    # Stashed on the verdict so _apply_reviewer_verdict_to_ad mirrors it
    # onto the master ad.
    assert result.verdicts[0].adjusted_start == 120.0
    assert result.verdicts[0].adjusted_end == 170.0


def test_contradicted_confirmed_without_trim_language_skips_recovery_call():
    # "contains no ad content" identifies nothing to recover; the precheck
    # must not spend a second LLM call.
    reviewer = _build_reviewer({
        'review_prompt': 'review', 'resurrect_prompt': 'resurrect',
        'review_max_boundary_shift': '60',
    })
    reviewer._llm_client.messages_create.return_value = _resp(
        '[{"start": 120.0, "end": 180.0, "confidence": 0.9, '
        '"reason": "This segment contains no advertisement content whatsoever"}]'
    )
    ad = {'start': 120.0, 'end': 180.0, 'confidence': 0.9}
    result = reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )
    assert reviewer._llm_client.messages_create.call_count == 1
    held = result.held_by_contradiction[0]
    assert 'reviewer_proposed_start' not in held


TOSH_REASONING = (
    "The candidate is a genuine ad break containing three back-to-back "
    "sponsor reads: Fabletics (837.2s-910.8s), PestEase (911.4s-956.4s), "
    "and HIMS (956.6s-1024.6s). The original end of 1068.49s overshoots: "
    "at 1040.9s the 'Tosh Show' bumper begins the return to programming. "
    "That interview material is not advertising and should be excluded, "
    "so the end is trimmed back to 1040.9s where the show resumes."
)

DTNS_OUTRO_REASONING = (
    "The span from 2475s to ~2503.6s is the show's own outro. The rest is "
    "a genuine ad break with a Patreon read; the outro portion is not an "
    "ad and the start should move to 2503.6s."
)

AFFIRMED_TRIM_REASONS = [TOSH_REASONING, DTNS_OUTRO_REASONING]


@pytest.mark.parametrize("reason", AFFIRMED_TRIM_REASONS)
def test_affirmed_span_with_boundary_negation_is_not_contradiction(reason):
    assert reasoning_affirms_ad(reason)
    assert not reasoning_contradicts_cut(reason)


@pytest.mark.parametrize("reason", NEGATIVE_REASONS)
def test_whole_span_negations_still_contradict(reason):
    assert not reasoning_affirms_ad(reason)
    assert reasoning_contradicts_cut(reason)


AD_FREE_NEGATIONS = [
    'This span is an ad-free segment; the discussion here is organic '
    'conversation, not advertising',
    'The block is an advertisement-free stretch; it contains no ad content',
    'This is a genuine ad-free zone; it contains no advertising content',
]


@pytest.mark.parametrize("reason", AD_FREE_NEGATIONS)
def test_ad_free_phrasing_is_not_affirmation(reason):
    assert not reasoning_affirms_ad(reason)
    assert reasoning_contradicts_cut(reason)


def test_affirms_ad_requires_assertion_shape():
    # Bare mention of ads or sponsors is not an affirmation of THIS span.
    assert not reasoning_affirms_ad(
        "The host discusses how podcast ads are recorded")
    assert not reasoning_affirms_ad(None)
    assert not reasoning_affirms_ad("")


NEGATED_PHRASE_REASONS = [
    'This is not a genuine ad break; it contains no ad content and is a '
    'false positive from the pattern matcher.',
    'That was not back-to-back sponsor reads, just the host mentioning '
    'past sponsors in passing. This span contains no advertising content.',
]


@pytest.mark.parametrize("reason", NEGATED_PHRASE_REASONS)
def test_negated_phrases_are_not_affirmations(reason):
    assert not reasoning_affirms_ad(reason)
    assert reasoning_contradicts_cut(reason)


# ---------- Affirmed confirm + trim language routes to recovery as adjust ----------
# TOSH_REASONING affirms the span IS an ad (reasoning_affirms_ad is True) while
# also naming a sub-span trim in prose ("trimmed back to 1040.9s"). The
# affirmation guard keeps this out of the contradiction-hold branch entirely;
# it must instead reach the new affirmed-confirm branch, spend one recovery
# call, and ship as an accepted adjust rather than an unchanged confirm.

def _run_affirmed_confirm(followup):
    """Run one review whose main call yields a confirmed verdict with
    unchanged bounds and Tosh-style affirming/trim reasoning, and whose
    follow-up recovery call yields ``followup`` (an _LLMResp or an exception
    instance). Returns (result, llm mock)."""
    reviewer = _build_reviewer({
        'review_prompt': 'review', 'resurrect_prompt': 'resurrect',
        'review_max_boundary_shift': '60',
    })
    main = _resp(
        '[{"start": 837.2, "end": 1068.5, "confidence": 0.95, '
        f'"reason": "{TOSH_REASONING}"}}]'
    )
    reviewer._llm_client.messages_create.side_effect = [main, followup]
    ad = {
        'start': 837.2, 'end': 1068.5, 'confidence': 0.95,
        'detection_stage': 'dai_differential', 'merged_distinct_ads': True,
        'merged_protected_start': None, 'merged_protected_end': None,
    }
    result = reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )
    return result, reviewer._llm_client


def test_affirmed_confirm_with_trim_language_applies_recovered_trim():
    # Reviewer returns unchanged bounds with Tosh-style reasoning. The
    # affirmation guard keeps it out of the hold path; the trim route
    # recovers (837.2, 1040.9) and the ad is accepted with those bounds.
    result, llm = _run_affirmed_confirm(
        _resp('{"ad_start": 837.2, "ad_end": 1040.9}'))
    assert llm.messages_create.call_count == 2
    assert result.held_by_contradiction == []
    accepted = result.accepted_after_review[0]
    assert accepted['start'] == 837.2
    assert accepted['end'] == pytest.approx(1040.9)
    assert accepted['reviewer_verdict'] == 'adjust'
    verdict = result.verdicts[0]
    assert verdict.verdict == 'adjust'
    assert verdict.adjusted_start == 837.2
    assert verdict.adjusted_end == pytest.approx(1040.9)


def test_affirmed_confirm_with_move_phrasing_applies_recovered_trim():
    # DTNS_OUTRO_REASONING affirms the span IS an ad while describing the
    # boundary move in assertion phrasing ("should move to") rather than
    # "trim"/"ends at". _TRIM_LANGUAGE_RE must recognize this phrasing so the
    # affirmed-confirm branch fires the recovery call instead of shipping the
    # unchanged span (which would cut the show's own outro).
    reviewer = _build_reviewer({
        'review_prompt': 'review', 'resurrect_prompt': 'resurrect',
        'review_max_boundary_shift': '60',
    })
    main = _resp(
        '[{"start": 2475.0, "end": 2563.8, "confidence": 0.9, '
        f'"reason": "{DTNS_OUTRO_REASONING}"}}]'
    )
    followup = _resp('{"ad_start": 2503.6, "ad_end": 2563.8}')
    reviewer._llm_client.messages_create.side_effect = [main, followup]
    ad = {'start': 2475.0, 'end': 2563.8, 'confidence': 0.9}
    result = reviewer.review(
        accepted_ads=[ad], resurrection_eligible=[],
        segments=_mock_segments(), episode_meta=_mock_episode_meta(),
        pass_num=1, pass_model='claude-test',
    )
    assert reviewer._llm_client.messages_create.call_count == 2
    assert result.held_by_contradiction == []
    accepted = result.accepted_after_review[0]
    assert accepted['start'] == pytest.approx(2503.6)
    assert accepted['end'] == pytest.approx(2563.8)
    verdict = result.verdicts[0]
    assert verdict.verdict == 'adjust'


def test_affirmed_confirm_recovery_failure_accepts_unchanged():
    # Same setup but the recovery call fails: the ad is accepted with its
    # original bounds, the verdict stays 'confirmed', nothing held.
    # A non-retryable message (unlike "timeout") keeps this test from
    # tripping call_llm's secondary backoff retries, matching the
    # RuntimeError('boom') convention used by the donor recovery-failure
    # test above.
    result, llm = _run_affirmed_confirm(RuntimeError('boom'))
    assert llm.messages_create.call_count == 2
    assert result.held_by_contradiction == []
    accepted = result.accepted_after_review[0]
    assert accepted['start'] == 837.2
    assert accepted['end'] == 1068.5
    assert result.verdicts[0].verdict == 'confirmed'
