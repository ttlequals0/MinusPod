"""Mid-episode holes the batched decoder skipped are re-transcribed without VAD.

Production shape: a sponsor read opening at 728.77s-752.55s never reached the transcript.
"""
import os
import sys
import tempfile

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='hole_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from unittest.mock import MagicMock, patch

import main_app.processing as processing


def _seg(start, end, text):
    return {'start': start, 'end': end, 'text': text,
            'words': [{'word': text, 'start': start, 'end': end}]}


def _run(tmp_path, segments, transcribed, volume=-20.0, hole_min=None, tried=()):
    mock_t = MagicMock()
    if isinstance(transcribed, Exception):
        mock_t.transcribe.side_effect = transcribed
    elif callable(transcribed):
        mock_t.transcribe.side_effect = transcribed
    else:
        mock_t.transcribe.return_value = transcribed
    mock_t.filter_hallucinations.side_effect = lambda segs: [
        s for s in segs if s.get('text', '').strip()]
    mock_db = MagicMock()
    mock_db.get_setting.return_value = hole_min
    chunks = []

    def _extract(path, start, end):
        chunk = tmp_path / f'hole_{len(chunks)}.wav'
        chunk.write_bytes(b'wav')
        chunks.append((start, end, chunk))
        return str(chunk)

    with patch.object(processing, 'transcriber', mock_t), \
         patch.object(processing, 'db', mock_db), \
         patch.object(processing, 'extract_audio_chunk',
                      side_effect=_extract), \
         patch.object(processing, 'mean_volume_db', return_value=volume):
        merged, added, empty = processing._retranscribe_holes_no_vad(
            'show', 'ep1', '/audio.mp3', segments, None, tried)
    _run.empty = empty
    return merged, added, mock_t, chunks


def _production_shape():
    return [_seg(700.0, 728.77, 'and that wraps up the listener mail. Great!'),
            _seg(752.55, 760.0, 'And the nicest thing? It works with everything')]


def test_production_hole_is_transcribed_and_merged_in_order(tmp_path):
    recovered = [_seg(0.4, 12.0, 'I kept losing my notes between meetings'),
                 _seg(12.0, 23.5, 'That is the problem Acme fixed for me.')]
    merged, added, mock_t, chunks = _run(tmp_path, _production_shape(), recovered)
    assert added is True
    assert [c[:2] for c in chunks] == [(728.77, 752.55)]
    assert [s['text'][:6] for s in merged] == [
        'and th', 'I kept', 'That i', 'And th']
    hole = merged[1]
    assert hole['novad_hole'] is True
    assert hole['start'] == 728.77 + 0.4
    assert hole['words'][0]['start'] == 728.77 + 0.4
    assert merged[2]['end'] == 728.77 + 23.5
    assert 'novad_hole' not in merged[0]
    args, kwargs = mock_t.transcribe.call_args
    assert kwargs == {'language_override': None, 'vad_filter': False,
                      'sequential': True}
    assert not chunks[0][2].exists()


def test_five_second_gap_is_not_a_hole_at_the_default(tmp_path):
    segments = [_seg(0.0, 10.0, 'a'), _seg(15.0, 20.0, 'b')]
    merged, added, mock_t, chunks = _run(tmp_path, segments, [])
    assert added is False
    assert merged == segments
    assert chunks == []


def test_quiet_hole_is_skipped(tmp_path):
    merged, added, mock_t, chunks = _run(
        tmp_path, _production_shape(), [_seg(0.0, 5.0, 'x')], volume=-60.0)
    assert added is False
    mock_t.transcribe.assert_not_called()
    assert not chunks[0][2].exists()
    assert _run.empty == [{'start': 728.77, 'end': 752.55, 'reason': 'quiet'}]


def test_unreadable_volume_is_skipped(tmp_path):
    merged, added, mock_t, _ = _run(
        tmp_path, _production_shape(), [_seg(0.0, 5.0, 'x')], volume=None)
    assert added is False
    mock_t.transcribe.assert_not_called()
    assert _run.empty == []  # a failed probe may succeed next time


def test_hole_count_cap_keeps_the_largest(tmp_path):
    # 25 holes of 9-33 s; total under 600 s so only the count binds.
    segments, t = [], 0.0
    for i in range(26):
        segments.append(_seg(t, t + 5.0, f's{i}'))
        t += 5.0 + 9.0 + i
    merged, added, mock_t, chunks = _run(tmp_path, segments, [])
    lengths = sorted(round(e - s, 2) for s, e, _ in chunks)
    assert len(chunks) == 20
    assert lengths[0] == 14.0  # the five smallest (9-13 s) were dropped


def test_hole_seconds_cap(tmp_path):
    # Holes of 400, 300 and 150 s: 400 + 150 fit in 600, 300 does not.
    segments = [_seg(0.0, 5.0, 'a'), _seg(405.0, 410.0, 'b'),
                _seg(710.0, 715.0, 'c'), _seg(865.0, 870.0, 'd')]
    merged, added, mock_t, chunks = _run(tmp_path, segments, [])
    assert sorted(round(e - s) for s, e, _ in chunks) == [150, 400]


def test_transcriber_failure_leaves_segments_unchanged(tmp_path):
    segments = _production_shape()
    merged, added, _, chunks = _run(tmp_path, segments, RuntimeError('boom'))
    assert added is False
    assert merged == segments
    assert not chunks[0][2].exists()
    assert _run.empty == []


def test_transcriber_none_leaves_segments_unchanged(tmp_path):
    segments = _production_shape()
    merged, added, _, _ = _run(tmp_path, segments, None)
    assert added is False
    assert merged == segments


def test_hallucination_only_output_is_discarded(tmp_path):
    segments = _production_shape()
    merged, added, _, _ = _run(tmp_path, segments, [_seg(0.0, 3.0, '   ')])
    assert added is False
    assert merged == segments
    assert _run.empty == [{'start': 728.77, 'end': 752.55, 'reason': 'no_speech'}]


def test_recorded_empty_hole_is_not_retried(tmp_path):
    tried = [{'start': 728.77, 'end': 752.55, 'reason': 'no_speech'}]
    merged, added, mock_t, chunks = _run(
        tmp_path, _production_shape(), [_seg(0.0, 3.0, 'x')], tried=tried)
    assert added is False
    assert chunks == []
    mock_t.transcribe.assert_not_called()


def test_hole_threshold_follows_the_vad_gap_setting(tmp_path):
    segments = [_seg(0.0, 10.0, 'a'), _seg(15.0, 20.0, 'b')]
    _, _, _, chunks = _run(tmp_path, segments, [], hole_min='4.0')
    assert [c[:2] for c in chunks] == [(10.0, 15.0)]


def test_tail_pass_uses_the_shared_helper(tmp_path):
    mock_t = MagicMock()
    mock_t.get_audio_duration.return_value = 142.4
    with patch.object(processing, 'transcriber', mock_t), \
         patch.object(processing, 'resolve_tail_retranscribe_tunables',
                      return_value={'min_seconds': 10.0, 'max_seconds': 600.0}), \
         patch.object(processing, '_retranscribe_span_no_vad',
                      return_value=([_seg(100.5, 120.0, 'post-roll')], None)) as span:
        merged, added = processing._retranscribe_tail_no_vad(
            'show', 'ep1', '/audio.mp3', [_seg(0.0, 100.0, 'x')], None)
    assert added is True
    span.assert_called_once_with(
        'show', 'ep1', '/audio.mp3', 100.0, 142.4, None, 'novad_tail')


def _reuse(tmp_path, repair):
    original = tmp_path / 'orig.mp3'
    original.write_bytes(b'mp3')
    mock_storage = MagicMock()
    mock_storage.get_transcript.return_value = 'existing transcript'
    mock_storage.get_original_path.return_value = str(original)
    mock_db = MagicMock()
    mock_db.get_original_segments.return_value = _production_shape()
    mock_db.get_repair_holes.return_value = [
        {'start': 728.77, 'end': 752.55, 'reason': 'no_speech'}]
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
                      side_effect=lambda *a: (a[3], False)), \
         patch.object(processing, 'extract_audio_chunk', return_value=None), \
         patch.object(processing, '_retranscribe_holes_no_vad',
                      side_effect=repair or processing._retranscribe_holes_no_vad):
        _, segments = processing._download_and_transcribe(
            'show', 'ep1', 'http://example.com/e.mp3')
    return segments, mock_storage, mock_db, mock_t


def test_reuse_with_a_recorded_empty_hole_loads_no_whisper(tmp_path):
    segments, mock_storage, mock_db, mock_t = _reuse(tmp_path, None)
    assert segments == _production_shape()
    mock_t.transcribe.assert_not_called()
    mock_storage.save_transcript.assert_not_called()
    mock_db.save_repaired_original_transcript.assert_not_called()


def test_reuse_repair_stores_the_repaired_originals(tmp_path, caplog):
    base = _production_shape()
    repaired = base[:1] + [dict(_seg(729.0, 750.0, 'Acme'), novad_hole=True)] + base[1:]
    empty = [{'start': 900.0, 'end': 910.0, 'reason': 'quiet'}]
    with caplog.at_level('INFO'):
        segments, mock_storage, mock_db, _ = _reuse(
            tmp_path, lambda *a: (repaired, True, empty))
    assert segments == repaired
    mock_storage.save_transcript.assert_called_once_with('show', 'ep1', 'joined')
    mock_db.save_repaired_original_transcript.assert_called_once_with(
        'show', 'ep1', 'joined', repaired)
    mock_db.add_repair_holes.assert_called_once_with('show', 'ep1', empty)
    mock_storage.save_original_segments.assert_not_called()
    assert 'Stored repaired original transcript (+1 segments)' in caplog.text


def test_first_run_writes_originals_once_and_records_empty_holes():
    base = [_seg(0.0, 100.0, 'show content')]
    empty = [{'start': 40.0, 'end': 55.0, 'reason': 'no_speech'}]
    mock_storage = MagicMock()
    mock_storage.get_transcript.return_value = None
    mock_storage.get_original_path.return_value = '/nonexistent/ep1-original.mp3'
    mock_t = MagicMock()
    mock_t.check_audio_availability.return_value = (True, None)
    mock_t.download_audio.return_value = '/tmp/dl.mp3'
    mock_t.transcribe_chunked.return_value = list(base)
    mock_t.segments_to_text.return_value = 'joined'
    mock_sponsor = MagicMock()
    mock_sponsor.apply_transcript_corrections.side_effect = lambda t: t
    mock_db = MagicMock()
    with patch.object(processing, 'storage', mock_storage), \
         patch.object(processing, 'db', mock_db), \
         patch.object(processing, 'transcriber', mock_t), \
         patch.object(processing, 'sponsor_service', mock_sponsor), \
         patch.object(processing, 'status_service', MagicMock()), \
         patch.object(processing, 'get_feed_language_override', return_value=None), \
         patch.object(processing, '_retranscribe_holes_no_vad',
                      return_value=(base, False, empty)) as holes, \
         patch.object(processing, '_retranscribe_tail_no_vad',
                      side_effect=lambda *a: (a[3], False)):
        processing._download_and_transcribe('show', 'ep1', 'http://example.com/e.mp3')
    assert holes.call_args.args[5] == ()
    mock_storage.save_original_transcript.assert_called_once_with('show', 'ep1', 'joined')
    mock_storage.save_original_segments.assert_called_once_with('show', 'ep1', base)
    mock_db.save_repaired_original_transcript.assert_not_called()
    mock_db.add_repair_holes.assert_called_once_with('show', 'ep1', empty, replace=True)


def test_mean_volume_db_parses_volumedetect():
    from utils import audio as audio_utils
    out = MagicMock(stderr='[Parsed_volumedetect_0 @ 0x1] mean_volume: -61.3 dB\n')
    with patch.object(audio_utils, 'tracked_run', return_value=out):
        assert audio_utils.mean_volume_db('/x.wav') == -61.3
    with patch.object(audio_utils, 'tracked_run',
                      return_value=MagicMock(stderr='garbage')):
        assert audio_utils.mean_volume_db('/x.wav') is None
    with patch.object(audio_utils, 'tracked_run', side_effect=OSError('no ffmpeg')):
        assert audio_utils.mean_volume_db('/x.wav') is None
