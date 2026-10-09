"""Verified MP3 copying uses existing audio and preserves coded source payloads."""

import shutil
import subprocess
from pathlib import Path

import pytest

import mp3_stream_copy
from audio_processor import AudioProcessor
from mp3_stream_copy import _crc, _scan, copy_mp3
from utils.audio import get_audio_duration
from utils.time import adjust_timestamp

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg is unavailable')


@pytest.fixture
def source(tmp_path):
    path = tmp_path / 'source.mp3'
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
    [{'start': 0, 'end': 10}],
    [{'start': 80, 'end': 90}],
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


def test_precise_user_edges_skip_copy(source, tmp_path, monkeypatch):
    processor = AudioProcessor(replacement_sound_enabled=False, mp3_stream_copy_enabled=True)
    monkeypatch.setattr('audio_processor.copy_mp3', lambda *args, **kwargs: pytest.fail('Exact user cut must not snap'))
    output = tmp_path / 'output.mp3'
    applied = processor.remove_ads(str(source), [{'start': 20.001, 'end': 30.002,
                                                 'validation': {'user_confirmed': True}}], str(output))
    assert applied[0]['start'] == 20.001 and applied[0]['end'] == 30.002


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
