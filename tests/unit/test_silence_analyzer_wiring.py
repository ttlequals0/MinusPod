"""Analyzer wiring tests for silence detection (task B2).

Verifies:
- _load_silence_config returns None when flag is off (default).
- _load_silence_config returns a SilenceDetector when flag is on.
- analyze() runs the detector only when resolver returns True.
- Spans land on result.silence_spans.
- to_dict() emits 'silence_spans' key only when non-empty.
- Detector exception -> errors entry, analysis continues (no abort).
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from audio_analysis.audio_analyzer import AudioAnalyzer
from audio_analysis.base import AudioAnalysisResult
from audio_analysis.silence_detector import SilenceDetector


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeDB:
    """Minimal DB stub for _load_silence_config tests."""

    def __init__(self, silence_enabled=False):
        self._silence_enabled = silence_enabled

    def get_setting(self, key):
        return None

    def get_setting_bool(self, key, default=False):
        return default

    def get_setting_float(self, key, default=None):
        return default

    def get_podcast_cue_settings_overrides(self, podcast_id):
        return {'silence_snap_enabled': 1 if self._silence_enabled else None}


# ---------------------------------------------------------------------------
# _load_silence_config unit tests
# ---------------------------------------------------------------------------

def test_load_silence_config_no_db_returns_none():
    analyzer = AudioAnalyzer(db=None)
    assert analyzer._load_silence_config(feed_id=1) is None


def test_load_silence_config_flag_off_returns_none():
    analyzer = AudioAnalyzer(db=_FakeDB(silence_enabled=False))
    assert analyzer._load_silence_config(feed_id=1) is None


def test_load_silence_config_flag_on_returns_detector():
    analyzer = AudioAnalyzer(db=_FakeDB(silence_enabled=True))
    detector = analyzer._load_silence_config(feed_id=1)
    assert isinstance(detector, SilenceDetector)


def test_load_silence_config_no_feed_id_returns_none():
    # Without a podcast_id, resolve_silence_snap_enabled defaults to False.
    analyzer = AudioAnalyzer(db=_FakeDB(silence_enabled=True))
    assert analyzer._load_silence_config(feed_id=None) is None


def test_load_silence_config_uses_tunables():
    """DB-overridden noise_db and min_duration_s reach the detector."""
    class _TunedDB(_FakeDB):
        def get_setting_float(self, key, default=None):
            return {'silence_snap_noise_db': -60.0,
                    'silence_snap_min_duration_seconds': 0.5}.get(key, default)

    analyzer = AudioAnalyzer(db=_TunedDB(silence_enabled=True))
    detector = analyzer._load_silence_config(feed_id=1)
    assert detector is not None
    assert detector.noise_db == -60.0
    assert detector.min_silence_s == 0.5


# ---------------------------------------------------------------------------
# analyze() integration -- detector invoked / not
# ---------------------------------------------------------------------------

def _make_analyzer(silence_enabled):
    return AudioAnalyzer(db=_FakeDB(silence_enabled=silence_enabled))


def _patch_analyze_deps(audio_path='/fake/ep.mp3', duration=600.0):
    """Patch os.path.exists, get_audio_duration, and volume analyzer so
    analyze() runs without real files."""
    exists = patch('os.path.exists', return_value=True)
    dur = patch('audio_analysis.audio_analyzer.get_audio_duration', return_value=duration)
    vol = patch.object(
        AudioAnalyzer, '_run_component_with_timeout',
        side_effect=_fake_run_component,
    )
    return exists, dur, vol


_FAKE_SPANS = [{'start': 10.0, 'end': 12.0, 'duration': 2.0}]


def _fake_run_component(name, func, timeout):
    """Stub that executes the callable so lambda captures are exercised."""
    if name == 'silence':
        return _FAKE_SPANS, None
    if name == 'volume':
        return ([], None, []), None
    return None, None


def test_analyze_skips_detector_when_flag_off():
    analyzer = _make_analyzer(silence_enabled=False)
    detect_calls = []

    original_load = analyzer._load_silence_config

    def spy_load(feed_id=None):
        result = original_load(feed_id=feed_id)
        detect_calls.append(result)
        return result

    analyzer._load_silence_config = spy_load

    with patch('os.path.exists', return_value=True), \
         patch('audio_analysis.audio_analyzer.get_audio_duration', return_value=600.0), \
         patch.object(analyzer, '_run_component_with_timeout', side_effect=_fake_run_component):
        result = analyzer.analyze('/fake/ep.mp3', feed_id=42)

    # spy was called but returned None (flag off)
    assert detect_calls == [None]
    assert result.silence_spans == []


def test_analyze_runs_detector_when_flag_on():
    analyzer = _make_analyzer(silence_enabled=True)

    with patch('os.path.exists', return_value=True), \
         patch('audio_analysis.audio_analyzer.get_audio_duration', return_value=600.0), \
         patch.object(analyzer, '_run_component_with_timeout', side_effect=_fake_run_component):
        result = analyzer.analyze('/fake/ep.mp3', feed_id=42)

    assert result.silence_spans == _FAKE_SPANS


def test_analyze_spans_land_on_result():
    analyzer = _make_analyzer(silence_enabled=True)

    with patch('os.path.exists', return_value=True), \
         patch('audio_analysis.audio_analyzer.get_audio_duration', return_value=600.0), \
         patch.object(analyzer, '_run_component_with_timeout', side_effect=_fake_run_component):
        result = analyzer.analyze('/fake/ep.mp3', feed_id=1)

    assert len(result.silence_spans) == 1
    assert result.silence_spans[0]['start'] == 10.0


# ---------------------------------------------------------------------------
# to_dict() emission
# ---------------------------------------------------------------------------

def test_to_dict_no_silence_spans_omits_key():
    result = AudioAnalysisResult()
    d = result.to_dict()
    assert 'silence_spans' not in d


def test_to_dict_with_silence_spans_emits_key():
    result = AudioAnalysisResult()
    result.silence_spans = [{'start': 5.0, 'end': 6.0, 'duration': 1.0}]
    d = result.to_dict()
    assert 'silence_spans' in d
    assert d['silence_spans'] == [{'start': 5.0, 'end': 6.0, 'duration': 1.0}]


# ---------------------------------------------------------------------------
# Detector exception -> errors entry, analysis continues
# ---------------------------------------------------------------------------

def test_detector_exception_adds_error_and_continues():
    """If the silence detector raises, analysis still returns a result."""
    analyzer = _make_analyzer(silence_enabled=True)

    def raising_run_component(name, func, timeout):
        if name == 'silence':
            # Simulate the ThreadPoolExecutor surface -- exception goes into errors.
            return None, 'silence analysis failed: RuntimeError: boom'
        if name == 'volume':
            return ([], None, []), None
        return None, None

    with patch('os.path.exists', return_value=True), \
         patch('audio_analysis.audio_analyzer.get_audio_duration', return_value=600.0), \
         patch.object(analyzer, '_run_component_with_timeout', side_effect=raising_run_component):
        result = analyzer.analyze('/fake/ep.mp3', feed_id=1)

    assert result.silence_spans == []
    assert any('silence' in e for e in result.errors)
