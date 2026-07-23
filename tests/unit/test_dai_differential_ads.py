"""Measured-corr gating and hold duration floor for differential candidates
(2.76.0). Complements test_dai_differential_stage.py, which pins the
corroboration/hold marker shapes."""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_detector import AdDetector, dai_differential_ads


def _diff(*regions):
    return {'status': 'ok', 'regions': list(regions)}


def _region(start, end, corr, kind='differential'):
    return {'start_s': start, 'end_s': end, 'kind': kind, 'corr': corr}


def test_high_corr_region_is_never_a_candidate():
    # corr 0.9 means the audio mostly matched across fetches: not a
    # differential candidate, corroborated or not.
    diff = _diff(_region(100.0, 160.0, 0.9))
    assert dai_differential_ads(diff, []) == []
    assert dai_differential_ads(
        diff, [], corroborating_spans=[(110.0, 150.0)]) == []


def test_low_corr_long_uncorroborated_region_is_held():
    ads = dai_differential_ads(_diff(_region(100.0, 130.0, 0.2)), [])
    assert len(ads) == 1
    assert ads[0]['held_for_review'] is True
    assert ads[0]['start'] == 100.0
    assert ads[0]['end'] == 130.0


def test_short_uncorroborated_region_below_floor_is_skipped():
    # 5s < hold_min_seconds 10: no marker at all (not held, not cut).
    ads = dai_differential_ads(_diff(_region(100.0, 105.0, 0.2)), [],
                               hold_min_seconds=10.0)
    assert ads == []


def test_zero_floor_holds_short_region():
    ads = dai_differential_ads(_diff(_region(100.0, 105.0, 0.2)), [],
                               hold_min_seconds=0.0)
    assert len(ads) == 1
    assert ads[0]['held_for_review'] is True


def test_short_corroborated_region_cuts_regardless_of_duration():
    ads = dai_differential_ads(_diff(_region(100.0, 105.0, 0.2)), [],
                               corroborating_spans=[(101.0, 104.0)],
                               hold_min_seconds=10.0)
    assert len(ads) == 1
    assert ads[0]['confidence'] == 0.95
    assert ads[0].get('held_for_review') is not True


def test_legacy_zero_corr_region_still_qualifies():
    # Compat pin: pre-2.76.0 stored differentials hard-coded corr 0.0 on
    # every differential region. 0.0 <= measured_corr_max, so recuts of old
    # episodes behave as before.
    ads = dai_differential_ads(_diff(_region(100.0, 160.0, 0.0)), [])
    assert len(ads) == 1
    assert ads[0]['held_for_review'] is True
    ads = dai_differential_ads(_diff(_region(100.0, 160.0, 0.0)), [],
                               corroborating_spans=[(110.0, 150.0)])
    assert len(ads) == 1
    assert ads[0].get('held_for_review') is not True


def test_unknown_kind_and_none_corr_are_never_candidates():
    diff = _diff(_region(100.0, 160.0, None, kind='unknown'),
                 _region(200.0, 260.0, None))
    assert dai_differential_ads(diff, []) == []
    assert dai_differential_ads(
        diff, [], corroborating_spans=[(110.0, 250.0)]) == []


def test_measured_corr_max_is_tunable():
    diff = _diff(_region(100.0, 160.0, 0.7))
    assert dai_differential_ads(diff, []) == []
    ads = dai_differential_ads(diff, [], measured_corr_max=0.75)
    assert len(ads) == 1


def test_borderline_adjacent_block_does_not_veto_qualifying_block():
    # Per-block gating: a break spanning two blocks where one member is
    # borderline (0.65 > threshold 0.60) still mints a candidate for the
    # qualifying block, and the borderline block does not ride along.
    diff = _diff(_region(100.0, 112.0, 0.2), _region(112.0, 120.0, 0.65))
    ads = dai_differential_ads(diff, [], measured_corr_max=0.60)
    assert len(ads) == 1
    assert ads[0]['start'] == 100.0
    assert ads[0]['end'] == 112.0


def test_touching_qualifying_blocks_merge_and_jointly_beat_floor():
    # Two sub-floor qualifying blocks (6s each) that touch merge into one
    # 12s span that beats the 10s hold floor jointly.
    diff = _diff(_region(100.0, 106.0, 0.2), _region(106.0, 112.0, 0.3))
    ads = dai_differential_ads(diff, [], hold_min_seconds=10.0)
    assert len(ads) == 1
    assert ads[0]['start'] == 100.0
    assert ads[0]['end'] == 112.0
    assert ads[0]['held_for_review'] is True


def test_non_touching_qualifying_blocks_stay_separate():
    # A non-candidate block between qualifying blocks keeps them separate;
    # each falls under the floor alone and is skipped.
    diff = _diff(_region(100.0, 106.0, 0.2),
                 _region(106.0, 130.0, 0.9, kind='identical'),
                 _region(130.0, 136.0, 0.3))
    assert dai_differential_ads(diff, [], hold_min_seconds=10.0) == []


def test_cue_marks_accepted_but_unused():
    # Task 7 will consume cue_marks for boundary snapping; this task only
    # pins that passing it neither errors nor changes the output.
    diff = _diff(_region(100.0, 130.0, 0.2))
    with_marks = dai_differential_ads(diff, [], cue_marks=[101.5, 128.0])
    without = dai_differential_ads(diff, [])
    assert with_marks == without


def test_call_site_passes_settings_thresholds():
    # process_transcript stage 2.5 reads both thresholds from settings at
    # detection time via db.get_setting_float.
    detector = AdDetector(api_key='test-key')
    detector.db = MagicMock()
    values = {'differential_measured_corr_max': 0.42,
              'differential_hold_min_seconds': 7.5}
    detector.db.get_setting_float.side_effect = (
        lambda key, default=0.0: values.get(key, default))
    captured = {}

    def fake_dd(dai_differential, fp_pairs, corroborating_spans=None, **kw):
        captured.update(kw)
        return []

    with patch.object(detector, 'initialize_client'), \
         patch('ad_detector.dai_differential_ads', side_effect=fake_dd), \
         patch.object(detector, 'detect_ads',
                      return_value={'ads': [], 'status': 'success',
                                    'raw_response': '', 'model': 'm'}):
        detector.process_transcript(
            [{'start': 0.0, 'end': 60.0, 'text': 'hello'}],
            slug='s', episode_id='e1', skip_patterns=True,
            dai_differential=_diff(_region(10.0, 40.0, 0.2)),
            keep_content=False)

    assert captured['measured_corr_max'] == 0.42
    assert captured['hold_min_seconds'] == 7.5
