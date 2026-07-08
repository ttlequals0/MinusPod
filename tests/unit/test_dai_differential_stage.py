"""Detection candidates from cross-fetch differential regions (Layer 3)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_detector import AdDetector, dai_differential_ads

_DIFF = {'status': 'ok', 'regions': [
    {'start_s': 100.0, 'end_s': 160.0, 'kind': 'differential', 'corr': 0.0},
    {'start_s': 0.0, 'end_s': 100.0, 'kind': 'identical', 'corr': 0.99},
]}


def test_only_differential_regions_become_markers():
    ads = dai_differential_ads(_DIFF, [])
    assert len(ads) == 1
    ad = ads[0]
    assert ad['start'] == 100.0
    assert ad['end'] == 160.0
    assert ad['confidence'] == 0.95
    assert ad['detection_stage'] == 'dai_differential'


def test_false_positive_regions_excluded():
    ads = dai_differential_ads(_DIFF, [(95.0, 165.0)])
    assert ads == []


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
