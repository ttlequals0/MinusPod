"""Upstream transcript differential pipeline stage: gating, reuse and never-raise."""
import logging
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='transcript_diff_stage_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from unittest.mock import MagicMock, patch

import pytest

import main_app.processing as processing
from transcript_differential import UpstreamTranscript

URL = 'https://cdn.example.com/transcripts/ep1.vtt'
EPISODE = {'upstream_transcript_url': URL, 'upstream_transcript_type': 'text/vtt'}
FEED = {'id': 7, 'transcript_differential': 1}
SEGMENTS = [{'start': 0.0, 'end': 5.0, 'text': 'hello there'}]
SPAN = {'start': 100.0, 'end': 160.0, 'words': 150, 'offset_confirmed': True,
        'text_preview': 'brought to you by'}
TRANSCRIPT = UpstreamTranscript(cues=[{'start': 0.0, 'end': 5.0, 'text': 'hello there'}],
                                timed=True, source_url=URL, mime='text/vtt')
ALIGNED = {'status': 'ok', 'coverage': 0.93, 'timed': True, 'spans': [SPAN]}


def _iso(hours_ago):
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(
        timespec='seconds').replace('+00:00', 'Z')


@pytest.fixture
def stage():
    fetch = MagicMock(return_value=TRANSCRIPT)
    align = MagicMock(return_value=ALIGNED)
    with patch('main_app.processing.fetch_upstream_transcript', fetch), \
         patch('main_app.processing.align', align), \
         patch.object(processing.db, 'get_episode_upstream_transcript',
                      return_value=None) as get_stored, \
         patch.object(processing.db, 'save_episode_upstream_transcript') as save, \
         patch.object(processing.db, 'get_setting_bool', return_value=True), \
         patch('main_app.processing._publish_status') as publish:
        def run(episode=EPISODE, podcast=FEED, segments=SEGMENTS, segments_reused=True):
            run_stats = {}
            payload = processing._run_transcript_diff(
                'feed', 'ep1', episode, segments, run_stats, podcast=podcast,
                segments_reused=segments_reused)
            return payload, run_stats
        yield MagicMock(run=run, fetch=fetch, align=align, get_stored=get_stored, save=save,
                        publish=publish)


def test_ok_run_persists_spans_and_stats(stage, caplog):
    with caplog.at_level(logging.INFO, logger='podcast.audio'):
        payload, run_stats = stage.run(podcast={**FEED, 'download_user_agent_override': 'Feed/2.0'})
    stage.fetch.assert_called_once_with(URL, 'text/vtt', user_agent='Feed/2.0')
    assert payload['status'] == 'ok'
    assert payload['spans'] == [SPAN]
    assert payload['source_url'] == URL
    assert payload['mime'] == 'text/vtt'
    assert payload['timed'] is True
    assert payload['coverage'] == 0.93
    assert payload['fetched_at']
    stage.save.assert_called_once_with('feed', 'ep1', payload)
    assert run_stats['transcript_diff'] == {'status': 'ok', 'coverage': 0.93, 'spans': 1}
    assert ('Transcript diff: status=ok coverage=0.93 spans=1 source=cdn.example.com'
            in caplog.text)
    stage.publish.assert_called_once_with('update_job_stage', 'feed', 'ep1',
                                          'pass1:transcript_diff', 21)


def test_fetch_exception_becomes_error_status(stage):
    stage.fetch.side_effect = RuntimeError('boom')
    payload, run_stats = stage.run()
    assert payload['status'] == 'error'
    assert payload['spans'] == []
    assert 'boom' in payload['error']
    assert run_stats['transcript_diff']['status'] == 'error'
    stage.save.assert_called_once()


def test_fetch_failure_none_becomes_error_status(stage):
    stage.fetch.return_value = None
    payload, run_stats = stage.run()
    assert payload['status'] == 'error'
    assert run_stats['transcript_diff'] == {'status': 'error', 'coverage': None, 'spans': 0}
    stage.align.assert_not_called()


def test_align_exception_becomes_error_status(stage):
    stage.align.side_effect = ValueError('bad tokens')
    payload, run_stats = stage.run()
    assert payload['status'] == 'error'
    assert run_stats['transcript_diff']['status'] == 'error'


def test_store_failure_is_nonfatal(stage):
    stage.save.side_effect = RuntimeError('db locked')
    with patch.object(processing.db, 'clear_leaked_transaction'):
        payload, run_stats = stage.run()
    assert payload['status'] == 'ok'
    assert run_stats['transcript_diff']['spans'] == 1


def test_unreliable_alignment_has_no_spans(stage):
    stage.align.return_value = {'status': 'unreliable', 'coverage': 0.31,
                                'timed': True, 'spans': []}
    payload, run_stats = stage.run()
    assert payload['status'] == 'unreliable'
    assert run_stats['transcript_diff'] == {'status': 'unreliable', 'coverage': 0.31,
                                            'spans': 0}


def test_disabled_feed_skips(stage):
    payload, run_stats = stage.run(podcast={'id': 7, 'transcript_differential': 0})
    assert payload['status'] == 'none'
    stage.fetch.assert_not_called()
    assert run_stats['transcript_diff']['status'] == 'none'


def test_global_toggle_off_skips(stage):
    with patch.object(processing.db, 'get_setting_bool', return_value=False):
        payload, _ = stage.run(podcast={'id': 7, 'transcript_differential': None})
    assert payload['status'] == 'none'
    stage.fetch.assert_not_called()


def test_toggle_off_does_not_clobber_stored_ok_result(stage):
    stage.get_stored.return_value = {'status': 'ok', 'source_url': URL, 'spans': [SPAN],
                                     'fetched_at': _iso(1)}
    payload, run_stats = stage.run(podcast={'id': 7, 'transcript_differential': 0})
    assert payload['status'] == 'none'
    assert run_stats['transcript_diff']['status'] == 'none'
    stage.save.assert_not_called()


def test_no_segments_does_not_clobber_stored_ok_result(stage):
    stage.get_stored.return_value = {'status': 'ok', 'source_url': URL, 'spans': [SPAN],
                                     'fetched_at': _iso(1)}
    payload, _ = stage.run(segments=[])
    assert payload['status'] == 'none'
    stage.save.assert_not_called()


def test_local_feed_without_url_skips(stage):
    payload, run_stats = stage.run(
        episode={'upstream_transcript_url': None},
        podcast={'id': 7, 'feed_type': 'local', 'transcript_differential': None})
    assert payload['status'] == 'none'
    stage.fetch.assert_not_called()
    assert run_stats['transcript_diff']['status'] == 'none'
    stage.save.assert_not_called()
    stage.publish.assert_not_called()


def test_no_url_failed_read_leaves_stored_payload(stage):
    stage.get_stored.side_effect = RuntimeError('db locked')
    with patch.object(processing.db, 'clear_leaked_transaction'):
        payload, run_stats = stage.run(episode={'upstream_transcript_url': None})
    stage.save.assert_not_called()
    assert payload['status'] == 'none'
    assert run_stats['transcript_diff']['status'] == 'none'


@pytest.mark.parametrize('stored,saved', [
    ({'status': 'none', 'spans': []}, False),
    ({'status': 'ok', 'source_url': URL, 'spans': [SPAN], 'fetched_at': _iso(1)}, True),
])
def test_no_url_saves_none_only_over_a_real_result(stage, stored, saved):
    stage.get_stored.return_value = stored
    payload, _ = stage.run(episode={'upstream_transcript_url': None})
    assert payload['status'] == 'none'
    assert stage.save.called is saved


def test_fresh_segments_bypass_reuse(stage):
    stage.get_stored.return_value = {'status': 'ok', 'source_url': URL, 'spans': [SPAN],
                                     'fetched_at': _iso(1)}
    stage.run(segments_reused=False)
    stage.fetch.assert_called_once()


def test_no_segments_skips(stage):
    payload, _ = stage.run(segments=[])
    assert payload['status'] == 'none'
    stage.fetch.assert_not_called()


def test_reuses_recent_ok_result_for_same_url(stage):
    stored = {'status': 'ok', 'source_url': URL, 'mime': 'text/vtt', 'timed': True,
              'coverage': 0.9, 'spans': [SPAN], 'fetched_at': _iso(2), 'error': None}
    stage.get_stored.return_value = stored
    payload, run_stats = stage.run()
    stage.fetch.assert_not_called()
    stage.align.assert_not_called()
    assert payload['spans'] == [SPAN]
    assert payload['fetched_at'] == stored['fetched_at']
    assert run_stats['transcript_diff'] == {'status': 'ok', 'coverage': 0.9, 'spans': 1}


@pytest.mark.parametrize('stored', [
    {'status': 'ok', 'source_url': URL, 'spans': [SPAN], 'fetched_at': _iso(25)},
    {'status': 'ok', 'source_url': 'https://cdn.example.com/other.vtt', 'spans': [SPAN],
     'fetched_at': _iso(1)},
    {'status': 'error', 'source_url': URL, 'spans': [], 'fetched_at': _iso(1)},
    {'status': 'ok', 'source_url': URL, 'spans': [SPAN], 'fetched_at': 'garbage'},
])
def test_redetection_fetches_fresh_otherwise(stage, stored):
    stage.get_stored.return_value = stored
    payload, _ = stage.run()
    stage.fetch.assert_called_once()
    assert payload['fetched_at'] != stored['fetched_at']


def test_resolver_failure_is_nonfatal(stage):
    with patch('main_app.processing.resolve_transcript_differential',
               side_effect=RuntimeError('db gone')), \
         patch.object(processing.db, 'clear_leaked_transaction'):
        payload, run_stats = stage.run()
    assert payload['status'] == 'error'
    assert run_stats['transcript_diff']['status'] == 'error'


def test_build_validator_forwards_transcript_spans():
    with patch('ad_validator.AdValidator') as validator_cls:
        processing._build_validator(
            100.0, SEGMENTS, '', false_positive_corrections=[], min_cut_confidence=0.8,
            max_ad_duration_override=None, cue_gate_enabled=False, transcript_spans=[SPAN])
    assert validator_cls.call_args.kwargs['transcript_spans'] == [SPAN]


def _transcribe(saved_text, saved_segments, fresh_segments):
    outcome = {}
    with patch('main_app.processing.storage') as storage, \
         patch.object(processing.db, 'get_original_segments', return_value=saved_segments), \
         patch.object(processing.db, 'get_repair_holes', return_value=[]), \
         patch.object(processing.db, 'add_repair_holes'), \
         patch('main_app.processing.parse_transcript_segments', return_value=[]), \
         patch('main_app.processing.transcriber.transcribe_chunked',
               return_value=fresh_segments) as transcribe, \
         patch('main_app.processing._repair_transcript',
               side_effect=lambda *a, **k: (a[3], [], [])), \
         patch('main_app.processing._apply_transcript_corrections'), \
         patch('main_app.processing.get_feed_language_override', return_value=None), \
         patch('main_app.processing._download_episode_audio', return_value='/tmp/a.mp3'), \
         patch('main_app.processing.status_service'):
        storage.get_transcript.return_value = saved_text
        storage.get_original_path.return_value = None
        _, segments = processing._download_and_transcribe(
            'feed', 'ep1', 'https://example.com/ep1.mp3', outcome=outcome)
    return outcome, segments, transcribe


def test_saved_segments_report_reuse():
    outcome, _, transcribe = _transcribe('saved text', SEGMENTS, SEGMENTS)
    transcribe.assert_not_called()
    assert outcome['segments_reused'] is True


def test_empty_saved_transcript_falls_back_and_skips_reuse(stage):
    outcome, segments, transcribe = _transcribe('saved text', None, SEGMENTS)
    transcribe.assert_called_once()
    assert outcome['segments_reused'] is False
    stage.get_stored.return_value = {'status': 'ok', 'source_url': URL, 'spans': [SPAN],
                                     'fetched_at': _iso(1)}
    stage.run(segments=segments, segments_reused=outcome['segments_reused'])
    stage.fetch.assert_called_once()
