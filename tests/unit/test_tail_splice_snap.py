"""Post-review tail recovery through untranscribed sonic logos."""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_detector.boundaries import (
    _merge_ad_pair,
    snap_extended_ad_tails_to_splice,
)
from ad_validator import AdValidator, ValidationResult
from main_app import processing


def _event(time, depth=-120.0, event_type='digital_silence'):
    return {
        'time': time,
        'end_time': time + 0.6,
        'type': event_type,
        'depth_dbfs': depth,
        'duration_s': 0.6,
    }


def _fixture():
    marker = {
        'start': 2360.85,
        'end': 2410.9,
        'confidence': 0.97,
        'reason': "McDonald's dynamically inserted ad",
        'end_extended_by_content': True,
    }
    segments = [
        {
            'start': 2400.0,
            'end': 2410.9,
            'text': "McDonald's is proud to be run and owned locally.",
        },
        {
            'start': 2423.29,
            'end': 2450.27,
            'text': 'Can you give me your quintessential PlayStation 3 experiences?',
        },
    ]
    return marker, segments


def test_content_extended_tail_reaches_forward_splice():
    marker, segments = _fixture()

    result = snap_extended_ad_tails_to_splice(
        [marker], segments, [_event(2415.85)], window_s=10.0)

    assert result[0]['end'] == 2415.85
    assert result[0]['tail_splice_snap'] == {
        'original_end': 2410.9,
        'event_time': 2415.85,
        'event_type': 'digital_silence',
        'depth_dbfs': -120.0,
    }


def test_plain_content_between_tail_and_splice_blocks_extension():
    marker, segments = _fixture()
    segments.insert(1, {
        'start': 2412.0,
        'end': 2414.0,
        'text': 'Back to the show and our next question.',
    })

    result = snap_extended_ad_tails_to_splice(
        [marker], segments, [_event(2415.85)], window_s=10.0)

    assert result[0]['end'] == 2410.9
    assert 'tail_splice_snap' not in result[0]


def test_marker_without_content_extension_is_not_eligible():
    marker, segments = _fixture()
    marker.pop('end_extended_by_content')

    result = snap_extended_ad_tails_to_splice(
        [marker], segments, [_event(2415.85)], window_s=10.0)

    assert result[0]['end'] == 2410.9


def test_tail_snap_does_not_cross_next_marker():
    marker, segments = _fixture()
    next_marker = {'start': 2414.0, 'end': 2420.0, 'reason': 'separate marker'}

    result = snap_extended_ad_tails_to_splice(
        [marker], segments, [_event(2415.85)], window_s=10.0,
        coverage_ads=[marker, next_marker])

    assert result[0]['end'] == 2410.9


def test_overlapping_marker_blocks_tail_extension_immediately():
    marker, segments = _fixture()
    overlapping_marker = {
        'start': 2409.0,
        'end': 2414.0,
        'reason': 'separate overlapping marker',
    }

    result = snap_extended_ad_tails_to_splice(
        [marker], segments, [_event(2412.0)], window_s=10.0,
        coverage_ads=[marker, overlapping_marker])

    assert result[0]['end'] == 2410.9


def test_later_marker_does_not_hide_content_before_proposed_splice():
    marker = {
        'start': 70.0,
        'end': 100.0,
        'confidence': 0.97,
        'reason': 'Sponsor ad',
        'end_extended_by_content': True,
    }
    next_marker = {
        'start': 110.0,
        'end': 130.0,
        'reason': 'Separate ad marker',
    }
    segments = [{
        'start': 105.0,
        'end': 115.0,
        'text': 'Now we return to the game analysis.',
    }]

    result = snap_extended_ad_tails_to_splice(
        [marker], segments, [_event(108.0)], window_s=10.0,
        coverage_ads=[marker, next_marker])

    assert result[0]['end'] == 100.0
    assert 'tail_splice_snap' not in result[0]


def test_non_silence_and_far_events_are_ignored():
    marker, segments = _fixture()
    events = [
        _event(2415.85, event_type='loudness_step'),
        _event(2421.0),
    ]

    result = snap_extended_ad_tails_to_splice(
        [marker], segments, events, window_s=10.0)

    assert result[0]['end'] == 2410.9


def test_merge_clears_stale_content_extension_from_earlier_fragment():
    ads = [
        {
            'start': 100.0,
            'end': 130.0,
            'confidence': 0.9,
            'reason': 'Sponsor ad part one',
            'end_extended_by_content': True,
        },
        {
            'start': 132.0,
            'end': 160.0,
            'confidence': 0.9,
            'reason': 'Sponsor ad part two',
        },
    ]

    merged = ads[0].copy()
    _merge_ad_pair(merged, ads[1])

    assert merged['end'] == 160.0
    assert 'end_extended_by_content' not in merged


def test_merge_preserves_content_extension_on_later_fragment():
    ads = [
        {
            'start': 100.0,
            'end': 130.0,
            'confidence': 0.9,
            'reason': 'Sponsor ad part one',
        },
        {
            'start': 132.0,
            'end': 160.0,
            'confidence': 0.9,
            'reason': 'Sponsor ad part two',
            'end_extended_by_content': True,
        },
    ]

    merged = ads[0].copy()
    _merge_ad_pair(merged, ads[1])

    assert merged['end_extended_by_content'] is True


@pytest.mark.parametrize('gap', [2.0, 8.0])
def test_validator_merge_clears_stale_earlier_tail_provenance(gap):
    earlier = {
        'start': 100.0,
        'end': 130.0,
        'confidence': 0.9,
        'reason': 'Sponsor ad part one',
        'end_extended_by_content': True,
        'tail_splice_snap': {'event_time': 130.0},
    }
    later = {
        'start': 130.0 + gap,
        'end': 160.0,
        'confidence': 0.9,
        'reason': 'Sponsor ad part two',
    }
    validator = AdValidator(
        episode_duration=300.0,
        segments=[{'start': 0.0, 'end': 1.0, 'text': 'intro'}],
    )

    merged = validator._merge_close_ads(
        [earlier, later], ValidationResult(ads=[]))[0]

    assert merged['end'] == 160.0
    assert 'end_extended_by_content' not in merged
    assert 'tail_splice_snap' not in merged


def test_validator_merge_inherits_later_tail_provenance():
    earlier = {
        'start': 100.0,
        'end': 130.0,
        'confidence': 0.9,
        'reason': 'Sponsor ad part one',
    }
    later_snap = {'event_time': 160.0, 'original_end': 158.0}
    later = {
        'start': 132.0,
        'end': 160.0,
        'confidence': 0.9,
        'reason': 'Sponsor ad part two',
        'end_extended_by_content': True,
        'tail_splice_snap': later_snap,
    }
    validator = AdValidator(episode_duration=300.0, segments=[])

    merged = validator._merge_close_ads(
        [earlier, later], ValidationResult(ads=[]))[0]

    assert merged['end_extended_by_content'] is True
    assert merged['tail_splice_snap'] == later_snap
    assert merged['tail_splice_snap'] is not later_snap


def test_processing_skips_destructive_tail_snap_during_cold_start(monkeypatch):
    marker, segments = _fixture()

    def unexpected_snap(*args, **kwargs):
        raise AssertionError('cold-start events must not drive a cut extension')

    monkeypatch.setattr(
        processing, 'snap_extended_ad_tails_to_splice', unexpected_snap)
    analysis = SimpleNamespace(splice_evidence={
        'events': [_event(2415.85)],
        'calibration': {'status': 'cold_start'},
    })

    result = processing._snap_completed_cut_tails_to_splice(
        'feed', 'episode', [marker], [marker], segments, analysis)

    assert result == [marker]


def test_processing_allows_tail_snap_after_calibration(monkeypatch):
    marker, segments = _fixture()
    snapped = dict(marker, end=2415.85, tail_splice_snap={
        'original_end': 2410.9,
        'event_time': 2415.85,
        'event_type': 'digital_silence',
        'depth_dbfs': -120.0,
    })
    monkeypatch.setattr(
        processing, 'snap_extended_ad_tails_to_splice',
        lambda *args, **kwargs: [snapped])
    saves = []
    monkeypatch.setattr(
        processing.storage, 'save_combined_ads',
        lambda *args: saves.append(args))
    analysis = SimpleNamespace(splice_evidence={
        'events': [_event(2415.85)],
        'calibration': {'status': 'calibrated'},
    })

    result = processing._snap_completed_cut_tails_to_splice(
        'feed', 'episode', [marker], [marker], segments, analysis)

    assert result[0]['end'] == 2415.85
    assert marker['end'] == 2415.85
    assert marker['tail_splice_snap']['event_time'] == 2415.85
    assert len(saves) == 1
