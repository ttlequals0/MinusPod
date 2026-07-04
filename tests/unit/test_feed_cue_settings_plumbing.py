"""Unit tests: per-feed cue settings are threaded into _detect_ads_first_pass (Task A2).

Verifies that when resolve_feed_cue_settings returns per-feed values, those
values reach synthesize_ads_from_cue_pairs and snap_ad_boundaries_to_cues.
Mirrors the pattern from test_cue_boundary_snap.test_settings_plumbing_snap_receives_db_values.
"""
import os
import sys
import tempfile

# Must set before importing main_app.processing to avoid /app/data mkdir error.
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='cue_plumbing_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from unittest.mock import MagicMock, patch
import main_app.processing as processing
from audio_analysis.base import AudioAnalysisResult, AudioSegmentSignal


def _cue(start, end, conf):
    return AudioSegmentSignal(
        start=start,
        end=end,
        signal_type='audio_cue',
        confidence=conf,
        details={'source': 'template'},
    )


def _result_with(*signals):
    r = AudioAnalysisResult()
    r.signals = list(signals)
    return r


def test_per_feed_snap_values_reach_snap_function():
    """Per-feed snap_lead/snap_lag from resolver reach snap_ad_boundaries_to_cues."""
    feed_snap_lead = 6.0
    feed_snap_lag = 2.5
    feed_snap_confidence = 0.70

    mock_snap = MagicMock(return_value=None)
    mock_resolve = MagicMock(return_value={
        'create_from_pairs': False,
        'pair_min_break': 30.0,
        'pair_max_break': 480.0,
        'pair_max_break_fraction': 0.5,
        'snap_confidence': feed_snap_confidence,
        'snap_lead': feed_snap_lead,
        'snap_lag': feed_snap_lag,
    })

    ctx = MagicMock()
    ctx.slug = 'test-feed'
    ctx.episode_id = 'ep-1'
    ctx.podcast_id = None  # skips telemetry branch

    audio_result = _result_with(_cue(start=93.0, end=94.0, conf=0.90))
    ad_result_stub = {'status': 'success', 'ads': [{'start': 100.0, 'end': 160.0}]}

    with patch.object(processing.db, 'get_setting', return_value=None), \
         patch.object(processing.db, 'upsert_episode', return_value=1), \
         patch.object(processing.status_service, 'update_job_stage'), \
         patch.object(processing.ad_detector, 'process_transcript',
                      return_value=ad_result_stub), \
         patch('main_app.processing.resolve_feed_cue_settings', mock_resolve), \
         patch('main_app.processing.snap_ad_boundaries_to_cues', mock_snap):
        processing._detect_ads_first_pass(
            ctx, [], '/fake/audio.mp3',
            skip_patterns=[], audio_analysis_result=audio_result,
            progress_callback=None,
        )

    mock_snap.assert_called_once()
    _, kwargs = mock_snap.call_args
    assert kwargs['snap_lead_s'] == feed_snap_lead, (
        f"expected snap_lead_s={feed_snap_lead}, got {kwargs.get('snap_lead_s')}"
    )
    assert kwargs['snap_lag_s'] == feed_snap_lag, (
        f"expected snap_lag_s={feed_snap_lag}, got {kwargs.get('snap_lag_s')}"
    )
    assert kwargs['min_confidence'] == feed_snap_confidence, (
        f"expected min_confidence={feed_snap_confidence}, got {kwargs.get('min_confidence')}"
    )


def test_per_feed_create_from_pairs_off_skips_synthesis():
    """create_from_pairs=False skips cue-pair synthesis regardless of global setting."""
    mock_synth = MagicMock(return_value=([{'start': 0.0, 'end': 30.0}], {}))
    mock_resolve = MagicMock(return_value={
        'create_from_pairs': False,
        'pair_min_break': 30.0,
        'pair_max_break': 480.0,
        'pair_max_break_fraction': 0.5,
        'snap_confidence': 0.80,
        'snap_lead': 10.0,
        'snap_lag': 4.0,
    })

    ctx = MagicMock()
    ctx.slug = 'test-feed'
    ctx.episode_id = 'ep-1'
    ctx.podcast_id = None

    audio_result = _result_with(_cue(start=10.0, end=11.0, conf=0.90),
                                _cue(start=200.0, end=201.0, conf=0.90))
    ad_result_stub = {'status': 'success', 'ads': []}

    with patch.object(processing.db, 'get_setting', return_value=None), \
         patch.object(processing.db, 'upsert_episode', return_value=1), \
         patch.object(processing.status_service, 'update_job_stage'), \
         patch.object(processing.ad_detector, 'process_transcript',
                      return_value=ad_result_stub), \
         patch('main_app.processing.resolve_feed_cue_settings', mock_resolve), \
         patch('main_app.processing.snap_ad_boundaries_to_cues', MagicMock()), \
         patch('main_app.processing.synthesize_ads_from_cue_pairs', mock_synth):
        processing._detect_ads_first_pass(
            ctx, [], '/fake/audio.mp3',
            skip_patterns=[], audio_analysis_result=audio_result,
            progress_callback=None,
        )

    mock_synth.assert_not_called()


def test_per_feed_create_from_pairs_on_calls_synthesis_with_feed_values():
    """create_from_pairs=True calls synthesis with per-feed pair knob values."""
    feed_min_break = 20.0
    feed_max_break = 300.0
    feed_max_fraction = 0.4

    mock_synth = MagicMock(return_value=([], {}))
    mock_resolve = MagicMock(return_value={
        'create_from_pairs': True,
        'pair_min_break': feed_min_break,
        'pair_max_break': feed_max_break,
        'pair_max_break_fraction': feed_max_fraction,
        'snap_confidence': 0.80,
        'snap_lead': 10.0,
        'snap_lag': 4.0,
    })

    ctx = MagicMock()
    ctx.slug = 'test-feed'
    ctx.episode_id = 'ep-1'
    ctx.podcast_id = None

    audio_result = _result_with(_cue(start=10.0, end=11.0, conf=0.90))
    ad_result_stub = {'status': 'success', 'ads': [{'start': 100.0, 'end': 160.0}]}

    with patch.object(processing.db, 'get_setting', return_value=None), \
         patch.object(processing.db, 'upsert_episode', return_value=1), \
         patch.object(processing.status_service, 'update_job_stage'), \
         patch.object(processing.ad_detector, 'process_transcript',
                      return_value=ad_result_stub), \
         patch('main_app.processing.resolve_feed_cue_settings', mock_resolve), \
         patch('main_app.processing.snap_ad_boundaries_to_cues', MagicMock()), \
         patch('main_app.processing.synthesize_ads_from_cue_pairs', mock_synth):
        processing._detect_ads_first_pass(
            ctx, [], '/fake/audio.mp3',
            skip_patterns=[], audio_analysis_result=audio_result,
            progress_callback=None,
        )

    mock_synth.assert_called_once()
    _, kwargs = mock_synth.call_args
    assert kwargs['min_break_s'] == feed_min_break
    assert kwargs['max_break_s'] == feed_max_break
    assert kwargs['max_break_fraction'] == feed_max_fraction
