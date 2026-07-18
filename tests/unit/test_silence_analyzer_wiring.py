"""Analyzer wiring tests for silence detection (task B2).

Verifies:
- _load_silence_config returns None when flag is off (default).
- _load_silence_config returns a SilenceDetector when flag is on.
- analyze() runs the detector only when resolver returns True.
- Spans land on result.silence_spans.
- to_dict() emits 'silence_spans' key only when non-empty.
- Detector exception -> errors entry, analysis continues (no abort).
- calculate_component_timeouts includes a decode-sized 'silence' timeout.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from audio_analysis.audio_analyzer import AudioAnalyzer, calculate_component_timeouts
from audio_analysis.base import AudioAnalysisResult
from audio_analysis.silence_detector import SilenceDetector
from utils.ffmpeg_run import ffmpeg_timeout


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


_FAKE_SPANS = [{'start': 10.0, 'end': 12.0, 'duration': 2.0}]


def _analyze(analyzer, feed_id, silence_detect=None):
    """Run analyze() with the raw component callables stubbed.

    Components are submitted to the shared pool directly (no
    _run_component_with_timeout wrapper anymore), so the seams are the
    detector methods themselves. SilenceDetector is instantiated inside
    _load_silence_config, hence the class-level patch.
    """
    silence_kwargs = ({'side_effect': silence_detect} if callable(silence_detect)
                      else {'return_value': _FAKE_SPANS})
    with patch('os.path.exists', return_value=True), \
         patch('audio_analysis.audio_analyzer.get_audio_duration', return_value=600.0), \
         patch.object(analyzer.volume_analyzer, 'analyze', return_value=([], None, [])), \
         patch.object(analyzer.splice_detector, 'detect', return_value=None), \
         patch.object(SilenceDetector, 'detect', **silence_kwargs):
        return analyzer.analyze('/fake/ep.mp3', feed_id=feed_id)


def test_analyze_skips_detector_when_flag_off():
    analyzer = _make_analyzer(silence_enabled=False)
    detect_calls = []

    original_load = analyzer._load_silence_config

    def spy_load(feed_id=None):
        result = original_load(feed_id=feed_id)
        detect_calls.append(result)
        return result

    analyzer._load_silence_config = spy_load

    result = _analyze(analyzer, feed_id=42)

    # spy was called but returned None (flag off)
    assert detect_calls == [None]
    assert result.silence_spans == []


def test_analyze_runs_detector_when_flag_on():
    analyzer = _make_analyzer(silence_enabled=True)
    result = _analyze(analyzer, feed_id=42)
    assert result.silence_spans == _FAKE_SPANS


def test_analyze_spans_land_on_result():
    analyzer = _make_analyzer(silence_enabled=True)
    result = _analyze(analyzer, feed_id=1)
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

    def boom(*_a, **_k):
        raise RuntimeError('boom')

    result = _analyze(analyzer, feed_id=1, silence_detect=boom)

    assert result.silence_spans == []
    assert any('silence' in e for e in result.errors)


# ---------------------------------------------------------------------------
# calculate_component_timeouts: silence timeout is decode-sized (finding 2)
# ---------------------------------------------------------------------------

def test_silence_timeout_is_decode_sized_not_volume_sized():
    """'silence' timeout must match ffmpeg_timeout(), not the volume formula.

    For a 90-minute episode the volume timeout is ~3 minutes (2s/min * 90 =
    180s, floored to 180s). The silence detector runs a full decode, so its
    timeout must be at least 5 minutes (the ffmpeg_timeout floor) and scale
    with the duration -- not the same small value as volume.
    """
    duration = 90 * 60.0  # 90 minutes in seconds
    timeouts = calculate_component_timeouts(duration)

    assert 'silence' in timeouts, "calculate_component_timeouts must include a 'silence' key"

    expected = ffmpeg_timeout(duration)  # decode-sized: min(max(300, 90*60+120), 1200) = 1200
    assert timeouts['silence'] == expected, (
        f"silence timeout {timeouts['silence']}s != expected decode-sized {expected}s"
    )
    # Sanity: silence timeout must be larger than the lightweight volume timeout.
    assert timeouts['silence'] > timeouts['volume'], (
        f"silence timeout ({timeouts['silence']}s) should exceed volume timeout "
        f"({timeouts['volume']}s) for a long episode"
    )
