"""Tail no-VAD re-transcription merge (spec 1.2).

Whisper's VAD drops quiet DAI post-rolls, so no LLM window covered the tail.
The tail gate re-runs it through Transcriber.transcribe_span_no_vad and the
segments, flagged novad_tail=True, are appended before the transcript is persisted.
"""
import os
import sys
import tempfile

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='tail_merge_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from unittest.mock import MagicMock, patch

import main_app.processing as processing


def _seg(start, end, text):
    return {'start': start, 'end': end, 'text': text,
            'words': [{'word': text, 'start': start, 'end': end}]}


_TUNABLES = {'min_seconds': 10.0, 'max_seconds': 600.0}


def _run_tail(segments, duration, span_result=(None, None)):
    mock_t = MagicMock()
    mock_t.get_audio_duration.return_value = duration
    mock_t.transcribe_span_no_vad.return_value = span_result
    with patch.object(processing, 'transcriber', mock_t), \
         patch.object(processing, 'resolve_tail_retranscribe_tunables',
                      return_value=_TUNABLES):
        tail = processing._retranscribe_tail_no_vad(
            'show', 'ep1', '/audio.mp3', segments, None)
    return tail, mock_t


def test_tail_gap_returns_the_span_segments():
    recovered = [dict(_seg(100.5, 120.0, 'quiet post-roll ad'), novad_tail=True)]
    tail, mock_t = _run_tail([_seg(0.0, 100.0, 'show content')], 142.4, (recovered, None))
    assert tail == recovered
    mock_t.transcribe_span_no_vad.assert_called_once_with(
        '/audio.mp3', 100.0, 142.4, None, 'novad_tail')


def test_gap_below_min_is_noop():
    tail, mock_t = _run_tail([_seg(0.0, 100.0, 'show content')], 105.0)
    assert tail == []
    mock_t.transcribe_span_no_vad.assert_not_called()


def test_gap_above_max_is_noop():
    tail, mock_t = _run_tail([_seg(0.0, 100.0, 'x')], 701.0)
    assert tail == []
    mock_t.transcribe_span_no_vad.assert_not_called()


def test_unknown_duration_is_noop():
    tail, mock_t = _run_tail([_seg(0.0, 100.0, 'x')], None)
    assert tail == []
    mock_t.transcribe_span_no_vad.assert_not_called()


def test_failed_or_empty_span_adds_nothing():
    for result in [(None, None), (None, 'no_speech')]:
        tail, _ = _run_tail([_seg(0.0, 100.0, 'x')], 142.4, result)
        assert tail == []


def test_fresh_transcription_appends_tail_before_persist():
    base = [_seg(0.0, 100.0, 'show content')]
    tail_seg = dict(_seg(100.5, 120.0, 'post-roll'), novad_tail=True)
    mock_storage = MagicMock()
    mock_storage.get_transcript.return_value = None
    # No retained original: the fresh-episode branch must download, not reuse.
    mock_storage.get_original_path.return_value = '/nonexistent/ep1-original.mp3'
    mock_t = MagicMock()
    mock_t.check_audio_availability.return_value = (True, None)
    mock_t.download_audio.return_value = '/tmp/dl.mp3'
    mock_t.transcribe_chunked.return_value = list(base)
    mock_t.segments_to_text.return_value = 'joined'
    mock_sponsor = MagicMock()
    mock_sponsor.apply_transcript_corrections.side_effect = lambda t: t
    with patch.object(processing, 'storage', mock_storage), \
         patch.object(processing, 'db', MagicMock()), \
         patch.object(processing, 'transcriber', mock_t), \
         patch.object(processing, 'sponsor_service', mock_sponsor), \
         patch.object(processing, 'status_service', MagicMock()), \
         patch.object(processing, 'get_feed_language_override',
                      return_value=None), \
         patch.object(processing, '_retranscribe_holes_no_vad', return_value=([], [])), \
         patch.object(processing, '_retranscribe_tail_no_vad',
                      return_value=[tail_seg]) as tail:
        audio_path, segments = processing._download_and_transcribe(
            'show', 'ep1', 'http://example.com/e.mp3')
    assert segments == base + [tail_seg]
    tail.assert_called_once_with('show', 'ep1', '/tmp/dl.mp3', base, None)
    mock_t.segments_to_text.assert_called_once_with(base + [tail_seg])
    mock_storage.save_transcript.assert_called_once_with('show', 'ep1', 'joined')
    mock_storage.save_original_segments.assert_called_once_with(
        'show', 'ep1', base + [tail_seg])


def test_reuse_branch_extends_in_memory_and_stores_the_repair(tmp_path):
    original = tmp_path / 'orig.mp3'
    original.write_bytes(b'mp3')
    base = [_seg(0.0, 100.0, 'show content')]
    tail_seg = dict(_seg(100.5, 120.0, 'post-roll'), novad_tail=True)
    mock_storage = MagicMock()
    mock_storage.get_transcript.return_value = 'existing transcript'
    mock_storage.get_original_path.return_value = str(original)
    mock_db = MagicMock()
    mock_db.get_original_segments.return_value = list(base)
    mock_db.get_repair_holes.return_value = []
    mock_t = MagicMock()
    mock_t.segments_to_text.return_value = 'joined'
    mock_sponsor = MagicMock()
    mock_sponsor.apply_transcript_corrections.side_effect = lambda t: t
    with patch.object(processing, 'storage', mock_storage), \
         patch.object(processing, 'db', mock_db), \
         patch.object(processing, 'transcriber', mock_t), \
         patch.object(processing, 'sponsor_service', mock_sponsor), \
         patch.object(processing, '_copy_retained_original_to_temp',
                      return_value='/tmp/work.mp3'), \
         patch.object(processing, 'get_feed_language_override',
                      return_value=None), \
         patch.object(processing, '_retranscribe_holes_no_vad', return_value=([], [])), \
         patch.object(processing, '_retranscribe_tail_no_vad',
                      return_value=[tail_seg]) as tail:
        audio_path, segments = processing._download_and_transcribe(
            'show', 'ep1', 'http://example.com/e.mp3')
    assert audio_path == '/tmp/work.mp3'
    assert segments == base + [tail_seg]
    tail.assert_called_once_with('show', 'ep1', '/tmp/work.mp3', base, None)
    mock_storage.save_transcript.assert_called_once_with('show', 'ep1', 'joined')
    mock_db.save_repaired_original_transcript.assert_called_once_with(
        'show', 'ep1', 'joined', base + [tail_seg])
    mock_storage.save_original_segments.assert_not_called()
