"""A known ad (pattern, fingerprint, known sponsor, confirmation, reviewer verdict) always cuts."""
from unittest.mock import patch

from tests.app_bootstrap import bootstrap
bootstrap('known_ad_keep_test_')

from ad_detector import AdDetector
from config import is_known_ad
from main_app.pipeline import cuts as cuts_stage
from main_app.pipeline import verify
from main_app.verification_reconciliation import _exclude_kept_spans_from_verification

KEEP_OUTRO_MAP = {'sponsor': 'remove', 'interaction': 'remove', 'cross_promo': 'keep',
                  'self_promo': 'keep', 'intro': 'keep', 'outro': 'keep', 'recap': 'keep'}
EPISODE_END = 2532.1
SPOT_START = 2474.68


def _outro_estimate():
    return {'start': 2425.52, 'end': 2483.7, 'confidence': 0.885, 'category': 'outro',
            'detection_stage': 'text_pattern', 'pattern_id': 600, 'pattern_defined': False,
            'span_estimated': True, 'text_start': 2425.52, 'text_end': 2435.76,
            'reason': 'Show sign-off (estimated span)'}


def _fingerprint_spot():
    return {'start': SPOT_START, 'end': EPISODE_END, 'confidence': 0.79, 'category': 'sponsor',
            'detection_stage': 'fingerprint', 'pattern_id': 1875, 'pattern_defined': False,
            'fingerprint_match_start': SPOT_START, 'fingerprint_match_end': EPISODE_END,
            'reason': 'Sponsor spot fingerprint'}


def _merge(ads):
    return AdDetector.__new__(AdDetector)._merge_detection_results(
        ads, None, action_map=KEEP_OUTRO_MAP)


def test_known_ad_truth_table():
    assert is_known_ad({'category': 'sponsor', 'fingerprint_match_start': 1.0})
    assert is_known_ad({'category': 'sponsor', 'merged_member_spans': [
        {'start': 1.0, 'end': 2.0, 'stage': 'fingerprint'}]})
    assert is_known_ad({'category': 'sponsor', 'pattern_id': 5, 'sponsor': 'Acme'})
    assert is_known_ad({'category': 'cross_promo', 'pattern_defined': True})
    assert is_known_ad({'sponsor_known': True})
    assert is_known_ad({'category': 'sponsor', 'reviewer_verdict': 'confirmed'})
    assert is_known_ad({'category': 'sponsor', 'validation': {'sponsor_confirmed': True}})
    assert not is_known_ad({'category': 'sponsor', 'pattern_id': 5})
    assert not is_known_ad({'category': 'sponsor', 'confidence': 0.99})
    assert not is_known_ad({'category': 'outro', 'pattern_id': 600, 'sponsor': 'Show'})


def test_estimated_outro_keep_yields_to_a_fingerprinted_spot():
    merged = _merge([_outro_estimate(), _fingerprint_spot()])
    spans = [(round(a['start'], 2), round(a['end'], 2), a['category']) for a in merged]
    assert spans == [(2425.52, SPOT_START, 'outro'), (SPOT_START, EPISODE_END, 'sponsor')]


def test_kept_audio_never_carves_a_known_ad():
    keep = {'start': 2425.52, 'end': 2483.7, 'category': 'outro', 'was_cut': False}
    spot = _fingerprint_spot()
    carved = cuts_stage._carve_cuts_around([spot], [keep, spot], [keep])
    assert carved == [spot]


def test_kept_audio_still_carves_an_unbacked_llm_ad():
    keep = {'start': 2425.52, 'end': 2483.7, 'category': 'outro', 'was_cut': False}
    guess = {'start': 2464.3, 'end': EPISODE_END, 'category': 'sponsor',
             'detection_stage': 'claude', 'confidence': 0.98}
    carved = cuts_stage._carve_cuts_around([guess], [keep, guess], [keep])
    assert [(round(a['start'], 2), round(a['end'], 2)) for a in carved] == [(2483.7, EPISODE_END)]


def test_pass2_known_sponsor_survives_a_kept_span():
    kept = [{'start': 2425.52, 'end': 2483.7, 'category': 'outro', 'was_cut': False}]
    processed = [{'start': 2446.7, 'end': 2483.7, 'category': 'sponsor', 'sponsor_known': True}]
    original = [dict(processed[0])]
    kept_processed, kept_original, conflicts = _exclude_kept_spans_from_verification(
        processed, original, kept, [])
    assert kept_original == original and kept_processed == processed and conflicts == []


def test_pass2_unknown_sponsor_inside_a_kept_span_is_dropped():
    kept = [{'start': 2425.52, 'end': 2483.7, 'category': 'outro', 'was_cut': False}]
    processed = [{'start': 2446.7, 'end': 2483.7, 'category': 'sponsor'}]
    original = [dict(processed[0])]
    kept_processed, kept_original, _ = _exclude_kept_spans_from_verification(
        processed, original, kept, [])
    assert kept_original == [] and kept_processed == []


def test_kept_spans_yield_to_a_known_pass2_ad_before_protection_is_built():
    kept = [{'start': 2425.52, 'end': 2483.7, 'category': 'outro', 'was_cut': False}]
    known = {'start': 2446.7, 'end': 2483.7, 'category': 'sponsor', 'sponsor_known': True}
    guess = {'start': 2430.0, 'end': 2440.0, 'category': 'sponsor'}
    trimmed = verify._keeps_yielding_to_known_ads(kept, [known, guess])
    assert [(k['start'], k['end']) for k in trimmed] == [(2425.52, 2446.7)]
    protection = verify.build_protection(kept=trimmed, category_kept=[], user_trims=[],
                                         fp_corrections=[], holds=[], pass1_cuts=[])
    assert protection.hard_orig == [{'start': 2425.52, 'end': 2446.7}]
    # A known ad that validation dropped gives the keep its audio back.
    assert verify._keeps_yielding_to_known_ads(kept, [guess]) == kept


def test_known_beep_ad_owns_the_overlap_too():
    from ad_detector.boundaries import split_conflicting_action_span
    keep = {'start': 100.0, 'end': 150.0, 'category': 'outro'}
    spot = {'start': 130.0, 'end': 170.0, 'category': 'sponsor', 'fingerprint_match_start': 130.0}
    new_last, entries = split_conflicting_action_span(keep, spot, 'keep', 'beep')
    assert new_last['end'] == 130.0 and entries == [spot]


def test_pass2_ads_are_stamped_when_the_feed_has_the_sponsor():
    processed = [{'start': 1.0, 'end': 2.0, 'sponsor': 'Capital One'},
                 {'start': 3.0, 'end': 4.0, 'sponsor': 'Nobody'}]
    original = [dict(a) for a in processed]
    with patch.object(verify, 'db') as database:
        database.get_active_pattern_sponsors.return_value = {'capital one'}
        verify._stamp_known_sponsors(processed, original)
    assert processed[0].get('sponsor_known') and original[0].get('sponsor_known')
    assert 'sponsor_known' not in processed[1] and 'sponsor_known' not in original[1]
