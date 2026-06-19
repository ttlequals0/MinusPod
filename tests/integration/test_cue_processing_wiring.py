"""Confirms _detect_ads_first_pass actually wires in cue-pair synthesis and
boundary snap after the LLM first pass (#350).

The unit tests cover snap/cue-pair in isolation; this proves the pipeline calls
them, with the right settings, on the first-pass ad list.
"""
import os
import sys
import tempfile
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='cue-wire-test-'))

from audio_analysis.base import AudioAnalysisResult, AudioSegmentSignal  # noqa: E402


def _cue(start, end, conf=0.95, label='ding'):
    return AudioSegmentSignal(
        start=start, end=end, signal_type='audio_cue', confidence=conf,
        details={'source': 'template', 'label': label},
    )


class _StubDB:
    def __init__(self, create_from_pairs):
        self._bools = {'audio_cue_create_from_pairs': create_from_pairs}
        self.recorded = []

    def get_setting_bool(self, key, default=False):
        return self._bools.get(key, default)

    def get_setting(self, key):
        return '60.0' if key == 'review_max_boundary_shift' else None

    def get_setting_float(self, key, default=0.0):
        return default

    def upsert_episode(self, *a, **k):
        return 1

    def record_cue_detections(self, podcast_id, episode_id, records):
        self.recorded.append((podcast_id, episode_id, records))
        return len(records)


def test_first_pass_applies_cue_pair_and_snap(monkeypatch):
    from main_app import processing

    # First-pass LLM returns one ad; the second break (around 300-360s) is missed.
    ad_result = {'status': 'success', 'ads': [{'start': 100.0, 'end': 160.0}]}
    monkeypatch.setattr(processing.ad_detector, 'process_transcript', lambda *a, **k: ad_result)
    monkeypatch.setattr(processing.storage, 'save_ads_json', lambda *a, **k: None)
    monkeypatch.setattr(processing.status_service, 'update_job_stage', lambda *a, **k: None)
    monkeypatch.setattr(processing, 'clear_fallback', lambda *a, **k: None)
    monkeypatch.setattr(processing, 'db', _StubDB(create_from_pairs=True))

    analysis = AudioAnalysisResult()
    analysis.signals = [
        _cue(98.0, 99.5),     # near the LLM ad start -> snap start
        _cue(161.0, 161.6),   # near the LLM ad end -> snap end
        _cue(300.0, 300.5),   # bracket a missed break -> cue pair
        _cue(360.0, 360.5),
    ]

    ctx = types.SimpleNamespace(slug='wire-feed', episode_id='abcdef012345', podcast_id=1)
    ads, count, _ = processing._detect_ads_first_pass(
        ctx, segments=[], audio_path='x.mp3', skip_patterns=False,
        audio_analysis_result=analysis, progress_callback=None,
    )

    # Cue-pair synthesized the missed break (proves synthesize_ads_from_cue_pairs ran).
    pair = [a for a in ads if a.get('detection_stage') == 'cue_pair']
    assert len(pair) == 1
    assert 300 < pair[0]['start'] < 305 and 355 < pair[0]['end'] < 360

    # Boundary snap moved the LLM ad's edges to the cues (proves snap ran).
    llm = next(a for a in ads if 99 < a['start'] < 101 or 'cue_snap' in a)
    assert 'cue_snap' in llm
    assert abs(llm['start'] - 99.55) < 0.01   # cue end (99.5) + 0.05 lead

    # Telemetry wiring: every template cue recorded with its outcome (proves
    # the pipeline calls build_cue_detection_records + record_cue_detections).
    assert len(processing.db.recorded) == 1
    pid, eid, records = processing.db.recorded[0]
    assert pid == 1 and eid == 'abcdef012345'
    assert sorted(r['outcome'] for r in records) == ['pair', 'pair', 'snap', 'snap']


def test_first_pass_no_cue_pair_when_setting_off(monkeypatch):
    from main_app import processing

    ad_result = {'status': 'success', 'ads': [{'start': 100.0, 'end': 160.0}]}
    monkeypatch.setattr(processing.ad_detector, 'process_transcript', lambda *a, **k: ad_result)
    monkeypatch.setattr(processing.storage, 'save_ads_json', lambda *a, **k: None)
    monkeypatch.setattr(processing.status_service, 'update_job_stage', lambda *a, **k: None)
    monkeypatch.setattr(processing, 'clear_fallback', lambda *a, **k: None)
    monkeypatch.setattr(processing, 'db', _StubDB(create_from_pairs=False))

    analysis = AudioAnalysisResult()
    analysis.signals = [_cue(300.0, 300.5), _cue(360.0, 360.5)]

    ctx = types.SimpleNamespace(slug='wire-feed', episode_id='abcdef012345', podcast_id=1)
    ads, _, _ = processing._detect_ads_first_pass(
        ctx, segments=[], audio_path='x.mp3', skip_patterns=False,
        audio_analysis_result=analysis, progress_callback=None,
    )
    # Setting off -> no synthesized ad.
    assert not any(a.get('detection_stage') == 'cue_pair' for a in ads)
