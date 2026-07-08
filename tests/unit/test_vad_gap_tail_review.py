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
from ad_validator import ValidationResult
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
