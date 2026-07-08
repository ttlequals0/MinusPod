"""Unit tests for vad_gap tail corroboration wiring and review visibility
(ad-splice-detection spec 1.1/1.3)."""
import json
import os
import sys
import tempfile

import pytest

# Bind a temp data dir via env so importing main_app does not mkdir /app/data
# (same isolation pattern as test_recut_ad_list.py).
_test_data_dir = tempfile.mkdtemp(prefix='vad_tail_test_')
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.setdefault('MINUSPOD_DATA_DIR', _test_data_dir)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import ad_validator
from ad_validator import AdValidator, ValidationResult
from config import (
    HOLD_REASON_UNCORROBORATED_TAIL, count_pending_review, is_pending_review,
)
from main_app import processing


@pytest.fixture(autouse=True)
def _isolate_db():
    """Pin the Database singleton to this module's dir per test so collection
    order cannot leave it bound to a sibling module's dir."""
    import database
    database.Database._instance = None
    database.Database.__init__.__defaults__ = (_test_data_dir,)
    database.Database.__new__.__defaults__ = (_test_data_dir,)
    yield


def test_refine_and_validate_passes_audio_analysis_to_validator(monkeypatch):
    captured = {}

    def fake_validate(self, ads, audio_analysis=None):
        captured['audio_analysis'] = audio_analysis
        return ValidationResult(ads=[])

    monkeypatch.setattr(processing, '_load_user_corrections',
                        lambda *a, **k: ([], []))
    monkeypatch.setattr(processing, '_refine_boundaries',
                        lambda ads, *a, **k: ads)
    monkeypatch.setattr(processing, '_apply_heuristic_rolls',
                        lambda *a, **k: None)
    monkeypatch.setattr(processing.storage, 'save_combined_ads',
                        lambda *a, **k: None)
    monkeypatch.setattr(ad_validator.AdValidator, 'validate', fake_validate)

    analysis = {'signals': [], 'loudness_baseline': -16.0,
                'analysis_time_seconds': 1.0, 'errors': []}
    processing._refine_and_validate(
        'slug', 'ep',
        [{'start': 1.0, 'end': 40.0, 'confidence': 0.9, 'reason': 'sponsor read'}],
        [], 'unused.mp3', '', 3600.0, 0.80, 'Pod',
        audio_analysis=analysis,
    )
    assert captured['audio_analysis'] is analysis


def test_build_recut_ad_list_passes_stored_audio_analysis(monkeypatch):
    captured = {}

    def fake_validate(self, ads, audio_analysis=None):
        captured['audio_analysis'] = audio_analysis
        return ValidationResult(ads=[])

    markers = [{'start': 5.0, 'end': 45.0, 'confidence': 0.9, 'reason': 'sponsor read'}]
    stored = {'signals': [], 'loudness_baseline': -15.0,
              'analysis_time_seconds': 1.0, 'errors': []}
    monkeypatch.setattr(processing.db, 'get_episode',
                        lambda slug, eid: {'ad_markers_json': json.dumps(markers)})
    monkeypatch.setattr(processing.db, 'get_episode_corrections', lambda eid: [])
    monkeypatch.setattr(processing.db, 'get_episode_audio_analysis',
                        lambda slug, eid: json.dumps(stored))
    monkeypatch.setattr(processing, '_load_user_corrections',
                        lambda *a, **k: ([], []))
    monkeypatch.setattr(processing, 'resolve_max_ad_duration_override',
                        lambda *a: None)
    monkeypatch.setattr(processing, 'resolve_cue_gated_approval',
                        lambda *a: False)
    monkeypatch.setattr(ad_validator.AdValidator, 'validate', fake_validate)

    processing._build_recut_ad_list('slug', 'ep', [], 3600.0, '', 0.80,
                                    podcast_id=1)
    assert captured['audio_analysis'] == stored


# Load-bearing: the last segment ends exactly at the marker start (10557.6)
# and is included by the boundary-inclusive overlap (seg_end >= start) in
# src/utils/text.py:137. A strict comparison would empty ad_text and bypass
# the vad_gap clamp branch, changing both tests' routing.
TAIL_SEGMENTS = [
    {'start': 10520.0, 'end': 10545.0,
     'text': 'So that is our show for this week everybody.'},
    {'start': 10545.0, 'end': 10557.6,
     'text': 'Thanks for being here and we will see you next time on the show.'},
]


def _tail_marker():
    return {
        'start': 10557.6,
        'end': 10600.0,
        'confidence': 0.75,
        'reason': 'VAD gap at episode tail (42.4s untranscribed)',
        'detection_stage': 'vad_gap',
        'sponsor': None,
    }


def _tail_transition_analysis():
    return {
        'signals': [{
            'start': 10557.4, 'end': 10599.6,
            'signal_type': 'dai_transition_pair',
            'confidence': 0.95, 'duration': 42.2,
            'details': {'avg_delta_db': 15.0, 'start_direction': 'down',
                        'start_delta_db': 15.2, 'end_delta_db': 14.8,
                        'start_from_lufs': -16.0, 'start_to_lufs': -31.2,
                        'end_from_lufs': -31.0, 'end_to_lufs': -16.2},
        }],
        'loudness_baseline': -16.0,
        'analysis_time_seconds': 4.2,
        'errors': [],
    }


def test_uncorroborated_tail_marker_lands_in_pending_review():
    # TWiT 1091 shipped silently with pendingReviewCount=0. The full path
    # (validator -> confidence gate -> pending-review bucket) must now keep
    # the marker in audio AND surface it to the review queue.
    validator = AdValidator(episode_duration=10600.0, segments=TAIL_SEGMENTS)
    result = validator.validate([_tail_marker()])
    ads_to_remove, _ = processing._gate_validation_by_confidence(
        'slug', 'ep', result.ads, 0.80
    )
    ad = result.ads[0]
    assert ads_to_remove == []
    assert ad['was_cut'] is False
    assert ad['held_for_review'] is True
    assert ad['hold_reason'] == HOLD_REASON_UNCORROBORATED_TAIL
    assert is_pending_review(ad) is True
    assert count_pending_review(result.ads) == 1


def test_corroborated_tail_marker_is_cut_not_pending():
    validator = AdValidator(episode_duration=10600.0, segments=TAIL_SEGMENTS)
    result = validator.validate([_tail_marker()],
                                audio_analysis=_tail_transition_analysis())
    ads_to_remove, _ = processing._gate_validation_by_confidence(
        'slug', 'ep', result.ads, 0.80
    )
    ad = result.ads[0]
    assert ads_to_remove == [ad]
    assert ad['was_cut'] is True
    assert ad.get('held_for_review') is not True
    assert ad['corroborated_by'] == 'transition_pair'
    assert is_pending_review(ad) is False
    assert count_pending_review(result.ads) == 0
