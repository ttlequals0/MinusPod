"""Tail no-VAD re-transcription merge (spec 1.2).

Whisper's VAD drops quiet DAI post-rolls (TWiT 1091: transcript ended 42.4s
before the audio), so no LLM window covered the tail. The helper re-runs the
tail with vad_filter=False, offsets timestamps, applies the hallucination
filter, and appends segments flagged novad_tail=True before the transcript
is persisted and windows are built.
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


def _run_helper(tmp_path, segments, duration, tail_segments):
    chunk = tmp_path / 'tail.wav'
    chunk.write_bytes(b'wav')
    mock_t = MagicMock()
    mock_t.get_audio_duration.return_value = duration
    mock_t.transcribe.return_value = tail_segments
    mock_t.filter_hallucinations.side_effect = lambda segs: [
        s for s in segs if s.get('text', '').strip()]
    with patch.object(processing, 'transcriber', mock_t), \
         patch.object(processing, 'extract_audio_chunk',
                      return_value=str(chunk)) as extract, \
         patch.object(processing, 'resolve_tail_retranscribe_tunables',
                      return_value=_TUNABLES):
        merged, added = processing._retranscribe_tail_no_vad(
            'show', 'ep1', '/audio.mp3', segments, 'Show', None)
    return merged, added, mock_t, extract, chunk


def test_tail_gap_appends_offset_flagged_segments(tmp_path):
    segments = [_seg(0.0, 100.0, 'show content')]
    merged, added, mock_t, extract, chunk = _run_helper(
        tmp_path, segments, 142.4, [_seg(0.5, 20.0, 'quiet post-roll ad')])
    assert added is True
    assert len(merged) == 2
    tail = merged[-1]
    assert tail['novad_tail'] is True
    assert tail['start'] == 100.5
    assert tail['end'] == 120.0
    assert tail['words'][0]['start'] == 100.5
    extract.assert_called_once_with('/audio.mp3', 100.0, 142.4)
    args, kwargs = mock_t.transcribe.call_args
    assert args == (str(chunk),)
    assert kwargs == {'podcast_name': 'Show', 'language_override': None,
                      'vad_filter': False}
    assert not chunk.exists()  # temp chunk cleaned up


def test_gap_below_min_is_noop(tmp_path):
    segments = [_seg(0.0, 100.0, 'show content')]
    merged, added, mock_t, extract, _ = _run_helper(
        tmp_path, segments, 105.0, [])
    assert added is False
    assert merged == segments
    extract.assert_not_called()
    mock_t.transcribe.assert_not_called()


def test_gap_above_max_is_noop(tmp_path):
    merged, added, mock_t, extract, _ = _run_helper(
        tmp_path, [_seg(0.0, 100.0, 'x')], 701.0, [])
    assert added is False
    extract.assert_not_called()


def test_unknown_duration_is_noop(tmp_path):
    merged, added, mock_t, extract, _ = _run_helper(
        tmp_path, [_seg(0.0, 100.0, 'x')], None, [])
    assert added is False
    extract.assert_not_called()


def test_empty_tail_transcription_is_noop(tmp_path):
    merged, added, _, _, _ = _run_helper(
        tmp_path, [_seg(0.0, 100.0, 'x')], 142.4, [])
    assert added is False
    assert len(merged) == 1


def test_swallowed_transcribe_failure_is_logged_as_failure(tmp_path, caplog):
    # transcribe() returns None on error rather than raising, so a silent
    # tail and a hard failure must not log the same line.
    with caplog.at_level('WARNING'):
        merged, added, _, _, _ = _run_helper(
            tmp_path, [_seg(0.0, 100.0, 'x')], 142.4, None)
    assert added is False
    assert len(merged) == 1
    assert 'Tail re-transcription failed' in caplog.text


def test_hallucination_filter_can_drop_whole_tail(tmp_path):
    merged, added, _, _, _ = _run_helper(
        tmp_path, [_seg(0.0, 100.0, 'x')], 142.4, [_seg(0.5, 3.0, '   ')])
    assert added is False
    assert len(merged) == 1


def test_transcribe_failure_is_non_fatal_and_cleans_up_chunk(tmp_path):
    chunk = tmp_path / 'tail.wav'
    chunk.write_bytes(b'wav')
    segments = [_seg(0.0, 100.0, 'show content')]
    mock_t = MagicMock()
    mock_t.get_audio_duration.return_value = 142.4
    mock_t.transcribe.side_effect = RuntimeError('model OOM')
    with patch.object(processing, 'transcriber', mock_t), \
         patch.object(processing, 'extract_audio_chunk',
                      return_value=str(chunk)), \
         patch.object(processing, 'resolve_tail_retranscribe_tunables',
                      return_value=_TUNABLES):
        merged, added = processing._retranscribe_tail_no_vad(
            'show', 'ep1', '/audio.mp3', segments, 'Show', None)
    assert added is False
    assert merged == segments
    assert not chunk.exists()  # cleanup still runs on failure


def test_fresh_transcription_appends_tail_before_persist():
    base = [_seg(0.0, 100.0, 'show content')]
    extended = base + [dict(_seg(100.5, 120.0, 'post-roll'), novad_tail=True)]
    mock_storage = MagicMock()
    mock_storage.get_transcript.return_value = None
    mock_t = MagicMock()
    mock_t.check_audio_availability.return_value = (True, None)
    mock_t.download_audio.return_value = '/tmp/dl.mp3'
    mock_t.transcribe_chunked.return_value = list(base)
    mock_t.segments_to_text.return_value = 'joined'
    mock_sponsor = MagicMock()
    mock_sponsor.apply_transcript_corrections.side_effect = lambda t: t
    with patch.object(processing, 'storage', mock_storage), \
         patch.object(processing, 'transcriber', mock_t), \
         patch.object(processing, 'sponsor_service', mock_sponsor), \
         patch.object(processing, 'status_service', MagicMock()), \
         patch.object(processing, 'get_feed_language_override',
                      return_value=None), \
         patch.object(processing, '_retranscribe_tail_no_vad',
                      return_value=(extended, True)) as tail:
        audio_path, segments = processing._download_and_transcribe(
            'show', 'ep1', 'http://example.com/e.mp3', 'Show')
    assert segments == extended
    tail.assert_called_once_with('show', 'ep1', '/tmp/dl.mp3', base, 'Show', None)
    mock_t.segments_to_text.assert_called_once_with(extended)
    mock_storage.save_transcript.assert_called_once_with('show', 'ep1', 'joined')
    mock_storage.save_original_segments.assert_called_once_with(
        'show', 'ep1', extended)


def test_reuse_branch_extends_in_memory_and_refreshes_transcript(tmp_path):
    original = tmp_path / 'orig.mp3'
    original.write_bytes(b'mp3')
    base = [_seg(0.0, 100.0, 'show content')]
    extended = base + [dict(_seg(100.5, 120.0, 'post-roll'), novad_tail=True)]
    mock_storage = MagicMock()
    mock_storage.get_transcript.return_value = 'existing transcript'
    mock_storage.get_original_path.return_value = str(original)
    mock_db = MagicMock()
    mock_db.get_original_segments.return_value = list(base)
    mock_t = MagicMock()
    mock_t.segments_to_text.return_value = 'joined'
    with patch.object(processing, 'storage', mock_storage), \
         patch.object(processing, 'db', mock_db), \
         patch.object(processing, 'transcriber', mock_t), \
         patch.object(processing, '_copy_retained_original_to_temp',
                      return_value='/tmp/work.mp3'), \
         patch.object(processing, 'get_feed_language_override',
                      return_value=None), \
         patch.object(processing, '_retranscribe_tail_no_vad',
                      return_value=(extended, True)) as tail:
        audio_path, segments = processing._download_and_transcribe(
            'show', 'ep1', 'http://example.com/e.mp3', 'Show')
    assert audio_path == '/tmp/work.mp3'
    assert segments == extended
    tail.assert_called_once_with(
        'show', 'ep1', '/tmp/work.mp3', base, 'Show', None)
    # Live transcript refreshed; write-once original stores are NOT re-written.
    mock_storage.save_transcript.assert_called_once_with('show', 'ep1', 'joined')
    mock_storage.save_original_segments.assert_not_called()
