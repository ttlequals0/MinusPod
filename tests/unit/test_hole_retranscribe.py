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
import transcriber as transcriber_mod
from transcriber import Transcriber


def _seg(start, end, text):
    return {'start': start, 'end': end, 'text': text,
            'words': [{'word': text, 'start': start, 'end': end}]}


def _production_shape():
    return [_seg(700.0, 728.77, 'and that wraps up the listener mail. Great!'),
            _seg(752.55, 760.0, 'And the nicest thing? It works with everything')]


def _repair(tmp_path, segments, decoded, volume=-20.0, min_s=8.0, skip=()):
    """Run Transcriber.repair_gaps with extraction, volume and decode faked.

    Returns (added, empty, decode mock, extracted chunks, volume probe mock).
    """
    chunks = []

    def _extract(path, start, end):
        chunk = tmp_path / f'hole_{len(chunks)}.wav'
        chunk.write_bytes(b'wav')
        chunks.append((start, end, chunk))
        return str(chunk)

    decode = MagicMock()
    if isinstance(decoded, Exception):
        decode.side_effect = decoded
    else:
        decode.side_effect = lambda *a: [dict(s, words=[dict(w) for w in s['words']])
                                         for s in decoded] if decoded is not None else None
    with patch.object(transcriber_mod, 'extract_audio_chunk', side_effect=_extract), \
         patch.object(transcriber_mod, 'mean_volume_db', return_value=volume) as probe, \
         patch.object(Transcriber, '_transcribe_sequential', decode):
        added, empty = Transcriber().repair_gaps(
            '/audio.mp3', segments, min_s, None, skip=skip)
    return added, empty, decode, chunks, probe


def test_production_hole_is_decoded_with_offsets(tmp_path):
    recovered = [_seg(0.4, 12.0, 'I kept losing my notes between meetings'),
                 _seg(12.0, 23.5, 'That is the problem Acme fixed for me.')]
    added, empty, decode, chunks, probe = _repair(tmp_path, _production_shape(), recovered)
    assert [c[:2] for c in chunks] == [(728.77, 752.55)]
    assert [s['text'][:6] for s in added] == ['I kept', 'That i']
    assert added[0]['novad_hole'] is True
    assert added[0]['start'] == 728.77 + 0.4
    assert added[0]['words'][0]['start'] == 728.77 + 0.4
    assert added[1]['end'] == 728.77 + 23.5
    assert empty == []
    decode.assert_called_once_with(str(chunks[0][2]), None)
    assert not chunks[0][2].exists()
    # The volume gate reads the source range before any extraction.
    assert probe.call_args.args == ('/audio.mp3', 728.77, 752.55 - 728.77)


def test_five_second_gap_is_not_a_hole(tmp_path):
    added, empty, _, chunks, _ = _repair(
        tmp_path, [_seg(0.0, 10.0, 'a'), _seg(15.0, 20.0, 'b')], [])
    assert (added, empty, chunks) == ([], [], [])


def test_quiet_hole_is_recorded_without_extraction(tmp_path):
    added, empty, decode, chunks, _ = _repair(
        tmp_path, _production_shape(), [_seg(0.0, 5.0, 'x')], volume=-60.0)
    assert added == []
    assert chunks == []
    decode.assert_not_called()
    assert empty == [{'start': 728.77, 'end': 752.55, 'reason': 'quiet'}]


def test_unreadable_volume_is_skipped_unrecorded(tmp_path):
    added, empty, decode, chunks, _ = _repair(
        tmp_path, _production_shape(), [_seg(0.0, 5.0, 'x')], volume=None)
    assert (added, empty, chunks) == ([], [], [])
    decode.assert_not_called()


def test_hole_count_cap_keeps_the_largest(tmp_path):
    # 25 holes of 9-33 s; total under 600 s so only the count binds.
    segments, t = [], 0.0
    for i in range(26):
        segments.append(_seg(t, t + 5.0, f's{i}'))
        t += 5.0 + 9.0 + i
    _, _, _, chunks, _ = _repair(tmp_path, segments, [])
    lengths = sorted(round(e - s, 2) for s, e, _ in chunks)
    assert len(chunks) == 20
    assert lengths[0] == 14.0  # the five smallest (9-13 s) were dropped


def test_hole_seconds_cap(tmp_path):
    # Holes of 400, 300 and 150 s: 400 + 150 fit in 600, 300 does not.
    segments = [_seg(0.0, 5.0, 'a'), _seg(405.0, 410.0, 'b'),
                _seg(710.0, 715.0, 'c'), _seg(865.0, 870.0, 'd')]
    _, _, _, chunks, _ = _repair(tmp_path, segments, [])
    assert sorted(round(e - s) for s, e, _ in chunks) == [150, 400]


def test_decoder_failure_adds_and_records_nothing(tmp_path):
    for decoded in (RuntimeError('boom'), None):
        added, empty, _, chunks, _ = _repair(tmp_path, _production_shape(), decoded)
        assert (added, empty) == ([], [])
        assert not chunks[-1][2].exists()


def test_hallucination_only_output_is_recorded_as_no_speech(tmp_path):
    added, empty, _, _, _ = _repair(tmp_path, _production_shape(), [_seg(0.0, 3.0, '   ')])
    assert added == []
    assert empty == [{'start': 728.77, 'end': 752.55, 'reason': 'no_speech'}]


def test_recorded_empty_hole_is_not_retried(tmp_path):
    skip = [{'start': 728.77, 'end': 752.55, 'reason': 'no_speech'}]
    added, empty, decode, chunks, probe = _repair(
        tmp_path, _production_shape(), [_seg(0.0, 3.0, 'x')], skip=skip)
    assert (added, empty, chunks) == ([], [], [])
    decode.assert_not_called()
    probe.assert_not_called()


def _hole_min(setting):
    mock_t = MagicMock()
    mock_t.repair_gaps.return_value = ([], [])
    mock_db = MagicMock()
    mock_db.get_setting.return_value = setting
    with patch.object(processing, 'transcriber', mock_t), \
         patch.object(processing, 'db', mock_db):
        processing._retranscribe_holes_no_vad('/audio.mp3', _production_shape(), None)
    return mock_t.repair_gaps.call_args.args[2]


def test_hole_threshold_follows_the_vad_gap_setting():
    assert _hole_min('12.0') == 12.0


def test_hole_threshold_never_drops_below_the_reviewer_gap():
    assert _hole_min('1.0') == 8.0


def test_sequential_decode_uses_the_base_model_without_batching():
    base, batched = MagicMock(), MagicMock()
    word = MagicMock(word='hi', start=0.1, end=0.4)
    seg = MagicMock(start=0.0, end=0.5, text=' hi ', words=[word])
    base.transcribe.return_value = (iter([seg]), MagicMock())
    with patch.object(transcriber_mod, '_get_whisper_settings',
                      return_value={'backend': 'local', 'language': 'en'}), \
         patch.object(transcriber_mod.WhisperModelSingleton, 'get_instance',
                      return_value=(base, batched)), \
         patch.object(transcriber_mod, '_record_local_transcription_outcome') as record, \
         patch.object(Transcriber, 'preprocess_audio', return_value=None), \
         patch.object(Transcriber, 'get_audio_duration') as probe:
        t = Transcriber()
        t.last_transcription_stats = {'outcome': 'success', 'batch_size': 16}
        result = t._transcribe_sequential('/hole.wav', None)
    assert result == [{'start': 0.0, 'end': 0.5, 'text': 'hi',
                       'words': [{'word': 'hi', 'start': 0.1, 'end': 0.4}]}]
    batched.transcribe.assert_not_called()
    kwargs = base.transcribe.call_args.kwargs
    assert kwargs == {'language': 'en', 'beam_size': 5, 'word_timestamps': True,
                      'vad_filter': False}
    probe.assert_not_called()
    record.assert_not_called()
    assert t.last_transcription_stats == {'outcome': 'success', 'batch_size': 16}


def test_sequential_decode_failure_keeps_the_main_run_stats():
    base = MagicMock()
    base.transcribe.side_effect = RuntimeError('decoder broke')
    with patch.object(transcriber_mod, '_get_whisper_settings',
                      return_value={'backend': 'local', 'language': 'en'}), \
         patch.object(transcriber_mod.WhisperModelSingleton, 'get_instance',
                      return_value=(base, MagicMock())), \
         patch.object(transcriber_mod, '_record_local_transcription_outcome') as record, \
         patch.object(Transcriber, 'preprocess_audio', return_value=None):
        t = Transcriber()
        t.last_transcription_stats = {'outcome': 'success'}
        assert t._transcribe_sequential('/hole.wav', None) is None
    record.assert_not_called()
    assert t.last_transcription_stats == {'outcome': 'success'}


def test_sequential_decode_on_the_api_backend_disables_vad():
    with patch.object(transcriber_mod, '_get_whisper_settings',
                      return_value={'backend': transcriber_mod.WHISPER_BACKEND_API}), \
         patch.object(Transcriber, '_transcribe_via_api', return_value=[]) as api:
        Transcriber()._transcribe_sequential('/hole.wav', 'de')
    assert api.call_args.kwargs == {'language_override': 'de', 'vad_filter': False}


def _reuse(tmp_path, holes):
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
    mock_t.repair_gaps.side_effect = holes
    mock_sponsor = MagicMock()
    mock_sponsor.apply_transcript_corrections.side_effect = lambda t: t.replace('Akme', 'Acme')
    with patch.object(processing, 'storage', mock_storage), \
         patch.object(processing, 'db', mock_db), \
         patch.object(processing, 'transcriber', mock_t), \
         patch.object(processing, 'sponsor_service', mock_sponsor), \
         patch.object(processing, '_copy_retained_original_to_temp',
                      return_value='/tmp/work.mp3'), \
         patch.object(processing, 'get_feed_language_override',
                      return_value=None), \
         patch.object(processing, '_retranscribe_tail_no_vad', return_value=[]):
        _, segments = processing._download_and_transcribe(
            'show', 'ep1', 'http://example.com/e.mp3')
    return segments, mock_storage, mock_db, mock_t


def test_reuse_passes_the_recorded_holes_and_stores_nothing_without_a_repair(tmp_path):
    segments, mock_storage, mock_db, mock_t = _reuse(tmp_path, lambda *a, **k: ([], []))
    assert segments == _production_shape()
    assert mock_t.repair_gaps.call_args.kwargs['skip'] == [
        {'start': 728.77, 'end': 752.55, 'reason': 'no_speech'}]
    mock_storage.save_transcript.assert_not_called()
    mock_db.save_repaired_original_transcript.assert_not_called()


def test_reuse_repair_stores_the_repaired_originals(tmp_path, caplog):
    recovered = dict(_seg(729.0, 750.0, 'brought to you by Akme'), novad_hole=True)
    empty = [{'start': 900.0, 'end': 910.0, 'reason': 'quiet'}]
    with caplog.at_level('INFO'):
        segments, mock_storage, mock_db, _ = _reuse(
            tmp_path, lambda *a, **k: ([recovered], empty))
    assert [s['start'] for s in segments] == [700.0, 729.0, 752.55]
    # Sponsor-name corrections reach the recovered opening.
    assert recovered['text'] == 'brought to you by Acme'
    mock_storage.save_transcript.assert_called_once_with('show', 'ep1', 'joined')
    mock_db.save_repaired_original_transcript.assert_called_once_with(
        'show', 'ep1', 'joined', segments)
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
                      return_value=([], empty)) as holes, \
         patch.object(processing, '_retranscribe_tail_no_vad', return_value=[]):
        processing._download_and_transcribe('show', 'ep1', 'http://example.com/e.mp3')
    assert holes.call_args.args[3] == ()
    mock_storage.save_original_transcript.assert_called_once_with('show', 'ep1', 'joined')
    mock_storage.save_original_segments.assert_called_once_with('show', 'ep1', base)
    mock_db.save_repaired_original_transcript.assert_not_called()
    mock_db.add_repair_holes.assert_called_once_with('show', 'ep1', empty, replace=True)


def test_mean_volume_db_parses_volumedetect():
    from utils import audio as audio_utils
    out = MagicMock(stderr='[Parsed_volumedetect_0 @ 0x1] mean_volume: -61.3 dB\n')
    with patch.object(audio_utils, 'tracked_run', return_value=out) as run:
        assert audio_utils.mean_volume_db('/x.wav', 10.0, 5.0) == -61.3
    cmd = run.call_args.args[0]
    assert cmd[cmd.index('-ss') + 1] == '10.0' and cmd[cmd.index('-t') + 1] == '5.0'
    assert cmd.index('-ss') < cmd.index('-i') < cmd.index('-t')
    with patch.object(audio_utils, 'tracked_run',
                      return_value=MagicMock(stderr='garbage')):
        assert audio_utils.mean_volume_db('/x.wav') is None
    with patch.object(audio_utils, 'tracked_run', side_effect=OSError('no ffmpeg')):
        assert audio_utils.mean_volume_db('/x.wav') is None
