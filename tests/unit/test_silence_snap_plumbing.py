"""Unit tests: silence snap settings reach _detect_ads_first_pass (task B3).

Mirrors test_feed_cue_settings_plumbing.py for the cue snap path.
"""
import os
import sys
import tempfile

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='silence_plumbing_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from unittest.mock import MagicMock, patch
import main_app.processing as processing
from audio_analysis.base import AudioAnalysisResult


def _result_with_spans(spans):
    r = AudioAnalysisResult()
    r.silence_spans = spans
    return r


def _base_cue_settings(**overrides):
    base = {
        'create_from_pairs': False,
        'pair_min_break': 30.0,
        'pair_max_break': 480.0,
        'pair_max_break_fraction': 0.5,
        'snap_confidence': 0.80,
        'snap_lead': 10.0,
        'snap_lag': 4.0,
        'silence_snap_enabled': False,
        'transition_snap_enabled': False,
    }
    base.update(overrides)
    return base


def test_silence_snap_max_distance_and_min_duration_reach_function():
    """max_distance_seconds and min_duration_seconds from resolve_silence_snap_tunables reach snap_ad_boundaries_to_silence."""
    db_max_distance = 3.5
    db_min_duration = 0.5
    mock_silence_snap = MagicMock()
    mock_resolve = MagicMock(return_value=_base_cue_settings())
    mock_tunables = MagicMock(return_value={
        'noise_db': -50.0,
        'min_duration_seconds': db_min_duration,
        'max_distance_seconds': db_max_distance,
    })

    ctx = MagicMock()
    ctx.slug = 'test-feed'
    ctx.episode_id = 'ep-1'
    ctx.podcast_id = None

    spans = [{'start': 99.0, 'end': 99.5, 'duration': 0.5}]
    audio_result = _result_with_spans(spans)
    ad_result_stub = {'status': 'success', 'ads': [{'start': 100.0, 'end': 160.0}]}

    with patch.object(processing.db, 'get_setting', return_value=None), \
         patch.object(processing.db, 'upsert_episode', return_value=1), \
         patch.object(processing.status_service, 'update_job_stage'), \
         patch.object(processing.ad_detector, 'process_transcript',
                      return_value=ad_result_stub), \
         patch('main_app.processing.resolve_feed_cue_settings', mock_resolve), \
         patch('main_app.processing.resolve_silence_snap_tunables', mock_tunables), \
         patch('main_app.processing.snap_ad_boundaries_to_cues', MagicMock()), \
         patch('main_app.processing.snap_ad_boundaries_to_silence', mock_silence_snap):
        processing._detect_ads_first_pass(
            ctx, [], '/fake/audio.mp3',
            skip_patterns=[], audio_analysis_result=audio_result,
            progress_callback=None,
        )

    mock_silence_snap.assert_called_once()
    _, kwargs = mock_silence_snap.call_args
    assert kwargs['max_distance_s'] == db_max_distance, (
        f"expected max_distance_s={db_max_distance}, got {kwargs.get('max_distance_s')}"
    )
    assert kwargs['min_silence_s'] == db_min_duration, (
        f"expected min_silence_s={db_min_duration}, got {kwargs.get('min_silence_s')}"
    )


def test_silence_snap_not_called_when_no_spans():
    """snap_ad_boundaries_to_silence is not called when audio_result.silence_spans is empty."""
    mock_silence_snap = MagicMock()
    mock_resolve = MagicMock(return_value=_base_cue_settings())

    ctx = MagicMock()
    ctx.slug = 'test-feed'
    ctx.episode_id = 'ep-1'
    ctx.podcast_id = None

    audio_result = _result_with_spans([])  # no spans
    ad_result_stub = {'status': 'success', 'ads': [{'start': 100.0, 'end': 160.0}]}

    with patch.object(processing.db, 'get_setting', return_value=None), \
         patch.object(processing.db, 'upsert_episode', return_value=1), \
         patch.object(processing.status_service, 'update_job_stage'), \
         patch.object(processing.ad_detector, 'process_transcript',
                      return_value=ad_result_stub), \
         patch('main_app.processing.resolve_feed_cue_settings', mock_resolve), \
         patch('main_app.processing.snap_ad_boundaries_to_cues', MagicMock()), \
         patch('main_app.processing.snap_ad_boundaries_to_silence', mock_silence_snap):
        processing._detect_ads_first_pass(
            ctx, [], '/fake/audio.mp3',
            skip_patterns=[], audio_analysis_result=audio_result,
            progress_callback=None,
        )

    mock_silence_snap.assert_not_called()


def test_silence_snap_not_called_when_no_ads():
    """snap_ad_boundaries_to_silence is not called when first_pass_ads is empty."""
    mock_silence_snap = MagicMock()
    mock_resolve = MagicMock(return_value=_base_cue_settings())

    ctx = MagicMock()
    ctx.slug = 'test-feed'
    ctx.episode_id = 'ep-1'
    ctx.podcast_id = None

    spans = [{'start': 99.0, 'end': 99.5, 'duration': 0.5}]
    audio_result = _result_with_spans(spans)
    ad_result_stub = {'status': 'success', 'ads': []}  # no ads

    with patch.object(processing.db, 'get_setting', return_value=None), \
         patch.object(processing.db, 'upsert_episode', return_value=1), \
         patch.object(processing.status_service, 'update_job_stage'), \
         patch.object(processing.ad_detector, 'process_transcript',
                      return_value=ad_result_stub), \
         patch('main_app.processing.resolve_feed_cue_settings', mock_resolve), \
         patch('main_app.processing.snap_ad_boundaries_to_cues', MagicMock()), \
         patch('main_app.processing.snap_ad_boundaries_to_silence', mock_silence_snap):
        processing._detect_ads_first_pass(
            ctx, [], '/fake/audio.mp3',
            skip_patterns=[], audio_analysis_result=audio_result,
            progress_callback=None,
        )

    mock_silence_snap.assert_not_called()
