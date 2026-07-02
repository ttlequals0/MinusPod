"""Locks the near-miss blindness contract (#350 Phase 6).

A sub-threshold near-miss must be advisory-only: it appears in the cue telemetry
as a 'below_threshold' row, but it is NOT an audio_cue signal, so prompt / snap /
pair / detected-cues never see it. This test drives _detect_ads_first_pass with
one real match and one near-miss and asserts both rows land while only the real
match is visible to get_signals_by_type('audio_cue').
"""
import os
import sys
import tempfile
import types


sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='cue-nm-test-'))

from audio_analysis.base import AudioAnalysisResult, AudioSegmentSignal  # noqa: E402


def _match(start, end, conf=0.95, label='ding'):
    return AudioSegmentSignal(
        start=start, end=end, signal_type='audio_cue', confidence=conf,
        details={'source': 'template', 'template_id': 1, 'label': label,
                 'cue_type': 'ad_break_boundary', 'role': 'boundary', 'score': 0.9},
    )


class _StubDB:
    def __init__(self):
        self.recorded = []

    def get_setting_bool(self, key, default=False):
        return default          # cue-pair off: keep the test focused on blindness

    def get_setting(self, key):
        return '60.0' if key == 'review_max_boundary_shift' else None

    def get_setting_float(self, key, default=0.0):
        return default

    def upsert_episode(self, *a, **k):
        return 1

    def record_cue_detections(self, podcast_id, episode_id, records):
        self.recorded.append((podcast_id, episode_id, records))
        return len(records)


def test_near_miss_is_advisory_only(monkeypatch):
    from main_app import processing

    ad_result = {'status': 'success', 'ads': [{'start': 100.0, 'end': 160.0}]}
    monkeypatch.setattr(processing.ad_detector, 'process_transcript', lambda *a, **k: ad_result)
    monkeypatch.setattr(processing.storage, 'save_ads_json', lambda *a, **k: None)
    monkeypatch.setattr(processing.status_service, 'update_job_stage', lambda *a, **k: None)
    monkeypatch.setattr(processing, 'clear_fallback', lambda *a, **k: None)
    monkeypatch.setattr(processing, 'db', _StubDB())

    analysis = AudioAnalysisResult()
    analysis.signals = [_match(98.0, 99.5)]   # one real match, near the LLM ad start
    analysis.cue_near_misses = [{
        'template_id': 1, 'label': 'ding', 'cue_type': 'ad_break_boundary',
        'role': 'boundary', 'start_s': 250.0, 'end_s': 250.5, 'score': 0.7,
    }]

    # The prompt / snap / pair layers only ever read get_signals_by_type; that
    # must return the single real match, never the near-miss.
    assert len(analysis.get_signals_by_type('audio_cue')) == 1

    ctx = types.SimpleNamespace(slug='nm-feed', episode_id='abcdef012345', podcast_id=1)
    ads, _, _ = processing._detect_ads_first_pass(
        ctx, segments=[], audio_path='x.mp3', skip_patterns=False,
        audio_analysis_result=analysis, progress_callback=None,
    )

    # No cue-pair ad could form from a single signal + an invisible near-miss.
    assert not any(a.get('detection_stage') == 'cue_pair' for a in ads)
    # The real match snapped the LLM ad start (proves it was a signal).
    assert any('cue_snap' in a for a in ads)

    # Telemetry: exactly two rows -- the real match (snap) and the near-miss
    # (below_threshold). The near-miss never became a signal.
    assert len(processing.db.recorded) == 1
    _, _, records = processing.db.recorded[0]
    outcomes = sorted(r['outcome'] for r in records)
    assert outcomes == ['below_threshold', 'snap']
    nm = next(r for r in records if r['outcome'] == 'below_threshold')
    assert nm['start_s'] == 250.0 and nm['match_score'] == 0.7
    # Near-miss is not part of the signal set the pipeline acted on.
    assert len(analysis.get_signals_by_type('audio_cue')) == 1
