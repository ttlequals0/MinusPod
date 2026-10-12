"""Analyzer wiring tests for splice-evidence detection (docs/specs/2026-07-08-ad-splice-detection.md#21-new-module-srcaudio_analysissplice_detectorpy).

Verifies:
- splice detector runs when splice_evidence_enabled is on (the default).
- flag off / no db -> detector not invoked, result.splice_evidence is None.
- payload lands on result.splice_evidence and in to_dict().
- detector exception -> errors entry, analysis continues.
- calculate_component_timeouts includes a decode-sized 'splice' timeout.
"""
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from audio_analysis.audio_analyzer import AudioAnalyzer, calculate_component_timeouts
from audio_analysis.base import LoudnessFrame
from utils.ffmpeg_run import ffmpeg_timeout

_PAYLOAD = {'version': 1, 'events': [{'time': 30.0, 'end_time': 31.0,
            'type': 'digital_silence', 'depth_dbfs': -92.0, 'duration_s': 1.0,
            'loudness_step_lu': None, 'centroid_step_hz': None,
            'flatness_step': None}],
            'calibration': {'status': 'cold_start'}}


class _FakeDB:
    def __init__(self, splice_enabled=True):
        self._splice_enabled = splice_enabled

    def get_setting(self, key):
        return None

    def get_setting_bool(self, key, default=False):
        if key == 'splice_evidence_enabled':
            return self._splice_enabled
        return default

    def get_setting_float(self, key, default=None):
        return default

    def get_podcast_cue_settings_overrides(self, podcast_id):
        return {}


def _run_analyze(db):
    analyzer = AudioAnalyzer(db=db)
    frames = [LoudnessFrame(start=0.0, end=5.0, loudness_lufs=-20.0)]
    with patch.object(analyzer.volume_analyzer, 'analyze',
                      return_value=([], -20.0, frames)), \
         patch.object(analyzer.splice_detector, 'detect',
                      return_value=_PAYLOAD) as mock_detect, \
         patch('audio_analysis.audio_analyzer.get_audio_duration',
               return_value=600.0), \
         patch('os.path.exists', return_value=True):
        result = analyzer.analyze('/fake/episode.mp3')
    return result, mock_detect, frames


def test_enabled_by_default_runs_detector():
    result, mock_detect, frames = _run_analyze(_FakeDB(splice_enabled=True))
    mock_detect.assert_called_once_with('/fake/episode.mp3', 600.0, frames)
    assert result.splice_evidence == _PAYLOAD
    assert result.to_dict()['splice_evidence'] == _PAYLOAD


def test_flag_off_skips_detector():
    result, mock_detect, _ = _run_analyze(_FakeDB(splice_enabled=False))
    mock_detect.assert_not_called()
    assert result.splice_evidence is None
    assert 'splice_evidence' not in result.to_dict()


def test_no_db_skips_detector():
    result, mock_detect, _ = _run_analyze(None)
    mock_detect.assert_not_called()
    assert result.splice_evidence is None


def test_detector_exception_recorded_and_continues():
    analyzer = AudioAnalyzer(db=_FakeDB(splice_enabled=True))
    with patch.object(analyzer.volume_analyzer, 'analyze',
                      return_value=([], -20.0, [])), \
         patch.object(analyzer.splice_detector, 'detect',
                      side_effect=RuntimeError('boom')), \
         patch('audio_analysis.audio_analyzer.get_audio_duration',
               return_value=600.0), \
         patch('os.path.exists', return_value=True):
        result = analyzer.analyze('/fake/episode.mp3')
    assert result.splice_evidence is None
    assert any('splice' in e for e in result.errors)


def test_component_timeouts_include_splice():
    timeouts = calculate_component_timeouts(3600.0)
    assert timeouts['splice'] == ffmpeg_timeout(3600.0)
