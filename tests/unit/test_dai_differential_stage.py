"""Detection candidates from cross-fetch differential regions (Layer 3)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_detector import AdDetector, dai_differential_ads
from config import HOLD_REASON_DIFFERENTIAL_UNCORROBORATED, is_pending_review

_DIFF = {'status': 'ok', 'regions': [
    {'start_s': 100.0, 'end_s': 160.0, 'kind': 'differential', 'corr': 0.0},
    {'start_s': 0.0, 'end_s': 100.0, 'kind': 'identical', 'corr': 0.99},
]}


def test_differential_with_stage_overlap_becomes_cut():
    # #541: a differential region that overlaps another stage's marker is
    # corroborated and cuts exactly as before -- 0.95, not held.
    ads = dai_differential_ads(_DIFF, [], corroborating_spans=[(110.0, 150.0)])
    assert len(ads) == 1
    ad = ads[0]
    assert ad['start'] == 100.0
    assert ad['end'] == 160.0
    assert ad['confidence'] == 0.95
    assert ad['detection_stage'] == 'dai_differential'
    assert ad.get('held_for_review') is not True
    assert 'differential_uncorroborated' not in ad
    assert 'corroborated by overlapping ad marker' in ad['reason']


def test_uncorroborated_differential_is_held_not_cut_not_dropped():
    # The #541 false positive AND the transcript-less real-DAI case share one
    # shape: audio differs, no other signal. Do not silently cut, do not drop --
    # hold for review so it surfaces for one-tap approval.
    for ads in (dai_differential_ads(_DIFF, []),
                dai_differential_ads(_DIFF, [], corroborating_spans=[])):
        assert len(ads) == 1
        ad = ads[0]
        assert ad['start'] == 100.0
        assert ad['end'] == 160.0
        assert ad['detection_stage'] == 'dai_differential'
        assert ad['held_for_review'] is True
        assert ad['was_cut'] is False
        assert ad['hold_reason'] == HOLD_REASON_DIFFERENTIAL_UNCORROBORATED
        assert ad['differential_uncorroborated'] is True
        assert '-- review' in ad['reason']
        # The held marker must satisfy the pending-review predicate so the
        # existing review UI and pending_review_count pick it up unchanged.
        assert is_pending_review(ad) is True


def test_transcriptless_dai_ad_overlapping_marker_still_cuts():
    # Regression the review caught: a genuine DAI ad is often dynamically
    # served and never transcribed, so it has NO ad-language text. As long as
    # it overlaps a fingerprint/LLM marker it must still cut, not hold.
    ads = dai_differential_ads(_DIFF, [], corroborating_spans=[(105.0, 155.0)])
    assert len(ads) == 1
    assert ads[0].get('held_for_review') is not True
    assert ads[0]['confidence'] == 0.95


def test_false_positive_regions_excluded():
    # A user false-positive span excludes the region regardless of corroboration.
    assert dai_differential_ads(_DIFF, [(95.0, 165.0)],
                                corroborating_spans=[(110.0, 150.0)]) == []
    assert dai_differential_ads(_DIFF, [(95.0, 165.0)]) == []


def test_none_and_empty_inputs():
    assert dai_differential_ads(None, []) == []
    assert dai_differential_ads({'regions': []}, []) == []


def test_merge_prefers_differential_stage_and_confidence():
    detector = AdDetector(api_key='test-key')
    merged = detector._merge_detection_results([
        {'start': 98.0, 'end': 150.0, 'confidence': 0.70,
         'reason': 'Sponsor read', 'detection_stage': 'claude'},
        {'start': 100.0, 'end': 160.0, 'confidence': 0.95,
         'reason': 'Dynamically inserted: audio differs across fetches',
         'sponsor': None, 'detection_stage': 'dai_differential'},
    ])
    assert len(merged) == 1
    assert merged[0]['detection_stage'] == 'dai_differential'
    assert merged[0]['confidence'] == 0.95
    assert merged[0]['end'] == 160.0


def test_merge_clears_held_state_when_claude_verifies_transcribed_span():
    # A held uncorroborated differential that a Claude ad overlaps upgrades to
    # a cut ONLY when the span has transcript coverage: claude saw the region
    # as a prompt hint, so its flag is independent evidence only when there was
    # ad text to read (#541). Held differential sorts first (start 100).
    detector = AdDetector(api_key='test-key')
    segments = [
        {'start': 95.0, 'end': 130.0, 'text': 'this episode is sponsored by BetterHelp'},
        {'start': 130.0, 'end': 158.0, 'text': 'get ten percent off your first month'},
    ]
    merged = detector._merge_detection_results([
        {'start': 100.0, 'end': 160.0, 'confidence': 0.95,
         'reason': 'Audio differs across fetches; no other ad signal -- review',
         'sponsor': None, 'detection_stage': 'dai_differential',
         'held_for_review': True, 'was_cut': False,
         'hold_reason': HOLD_REASON_DIFFERENTIAL_UNCORROBORATED,
         'differential_uncorroborated': True},
        {'start': 102.0, 'end': 150.0, 'confidence': 0.80,
         'reason': 'BetterHelp sponsor read', 'sponsor': 'BetterHelp',
         'detection_stage': 'claude'},
    ], segments=segments)
    assert len(merged) == 1
    assert merged[0].get('held_for_review') is not True
    assert 'differential_uncorroborated' not in merged[0]
    assert 'hold_reason' not in merged[0]


def test_merge_keeps_hold_when_claude_echoes_untranscribed_span():
    # The echo-chamber case (#541): the differential span is untranscribed
    # (music bed), so an overlapping claude flag can only be an echo of the
    # prompt hint, not verification. The merged marker must STAY held.
    detector = AdDetector(api_key='test-key')
    merged = detector._merge_detection_results([
        {'start': 100.0, 'end': 160.0, 'confidence': 0.95,
         'reason': 'Audio differs across fetches; no other ad signal -- review',
         'sponsor': None, 'detection_stage': 'dai_differential',
         'held_for_review': True, 'was_cut': False,
         'hold_reason': HOLD_REASON_DIFFERENTIAL_UNCORROBORATED,
         'differential_uncorroborated': True},
        {'start': 102.0, 'end': 150.0, 'confidence': 0.80,
         'reason': 'Likely dynamically inserted ad', 'sponsor': None,
         'detection_stage': 'claude'},
    ], segments=[])
    assert len(merged) == 1
    assert merged[0]['held_for_review'] is True
    assert merged[0]['differential_uncorroborated'] is True
    assert merged[0]['hold_reason'] == HOLD_REASON_DIFFERENTIAL_UNCORROBORATED


def test_merge_hold_survives_fold_when_differential_is_second():
    # Ordering hole: when the held differential is `current` folding into a
    # claude `last`, the fold previously dropped its hold fields silently.
    # With no transcript coverage the merged marker must carry the hold.
    detector = AdDetector(api_key='test-key')
    merged = detector._merge_detection_results([
        {'start': 100.0, 'end': 150.0, 'confidence': 0.80,
         'reason': 'Likely dynamically inserted ad', 'sponsor': None,
         'detection_stage': 'claude'},
        {'start': 102.0, 'end': 160.0, 'confidence': 0.95,
         'reason': 'Audio differs across fetches; no other ad signal -- review',
         'sponsor': None, 'detection_stage': 'dai_differential',
         'held_for_review': True, 'was_cut': False,
         'hold_reason': HOLD_REASON_DIFFERENTIAL_UNCORROBORATED,
         'differential_uncorroborated': True},
    ], segments=[])
    assert len(merged) == 1
    assert merged[0]['held_for_review'] is True
    assert merged[0]['differential_uncorroborated'] is True


def test_merge_independent_stage_upgrades_hold_without_transcript():
    # Fingerprint corroboration is independent of the prompt hint, so it
    # upgrades the hold to a cut even on an untranscribed span.
    detector = AdDetector(api_key='test-key')
    merged = detector._merge_detection_results([
        {'start': 100.0, 'end': 160.0, 'confidence': 0.95,
         'reason': 'Audio differs across fetches; no other ad signal -- review',
         'sponsor': None, 'detection_stage': 'dai_differential',
         'held_for_review': True, 'was_cut': False,
         'hold_reason': HOLD_REASON_DIFFERENTIAL_UNCORROBORATED,
         'differential_uncorroborated': True},
        {'start': 102.0, 'end': 150.0, 'confidence': 0.90,
         'reason': 'Known ad fingerprint', 'sponsor': 'BetterHelp',
         'detection_stage': 'fingerprint'},
    ], segments=[])
    assert len(merged) == 1
    assert merged[0].get('held_for_review') is not True
    assert 'differential_uncorroborated' not in merged[0]


def test_merge_keeps_held_state_when_only_differentials_overlap():
    # Two uncorroborated differentials overlapping each other are still
    # uncorroborated: the merge must not treat one differential as corroborating
    # the other, so the held state survives.
    detector = AdDetector(api_key='test-key')
    merged = detector._merge_detection_results([
        {'start': 100.0, 'end': 160.0, 'confidence': 0.95,
         'reason': 'Audio differs across fetches; no other ad signal -- review',
         'sponsor': None, 'detection_stage': 'dai_differential',
         'held_for_review': True, 'was_cut': False,
         'hold_reason': HOLD_REASON_DIFFERENTIAL_UNCORROBORATED,
         'differential_uncorroborated': True},
        {'start': 150.0, 'end': 200.0, 'confidence': 0.95,
         'reason': 'Audio differs across fetches; no other ad signal -- review',
         'sponsor': None, 'detection_stage': 'dai_differential',
         'held_for_review': True, 'was_cut': False,
         'hold_reason': HOLD_REASON_DIFFERENTIAL_UNCORROBORATED,
         'differential_uncorroborated': True},
    ])
    assert len(merged) == 1
    assert merged[0]['differential_uncorroborated'] is True
    assert merged[0]['held_for_review'] is True


def test_merge_nulls_claude_sponsor_when_dai_reason_is_longer():
    # PIN current behavior: the merge keeps reason+sponsor as a consistent pair
    # from the member with the LONGER reason (fingerprint-mirror heuristic). The
    # corroborated-cut DAI reason is longer than a short Claude reason, so the
    # Claude ad's real sponsor is dropped with it (sponsor becomes None). This
    # is the spec-mandated pattern; the test exists to make the loss visible.
    detector = AdDetector(api_key='test-key')
    dai_reason = ('Dynamically inserted: audio differs across fetches '
                  '(corroborated by overlapping ad marker)')
    merged = detector._merge_detection_results([
        {'start': 100.0, 'end': 150.0, 'confidence': 0.70,
         'reason': 'BetterHelp read', 'sponsor': 'BetterHelp',
         'detection_stage': 'claude'},
        {'start': 102.0, 'end': 160.0, 'confidence': 0.95,
         'reason': dai_reason,
         'sponsor': None, 'detection_stage': 'dai_differential'},
    ])
    assert len(merged) == 1
    assert merged[0]['detection_stage'] == 'dai_differential'
    assert merged[0]['confidence'] == 0.95
    assert merged[0]['sponsor'] is None
    assert merged[0]['reason'] == dai_reason


def test_merge_does_not_fold_adjacent_held_differential_into_real_ad():
    # Adjacency is not corroboration (#541): a held differential ENDING 1s
    # before a real Claude ad must not merge with it -- merging would cut the
    # held span (or hold the ad). Both markers survive independently.
    detector = AdDetector(api_key='test-key')
    merged = detector._merge_detection_results([
        {'start': 100.0, 'end': 160.0, 'confidence': 0.95,
         'reason': 'Audio differs across fetches; no other ad signal -- review',
         'sponsor': None, 'detection_stage': 'dai_differential',
         'held_for_review': True, 'was_cut': False,
         'hold_reason': HOLD_REASON_DIFFERENTIAL_UNCORROBORATED,
         'differential_uncorroborated': True},
        {'start': 161.0, 'end': 200.0, 'confidence': 0.85,
         'reason': 'BetterHelp sponsor read', 'sponsor': 'BetterHelp',
         'detection_stage': 'claude'},
    ], segments=[])
    assert len(merged) == 2
    assert merged[0]['held_for_review'] is True
    assert merged[0]['differential_uncorroborated'] is True
    assert merged[1].get('held_for_review') is not True
