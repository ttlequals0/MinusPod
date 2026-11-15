"""A long segment holding almost no words is a hole the no-VAD re-decode may replace (case 2)."""
import json
import pathlib
from unittest.mock import MagicMock, patch

from tests.app_bootstrap import bootstrap

bootstrap('sparse_segment_holes_test_')

import transcriber as transcriber_mod
from transcriber import Transcriber
from utils import audio as audio_utils
from utils.text import sparse_segment_holes

FIXTURE = pathlib.Path(__file__).resolve().parents[1] / 'fixtures' / 'cut_edges'


def _seg(start, end, text):
    return {'start': start, 'end': end, 'text': text,
            'words': [{'word': text, 'start': start, 'end': end}]}


def _case2_segments():
    return json.loads((FIXTURE / 'case2_example_northguard_hole.json').read_text())['segments']


def _repair(tmp_path, segments, decoded, skip=()):
    chunks = []

    def _extract(path, start, end):
        chunk = tmp_path / f'hole_{len(chunks)}.wav'
        chunk.write_bytes(b'wav')
        chunks.append((start, end))
        return str(chunk)

    decode = MagicMock(side_effect=lambda *a: [dict(s, words=[dict(w) for w in s['words']])
                                               for s in decoded])
    with patch.object(transcriber_mod.chunking, 'extract_audio_chunk', side_effect=_extract), \
         patch.object(audio_utils, 'mean_volume_db', return_value=-20.0), \
         patch.object(Transcriber, '_transcribe_sequential', decode):
        added, empty = Transcriber().repair_gaps(
            '/audio.mp3', segments, 8.0, None, skip=skip, sparse=True)
    return added, empty, decode, chunks


RECOVERED = [_seg(0.0, 10.0, 'This episode is sponsored by NorthGuard. The quiet part about'),
             _seg(10.0, 20.0, 'quiet garden stories is that they sound like calm weather.')]


def test_case2_sparse_segment_is_a_hole():
    # The 4-word line stretched over 30 s is a hole; the 1-word music note is not.
    assert sparse_segment_holes(_case2_segments()) == [(114.78, 144.76)]


def test_dense_long_segment_is_not_a_hole():
    dense = _seg(0.0, 12.0, ' '.join(['word'] * 30))
    assert sparse_segment_holes([dense]) == []
    assert sparse_segment_holes([_seg(0.0, 6.0, 'hi')]) == []


def test_repair_replaces_sparse_segment_when_denser(tmp_path):
    segments = _case2_segments()
    sparse = next(s for s in segments if s['start'] == 114.78)
    added, empty, _decode, chunks = _repair(tmp_path, segments, RECOVERED)
    assert chunks == [(114.78, 144.76)]
    assert [s['text'][:12] for s in added] == ['This episode', 'quiet garden']
    assert added[0]['start'] == 114.78 and added[0]['novad_hole'] is True
    assert sparse not in segments
    assert empty == []


def test_repair_keeps_sparse_segment_when_not_denser(tmp_path):
    segments = _case2_segments()
    sparse = next(s for s in segments if s['start'] == 114.78)
    added, empty, _decode, _chunks = _repair(
        tmp_path, segments, [_seg(0.0, 20.0, 'I am Alex Reeder')])
    assert added == []
    assert sparse in segments
    assert empty == [{'start': 114.78, 'end': 144.76, 'reason': 'not_denser'}]


def test_recorded_sparse_hole_not_retried(tmp_path):
    segments = _case2_segments()
    added, empty, decode, chunks = _repair(
        tmp_path, segments, RECOVERED,
        skip=[{'start': 114.78, 'end': 144.76, 'reason': 'not_denser'}])
    assert (added, empty, chunks) == ([], [], [])
    decode.assert_not_called()


def test_sparse_is_opt_in(tmp_path):
    segments = _case2_segments()
    with patch.object(audio_utils, 'mean_volume_db', return_value=-20.0):
        added, empty = Transcriber().repair_gaps('/audio.mp3', segments, 8.0, None)
    assert (added, empty) == ([], [])
