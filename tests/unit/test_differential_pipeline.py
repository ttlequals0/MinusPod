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
    with patch('main_app.processing.resolve_differential_fetch_enabled',
               return_value=False), \
         patch('main_app.processing.fetch_and_diff', mock_fetch), \
         patch.object(processing.db, 'save_episode_dai_differential') as mock_save:
        result = processing._run_differential_fetch(
            'feed', 'ep1', 'https://example.com/e.mp3', '/tmp/a.mp3', 7)
    assert result is None
    mock_fetch.assert_not_called()
    mock_save.assert_not_called()


def test_gate_on_fetches_and_persists():
    mock_fetch = MagicMock(return_value=_RESULT)
    with patch('main_app.processing.resolve_differential_fetch_enabled',
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
    with patch('main_app.processing.resolve_differential_fetch_enabled',
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
    with patch('main_app.processing.resolve_differential_fetch_enabled',
               return_value=True), \
         patch('main_app.processing.fetch_and_diff', mock_fetch), \
         patch.object(processing.status_service, 'update_job_stage'), \
         patch.object(processing.db, 'save_episode_dai_differential',
                      side_effect=RuntimeError('db gone')):
        result = processing._run_differential_fetch(
            'feed', 'ep1', 'https://example.com/e.mp3', '/tmp/a.mp3', 7)
    assert result == _RESULT


def test_flag_read_failure_is_nonfatal():
    with patch('main_app.processing.resolve_differential_fetch_enabled',
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
