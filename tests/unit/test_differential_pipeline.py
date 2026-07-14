"""Unit tests: differential fetch pipeline stage (Layer 3).

Mirrors test_silence_snap_plumbing.py setup for main_app.processing.
"""
import json
import os
import sys
import tempfile

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='diff_pipeline_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from unittest.mock import MagicMock, patch

import main_app.processing as processing
from audio_analysis.base import AudioAnalysisResult

_RESULT = {'status': 'ok',
           'regions': [{'start_s': 10.0, 'end_s': 16.0,
                        'kind': 'differential', 'corr': 0.0}],
           'refetch_meta': {'ua': 'AntennaPod/3.4.0'}, 'error': None}


def test_gate_off_skips_fetch():
    mock_fetch = MagicMock()
    with patch('main_app.processing.resolve_differential_fetch_setting',
               return_value=False), \
         patch('main_app.processing.fetch_and_diff', mock_fetch), \
         patch.object(processing.db, 'save_episode_dai_differential') as mock_save:
        result = processing._run_differential_fetch(
            'feed', 'ep1', 'https://example.com/e.mp3', '/tmp/a.mp3', 7)
    assert result is None
    mock_fetch.assert_not_called()
    mock_save.assert_not_called()


class TestDifferentialFetchEffective:
    """The one shared rule for the gate and the feeds API (#519)."""

    def test_matrix(self):
        from config import differential_fetch_effective as eff
        assert eff(True) is True
        assert eff(True, dai_platform=None, dai_likely=False) is True
        assert eff(False, dai_platform='acast', dai_likely=True) is False
        assert eff(None) is False
        assert eff(None, dai_platform='acast') is True
        assert eff(None, dai_likely=True) is True


def test_flag_unset_non_dai_feed_skips_fetch():
    """Tri-state gate (#519): unset flag + no DAI signal = stage stays off."""
    mock_fetch = MagicMock()
    with patch('main_app.processing.resolve_differential_fetch_setting',
               return_value=None), \
         patch('main_app.processing.is_likely_dai_feed', return_value=False), \
         patch('main_app.processing.fetch_and_diff', mock_fetch), \
         patch.object(processing.db, 'save_episode_dai_differential') as mock_save:
        result = processing._run_differential_fetch(
            'feed', 'ep1', 'https://example.com/e.mp3', '/tmp/a.mp3', 7)
    assert result is None
    mock_fetch.assert_not_called()
    mock_save.assert_not_called()


def test_flag_unset_dai_url_auto_enables():
    """Unset flag + DAI-prefix enclosure URL runs the stage (#519)."""
    mock_fetch = MagicMock(return_value=_RESULT)
    with patch('main_app.processing.resolve_differential_fetch_setting',
               return_value=None), \
         patch('main_app.processing.is_likely_dai_feed', return_value=True), \
         patch('main_app.processing.fetch_and_diff', mock_fetch), \
         patch.object(processing.status_service, 'update_job_stage'), \
         patch.object(processing.db, 'save_episode_dai_differential'):
        result = processing._run_differential_fetch(
            'feed', 'ep1', 'https://example.com/e.mp3', '/tmp/a.mp3', 7)
    assert result == _RESULT


def test_flag_unset_dai_platform_auto_enables():
    """Unset flag + detected DAI platform on the feed runs the stage (#519)."""
    mock_fetch = MagicMock(return_value=_RESULT)
    with patch('main_app.processing.resolve_differential_fetch_setting',
               return_value=None), \
         patch('main_app.processing.is_likely_dai_feed', return_value=False), \
         patch('main_app.processing.fetch_and_diff', mock_fetch), \
         patch.object(processing.status_service, 'update_job_stage'), \
         patch.object(processing.db, 'save_episode_dai_differential'):
        result = processing._run_differential_fetch(
            'feed', 'ep1', 'https://example.com/e.mp3', '/tmp/a.mp3', 7,
            dai_platform='acast')
    assert result == _RESULT


def test_explicit_off_beats_dai_signal():
    """A per-feed 0 opts out even when the feed looks DAI-served (#519)."""
    mock_fetch = MagicMock()
    with patch('main_app.processing.resolve_differential_fetch_setting',
               return_value=False), \
         patch('main_app.processing.is_likely_dai_feed', return_value=True), \
         patch('main_app.processing.fetch_and_diff', mock_fetch), \
         patch.object(processing.db, 'save_episode_dai_differential') as mock_save:
        result = processing._run_differential_fetch(
            'feed', 'ep1', 'https://example.com/e.mp3', '/tmp/a.mp3', 7,
            dai_platform='acast')
    assert result is None
    mock_fetch.assert_not_called()
    mock_save.assert_not_called()


def test_gate_on_fetches_and_persists():
    mock_fetch = MagicMock(return_value=_RESULT)
    with patch('main_app.processing.resolve_differential_fetch_setting',
               return_value=True), \
         patch('main_app.processing.fetch_and_diff', mock_fetch), \
         patch.object(processing.status_service, 'update_job_stage'), \
         patch.object(processing.db, 'save_episode_dai_differential') as mock_save:
        result = processing._run_differential_fetch(
            'feed', 'ep1', 'https://example.com/e.mp3', '/tmp/a.mp3', 7)
    assert result == _RESULT
    url_arg, audio_arg = mock_fetch.call_args.args[0:2]
    assert url_arg == 'https://example.com/e.mp3'
    assert audio_arg == '/tmp/a.mp3'
    saved = json.loads(mock_save.call_args.args[2])
    assert saved == _RESULT


def test_unexpected_error_recorded_not_raised():
    with patch('main_app.processing.resolve_differential_fetch_setting',
               return_value=True), \
         patch('main_app.processing.fetch_and_diff',
               side_effect=RuntimeError('decoder exploded')), \
         patch.object(processing.status_service, 'update_job_stage'), \
         patch.object(processing.db, 'save_episode_dai_differential') as mock_save:
        result = processing._run_differential_fetch(
            'feed', 'ep1', 'https://example.com/e.mp3', '/tmp/a.mp3', 7)
    assert result['status'] == 'error'
    assert 'decoder exploded' in result['error']
    saved = json.loads(mock_save.call_args.args[2])
    assert saved['status'] == 'error'


def test_store_failure_is_nonfatal():
    mock_fetch = MagicMock(return_value=_RESULT)
    with patch('main_app.processing.resolve_differential_fetch_setting',
               return_value=True), \
         patch('main_app.processing.fetch_and_diff', mock_fetch), \
         patch.object(processing.status_service, 'update_job_stage'), \
         patch.object(processing.db, 'save_episode_dai_differential',
                      side_effect=RuntimeError('db gone')):
        result = processing._run_differential_fetch(
            'feed', 'ep1', 'https://example.com/e.mp3', '/tmp/a.mp3', 7)
    assert result == _RESULT


def test_flag_read_failure_is_nonfatal():
    with patch('main_app.processing.resolve_differential_fetch_setting',
               side_effect=RuntimeError('db gone')), \
         patch.object(processing.db, 'save_episode_dai_differential') as mock_save:
        result = processing._run_differential_fetch(
            'feed', 'ep1', 'https://example.com/e.mp3', '/tmp/a.mp3', 7)
    assert result is None
    mock_save.assert_not_called()


def test_result_rides_on_analysis_to_dict():
    r = AudioAnalysisResult()
    assert 'dai_differential' not in r.to_dict()
    r.dai_differential = _RESULT
    assert r.to_dict()['dai_differential'] == _RESULT


class TestMakeValidatorAudioAnalysis:
    """Finding 3: Layer 3 differential must reach the validator and detection
    even when Layer 2 (audio analysis) returns None."""

    def test_none_result_with_diff_returns_dict_carrying_diff(self):
        diff = {'status': 'ok', 'regions': [
            {'start_s': 10.0, 'end_s': 20.0, 'kind': 'differential', 'corr': 0.0}
        ]}
        result = processing._make_validator_audio_analysis(None, diff)
        assert result == {'dai_differential': diff}

    def test_none_result_none_diff_returns_none(self):
        assert processing._make_validator_audio_analysis(None, None) is None

    def test_non_none_result_returns_to_dict(self):
        r = AudioAnalysisResult()
        r.dai_differential = _RESULT
        out = processing._make_validator_audio_analysis(r, _RESULT)
        assert out['dai_differential'] == _RESULT

    def test_detect_first_pass_accepts_dai_differential_kwarg(self):
        # After the fix, _detect_ads_first_pass has a dai_differential= param
        # so the outer pipeline can pass Layer 3 data when Layer 2 is None.
        import inspect
        sig = inspect.signature(processing._detect_ads_first_pass)
        assert 'dai_differential' in sig.parameters
