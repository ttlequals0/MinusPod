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


def _run(tmp_path, segments, transcribed, volume=-20.0, hole_min=None):
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
        merged, added = processing._retranscribe_holes_no_vad(
            'show', 'ep1', '/audio.mp3', segments, None)
    return merged, added, mock_t, chunks


def _production_shape():
    return [_seg(700.0, 728.77, 'thank you to our patrons. Yay!'),
            _seg(752.55, 760.0, 'And the best part? It integrates seamlessly')]


def test_production_hole_is_transcribed_and_merged_in_order(tmp_path):
    recovered = [_seg(0.4, 12.0, 'I used to be the person who hunched over'),
                 _seg(12.0, 23.5, 'That is the problem Acme fixed for me.')]
    merged, added, mock_t, chunks = _run(tmp_path, _production_shape(), recovered)
    assert added is True
    assert [c[:2] for c in chunks] == [(728.77, 752.55)]
    assert [s['text'][:6] for s in merged] == [
        'thank ', 'I used', 'That i', 'And th']
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


def test_unreadable_volume_is_skipped(tmp_path):
    merged, added, mock_t, _ = _run(
        tmp_path, _production_shape(), [_seg(0.0, 5.0, 'x')], volume=None)
    assert added is False
    mock_t.transcribe.assert_not_called()


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
                      return_value=[_seg(100.5, 120.0, 'post-roll')]) as span:
        merged, added = processing._retranscribe_tail_no_vad(
            'show', 'ep1', '/audio.mp3', [_seg(0.0, 100.0, 'x')], None)
    assert added is True
    span.assert_called_once_with(
        'show', 'ep1', '/audio.mp3', 100.0, 142.4, None, 'novad_tail')


def test_reuse_branch_refreshes_transcript_when_only_holes_were_added(tmp_path):
    original = tmp_path / 'orig.mp3'
    original.write_bytes(b'mp3')
    base = _production_shape()
    repaired = base[:1] + [dict(_seg(729.0, 750.0, 'Acme'), novad_hole=True)] + base[1:]
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
         patch.object(processing, '_retranscribe_holes_no_vad',
                      return_value=(repaired, True)) as holes, \
         patch.object(processing, '_retranscribe_tail_no_vad',
                      side_effect=lambda *a: (a[3], False)) as tail:
        _, segments = processing._download_and_transcribe(
            'show', 'ep1', 'http://example.com/e.mp3')
    holes.assert_called_once_with('show', 'ep1', '/tmp/work.mp3', base, None)
    assert tail.call_args.args[3] == repaired
    assert segments == repaired
    mock_storage.save_transcript.assert_called_once_with('show', 'ep1', 'joined')
    mock_storage.save_original_segments.assert_not_called()


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
