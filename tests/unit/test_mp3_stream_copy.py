"""Verified MP3 copying uses existing audio and preserves coded source payloads."""

import dataclasses
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import mp3_stream_copy
from audio_processor import AudioProcessor
from mp3_stream_copy import _crc, _scan, copy_mp3
from utils.audio import get_audio_duration
from utils.time import adjust_timestamp

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg is unavailable')


@pytest.fixture(scope='module')
def source(tmp_path_factory):
    path = tmp_path_factory.mktemp('mp3-copy') / 'source.mp3'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-stream_loop', '-1',
                    '-i', str(ROOT / 'assets/replace.mp3'), '-t', '90',
                    '-c:a', 'libmp3lame', '-b:a', '128k', str(path)],
                   check=True, capture_output=True)
    return path


@pytest.mark.parametrize('sound', [False, True])
def test_actual_copy_repack_with_xing_and_optional_filler(source, tmp_path, sound):
    output = tmp_path / 'output.mp3'
    applied = copy_mp3(str(source), str(output), [{'start': 20, 'end': 30}],
                       replacement_path=str(ROOT / 'assets/replace.mp3') if sound else None)
    assert applied is not None
    source_stream, target_stream = _scan(source), _scan(output)
    assert target_stream.skip == source_stream.skip
    assert target_stream.padding == source_stream.padding
    assert abs(applied[0]['start'] - 20) <= source_stream.samples / source_stream.rate / 2
    assert abs(applied[0]['end'] - 30) <= source_stream.samples / source_stream.rate / 2
    if sound:
        assert applied[0]['replacement_duration'] > 0
    else:
        assert applied[0]['replacement_duration'] == 0
    expected = get_audio_duration(str(source)) - (applied[0]['end'] - applied[0]['start']) + applied[0]['replacement_duration']
    assert get_audio_duration(str(output)) == pytest.approx(expected, abs=1e-6)
    assert adjust_timestamp(40, applied) == pytest.approx(40 - (applied[0]['end'] - applied[0]['start']) + applied[0]['replacement_duration'])
    second = tmp_path / 'second.mp3'
    assert copy_mp3(str(output), str(second), [{'start': 40, 'end': 50}],
                    replacement_path=str(ROOT / 'assets/replace.mp3') if sound else None) is not None


def test_no_xing_existing_encoded_bytes_can_remain_identical(tmp_path):
    source = tmp_path / 'source.mp3'
    output = tmp_path / 'output.mp3'
    original = (ROOT / 'assets/replace.mp3').read_bytes()
    source.write_bytes(original * 4)
    assert copy_mp3(str(source), str(output), [{'start': 1.08, 'end': 2.16}]) is not None
    assert output.read_bytes() == original * 3


@pytest.mark.parametrize('sound', [False, True])
def test_multiple_actual_cuts(source, tmp_path, sound):
    output = tmp_path / 'output.mp3'
    applied = copy_mp3(str(source), str(output), [{'start': 10, 'end': 20}, {'start': 40, 'end': 50}],
                       replacement_path=str(ROOT / 'assets/replace.mp3') if sound else None)
    assert applied is not None and len(applied) == 2
    assert get_audio_duration(str(output)) == pytest.approx(
        get_audio_duration(str(source)) - sum(c['end'] - c['start'] - c['replacement_duration'] for c in applied), abs=1e-6)


@pytest.mark.parametrize('cuts', [
    [{'start': 0, 'end': 90}],
    [{'start': 10, 'end': 20}, {'start': 20.02, 'end': 30}],
])
def test_unsupported_edges_and_overlapping_warmup_leave_output_unchanged(source, tmp_path, cuts):
    output = tmp_path / 'output.mp3'
    existing = (ROOT / 'assets/replace.mp3').read_bytes()
    output.write_bytes(existing)
    assert copy_mp3(str(source), str(output), cuts) is None
    assert output.read_bytes() == existing


def test_failed_payload_proof_leaves_requested_cuts_and_output_unchanged(source, tmp_path, monkeypatch):
    output = tmp_path / 'output.mp3'
    existing = (ROOT / 'assets/replace.mp3').read_bytes()
    output.write_bytes(existing)
    cut = {'start': 20, 'end': 30, 'replacement_duration': 0.0}
    original = mp3_stream_copy._bridge

    def corrupt(*args):
        bridge = bytearray(original(*args))
        bridge[-1] ^= 1
        return bytes(bridge)

    monkeypatch.setattr(mp3_stream_copy, '_bridge', corrupt)
    assert copy_mp3(str(source), str(output), [cut]) is None
    assert cut == {'start': 20, 'end': 30, 'replacement_duration': 0.0}
    assert output.read_bytes() == existing


def test_snapped_cut_cannot_cross_protected_audio(source, tmp_path):
    output = tmp_path / 'output.mp3'
    stream = _scan(source)
    index = round((20 * stream.rate + stream.skip) / stream.samples)
    snapped = (index * stream.samples - stream.skip) / stream.rate
    requested = snapped - .002
    assert copy_mp3(str(source), str(output), [{'start': 10, 'end': requested}],
                    barriers=[{'start': requested + .001, 'end': requested + 1}]) is None
    assert not output.exists()


def test_crc_known_vector():
    assert _crc(b'123456789') == 0xbb3d


def test_processor_fallback_preserves_requested_sound_off_cuts(source, tmp_path, monkeypatch):
    processor = AudioProcessor(replacement_sound_enabled=False, mp3_stream_copy_enabled=True)
    monkeypatch.setattr('audio_processor.copy_mp3', lambda *args, **kwargs: None)
    output = tmp_path / 'output.mp3'
    assert processor.remove_ads(str(source), [{'start': 20, 'end': 30}], str(output)) == [
        {'start': 20, 'end': 30, 'replacement_duration': 0.0}]


def test_precise_user_edges_copy_with_inward_snap(source, tmp_path):
    processor = AudioProcessor(replacement_sound_enabled=False, mp3_stream_copy_enabled=True)
    output = tmp_path / 'output.mp3'
    applied = processor.remove_ads(str(source), [{'start': 20.001, 'end': 30.002,
                                                 'validation': {'user_confirmed': True}}], str(output))
    assert applied[0]['start'] >= 20.001 and applied[0]['end'] <= 30.002
    assert applied[0]['start'] != 20.001


def test_processor_passes_locked_edges_per_cut(source, tmp_path, monkeypatch):
    processor = AudioProcessor(replacement_sound_enabled=False, mp3_stream_copy_enabled=True)
    seen = {}

    def capture(*args, **kwargs):
        seen['locked'] = kwargs.get('locked')

    monkeypatch.setattr('audio_processor.copy_mp3', capture)
    segments = [{'start': 20.001, 'end': 30.002, 'validation': {'user_confirmed': True}},
                {'start': 50, 'end': 60}]
    processor.remove_ads(str(source), segments, str(tmp_path / 'output.mp3'))
    assert seen['locked'] == [(True, True), (False, False)]


def test_merged_cut_keeps_locks_from_its_outer_members(source, tmp_path, monkeypatch):
    processor = AudioProcessor(replacement_sound_enabled=False, mp3_stream_copy_enabled=True)
    seen = {}

    def fake_copy(*args, **kwargs):
        seen['locked'] = kwargs['locked']
        return None

    monkeypatch.setattr('audio_processor.copy_mp3', fake_copy)
    processor.remove_ads(str(source), [
        {'start': 20.0, 'end': 30.0, 'validation': {'user_confirmed': True}},
        {'start': 29.5, 'end': 40.0}], str(tmp_path / 'output.mp3'))
    assert seen['locked'] == [(True, False)]


def test_beep_skip_is_logged(source, tmp_path, caplog):
    processor = AudioProcessor(replacement_sound_enabled=True, mp3_stream_copy_enabled=True)
    with caplog.at_level('INFO'):
        processor.remove_ads(str(source), [{'start': 20, 'end': 30, 'beep': True}], str(tmp_path / 'output.mp3'))
    assert 'MP3 stream copy skipped' in caplog.text


def _decoded(path):
    stream = _scan(path)
    return stream.decoded_samples / stream.rate



@pytest.mark.parametrize('sound', [False, True])
def test_start_cut_copies(source, tmp_path, sound):
    output = tmp_path / 'output.mp3'
    applied = copy_mp3(str(source), str(output), [{'start': 0, 'end': 10}],
                       replacement_path=str(ROOT / 'assets/replace.mp3') if sound else None)
    assert applied is not None
    assert applied[0]['start'] == 0
    expected = get_audio_duration(str(source)) - applied[0]['end'] + applied[0]['replacement_duration']
    assert get_audio_duration(str(output)) == pytest.approx(expected, abs=1e-6)
    assert _scan(output).skip == _scan(source).skip


@pytest.mark.parametrize('sound', [False, True])
def test_end_cut_copies(source, tmp_path, sound):
    output = tmp_path / 'output.mp3'
    applied = copy_mp3(str(source), str(output), [{'start': 80, 'end': 90}],
                       replacement_path=str(ROOT / 'assets/replace.mp3') if sound else None)
    assert applied is not None
    assert applied[0]['end'] == pytest.approx(_decoded(source), abs=1e-9)
    assert _scan(output).padding == 0
    assert _decoded(output) == pytest.approx(applied[0]['start'] + applied[0]['replacement_duration'], abs=1e-6)


@pytest.mark.parametrize('sound', [False, True])
def test_start_middle_and_end_cuts_in_one_call(source, tmp_path, sound):
    output = tmp_path / 'output.mp3'
    applied = copy_mp3(str(source), str(output),
                       [{'start': 0, 'end': 10}, {'start': 40, 'end': 50}, {'start': 80, 'end': 90}],
                       replacement_path=str(ROOT / 'assets/replace.mp3') if sound else None)
    assert applied is not None and len(applied) == 3
    assert _decoded(output) == pytest.approx(
        _decoded(source) - sum(c['end'] - c['start'] - c['replacement_duration'] for c in applied), abs=1e-6)


def test_locked_edges_snap_inward(source, tmp_path):
    output = tmp_path / 'output.mp3'
    frame = _scan(source).samples / _scan(source).rate
    applied = copy_mp3(str(source), str(output), [{'start': 20.001, 'end': 30.002}], locked=[(True, True)])
    assert applied is not None
    assert 20.001 <= applied[0]['start'] < 20.001 + frame
    assert 30.002 - frame < applied[0]['end'] <= 30.002


def test_start_cut_with_skip_beyond_the_bridge_is_unavailable(source, tmp_path, monkeypatch, caplog):
    real_scan = mp3_stream_copy._scan

    def scan(path):
        stream = real_scan(path)
        return dataclasses.replace(stream, skip=stream.samples) if path == str(source) else stream

    monkeypatch.setattr(mp3_stream_copy, '_scan', scan)
    output = tmp_path / 'output.mp3'
    with caplog.at_level('INFO'):
        assert copy_mp3(str(source), str(output), [{'start': 0, 'end': 10}]) is None
    assert 'start skip exceeds the bridge' in caplog.text


def test_locked_cut_shorter_than_a_frame_is_unavailable(source, tmp_path):
    output = tmp_path / 'output.mp3'
    assert copy_mp3(str(source), str(output), [{'start': 20.0, 'end': 20.01}], locked=[(True, True)]) is None
    assert not output.exists()


@pytest.mark.parametrize('content', [b'', b'ID3', b'ID3\x04\0\0\x7f\x7f\x7f\x7f'])
def test_empty_and_oversized_tag_fallback_is_atomic(tmp_path, content):
    source = tmp_path / 'source.mp3'
    output = tmp_path / 'output.mp3'
    source.write_bytes(content)
    original = (ROOT / 'assets/replace.mp3').read_bytes()
    output.write_bytes(original)
    assert copy_mp3(str(source), str(output), [{'start': 1, 'end': 2}]) is None
    assert output.read_bytes() == original


@pytest.mark.parametrize('rate,channels,bitrate', [(44100, 1, '128k'), (44100, 2, '192k'), (48000, 2, '256k')])
def test_mono_and_high_bitrate_bridge_capacity(tmp_path, rate, channels, bitrate):
    source = tmp_path / 'source.mp3'
    output = tmp_path / 'output.mp3'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-stream_loop', '-1',
                    '-i', str(ROOT / 'assets/replace.mp3'), '-t', '60',
                    '-ar', str(rate), '-ac', str(channels), '-c:a', 'libmp3lame',
                    '-b:a', bitrate, str(source)], check=True, capture_output=True)
    assert copy_mp3(str(source), str(output), [{'start': 20, 'end': 30}]) is not None
    assert _scan(output).channels == channels


@pytest.mark.parametrize('kind', ['mpeg2', 'crc'])
def test_unsupported_version_and_crc_leave_existing_audio_unchanged(source, tmp_path, kind):
    output = tmp_path / 'output.mp3'
    if kind == 'mpeg2':
        unsupported = tmp_path / 'unsupported.mp3'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(source),
                        '-ar', '22050', '-c:a', 'libmp3lame', str(unsupported)],
                       check=True, capture_output=True)
    else:
        unsupported = tmp_path / 'unsupported.mp3'
        content = bytearray(source.read_bytes())
        frame = _scan(source).frames[3]
        content[frame.offset + 1] &= 0xfe
        unsupported.write_bytes(content)
    original = (ROOT / 'assets/replace.mp3').read_bytes()
    output.write_bytes(original)
    assert copy_mp3(str(unsupported), str(output), [{'start': 20, 'end': 30}]) is None
    assert output.read_bytes() == original


def test_repaired_xing_counts_toc_music_and_tag_crc(source, tmp_path):
    output = tmp_path / 'output.mp3'
    assert copy_mp3(str(source), str(output), [{'start': 20, 'end': 30}]) is not None
    stream = _scan(output)
    with output.open('rb') as audio:
        first = stream.frames[0]
        audio.seek(first.offset)
        header = audio.read(first.size)
        x = stream.xing
        assert int.from_bytes(header[x + 8:x + 12], 'big') == len(stream.frames) - 1
        total = sum(f.size for f in stream.frames)
        assert int.from_bytes(header[x + 12:x + 16], 'big') == total
        assert int.from_bytes(header[x + 148:x + 152], 'big') == total
        crc = 0
        positions = []
        position = 0
        for index, frame in enumerate(stream.frames):
            positions.append(position)
            audio.seek(frame.offset)
            data = audio.read(frame.size)
            if index:
                crc = _crc(data, crc)
            position += frame.size
        assert int.from_bytes(header[x + 152:x + 154], 'big') == crc
        assert int.from_bytes(header[x + 154:x + 156], 'big') == _crc(header[:x + 154])
        assert list(header[x + 16:x + 116]) == [
            min(255, positions[i * len(positions) // 100] * 256 // total) for i in range(100)]


def _first_frame(data):
    """Byte length of the MPEG-1 Layer III frame at the start of ``data``."""
    header = int.from_bytes(data[:4], 'big')
    assert header >> 21 == 0x7ff
    bitrate = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320][(header >> 12) & 0xf] * 1000
    rate = [44100, 48000, 32000][(header >> 10) & 3]
    return 144 * bitrate // rate + ((header >> 9) & 1)


@pytest.fixture(scope='module')
def junk_lead_source(tmp_path_factory):
    """Stitched file whose first frame ffmpeg discards as junk: plain-stereo frame before joint-stereo frames."""
    directory = tmp_path_factory.mktemp('mp3-junk')
    encodes = {}
    for name, joint, seconds in (('a', '0', '2'), ('b', '1', '60')):
        path = directory / f'{name}.mp3'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-stream_loop', '-1',
                        '-i', str(ROOT / 'assets/replace.mp3'), '-t', seconds, '-ac', '2', '-ar', '44100',
                        '-c:a', 'libmp3lame', '-b:a', '192k', '-joint_stereo', joint,
                        '-write_xing', '0', '-id3v2_version', '0', str(path)],
                       check=True, capture_output=True)
        encodes[name] = path.read_bytes()
    path = directory / 'source.mp3'
    path.write_bytes(encodes['a'][:_first_frame(encodes['a'])] + encodes['b'])
    probe = subprocess.run(['ffmpeg', '-v', 'debug', '-i', str(path), '-t', '1', '-f', 'null', '-'],
                           capture_output=True, text=True)
    if 'bytes of junk' not in probe.stderr:
        pytest.skip('this ffmpeg does not discard the mismatched lead frame')
    return path


@pytest.mark.parametrize('sound', [False, True])
def test_decoder_dropped_lead_frame_is_modelled_not_refused(junk_lead_source, tmp_path, sound, caplog):
    output = tmp_path / 'output.mp3'
    with caplog.at_level('INFO', logger='mp3_stream_copy'):
        applied = copy_mp3(str(junk_lead_source), str(output), [{'start': 20, 'end': 30}],
                           replacement_path=str(ROOT / 'assets/replace.mp3') if sound else None)
    assert applied is not None and len(applied) == 1
    assert 'dropped 1 leading frame' in caplog.text
    expected = (get_audio_duration(str(junk_lead_source)) - (applied[0]['end'] - applied[0]['start'])
                + applied[0]['replacement_duration'])
    assert get_audio_duration(str(output)) == pytest.approx(expected, abs=2e-3)


def test_lead_frame_cut_away_is_not_mistaken_for_a_dropped_frame(junk_lead_source, tmp_path):
    """A cut at the stream start replaces the junk frame with silence, so the copy must refuse."""
    output = tmp_path / 'output.mp3'
    existing = (ROOT / 'assets/replace.mp3').read_bytes()
    output.write_bytes(existing)
    assert copy_mp3(str(junk_lead_source), str(output), [{'start': 0, 'end': 10}]) is None
    assert output.read_bytes() == existing


def _end_two_frames_short(source):
    """A cut end on the boundary two frames before the stream end: the tail it
    leaves (two frames minus encoder padding) is never above the warm-up."""
    stream = _scan(source)
    return ((stream.count - 2) * stream.samples - stream.skip) / stream.rate


@pytest.mark.parametrize('sound', [False, True])
def test_final_cut_reaching_the_end_is_extended_instead_of_refused(source, tmp_path, sound):
    """An ad running into the credits can snap a frame or two short; fold the tail into the cut."""
    output = tmp_path / 'output.mp3'
    end = _end_two_frames_short(source)
    applied = copy_mp3(str(source), str(output), [{'start': 60, 'end': end}],
                       replacement_path=str(ROOT / 'assets/replace.mp3') if sound else None)
    assert applied is not None and len(applied) == 1
    stream = _scan(source)
    assert applied[0]['end'] == pytest.approx(stream.decoded_samples / stream.rate, abs=1e-6)
    assert applied[0]['end'] > end
    assert abs(applied[0]['start'] - 60) <= stream.samples / stream.rate / 2
    expected = get_audio_duration(str(source)) - (applied[0]['end'] - applied[0]['start']) + applied[0]['replacement_duration']
    # The probed output length is millisecond-rounded; the exact figure is sample-rate precise.
    assert get_audio_duration(str(output)) == pytest.approx(expected, abs=2e-3)


def test_final_cut_with_locked_end_short_of_the_stream_is_still_refused(source, tmp_path):
    output = tmp_path / 'output.mp3'
    existing = (ROOT / 'assets/replace.mp3').read_bytes()
    output.write_bytes(existing)
    assert copy_mp3(str(source), str(output), [{'start': 60, 'end': _end_two_frames_short(source)}],
                    locked=[(False, True)]) is None
    assert output.read_bytes() == existing


def test_barrier_in_the_trailing_sub_warmup_tail_refuses_the_extension(source, tmp_path):
    output = tmp_path / 'output.mp3'
    stream = _scan(source)
    end = _end_two_frames_short(source)
    decoded_end = stream.decoded_samples / stream.rate
    assert copy_mp3(str(source), str(output), [{'start': 60, 'end': end}],
                    barriers=[{'start': decoded_end - .001, 'end': decoded_end}]) is None
    assert not output.exists()


def test_chatty_decoder_stderr_does_not_deadlock(tmp_path, monkeypatch):
    script = ('import sys\nsys.stderr.write("x" * 200000)\nsys.stderr.flush()\n'
              'sys.stdout.buffer.write(b"\\0" * 4000)\n')
    monkeypatch.setattr(mp3_stream_copy, '_decode_command',
                        lambda path: [sys.executable, '-c', script])
    pipe = mp3_stream_copy._PcmPipe('ignored', 4, 20)
    try:
        with pytest.raises(mp3_stream_copy.CopyUnavailable):
            pipe.finish()
        assert not pipe.timed_out
        assert len(pipe.stderr) == 1 << 16
    finally:
        pipe.close()
