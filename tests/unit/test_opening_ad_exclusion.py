"""Opening-window exclusion for ad markers."""
from tests.app_bootstrap import bootstrap

bootstrap('opening_ad_exclusion_test_')

from main_app.pipeline.detect import _exclude_opening_ads


def test_excludes_ads_starting_in_opening_window():
    ads = [
        {'start': 30.0, 'end': 60.0},
        {'start': 119.9, 'end': 180.0},
        {'start': 120.0, 'end': 150.0},
    ]

    assert _exclude_opening_ads(ads, 120.0) == [ads[2]]
    assert _exclude_opening_ads(ads, 0) == ads


def _built_validator(monkeypatch, seconds):
    from main_app import processing
    from main_app.pipeline import detect

    monkeypatch.setattr(
        processing.db, 'get_setting_float',
        lambda key, default=0.0: seconds if key == 'ad_detection_exclude_start_seconds' else default)
    segments = [{'start': 3.0, 'end': 20.0, 'text': 'A word from our sponsor.',
                 'words': [{'start': 3.0, 'end': 4.0}]}]
    return detect._build_validator(
        600.0, segments, '', false_positive_corrections=[], min_cut_confidence=0.8,
        max_ad_duration_override=None, cue_gate_enabled=False)


def test_leading_extension_is_off_when_the_feed_excludes_its_opening(monkeypatch):
    from ad_validator import ValidationResult

    validator = _built_validator(monkeypatch, 2.0)
    head = {'start': 3.0, 'end': 20.0}

    validator._extend_leading_ad([head], ValidationResult(ads=[]))

    assert head['start'] == 3.0


def test_leading_extension_stays_on_without_an_opening_exclusion(monkeypatch):
    from ad_validator import ValidationResult

    validator = _built_validator(monkeypatch, 0.0)
    head = {'start': 3.0, 'end': 20.0}

    validator._extend_leading_ad([head], ValidationResult(ads=[]))

    assert head['start'] == 0.0
