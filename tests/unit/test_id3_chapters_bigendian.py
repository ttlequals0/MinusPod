import struct

import pytest

import id3_chapters
from id3_chapters import ChapterTagError, chapter_frames, read_tag, write_tag


def _synchsafe(value):
    return bytes([(value >> 21) & 127, (value >> 14) & 127, (value >> 7) & 127, value & 127])


def _frame(name, data, big_endian):
    size = struct.pack('>I', len(data)) if big_endian else _synchsafe(len(data))
    return name + size + b'\0\0' + data


def _chap(title_len, big_endian):
    title = _frame(b'TIT2', b'\x03' + b'T' * (title_len - 1), big_endian)
    return b'c1\0' + struct.pack('>IIII', 0, 1000, 0xffffffff, 0xffffffff) + title


def _write(tmp_path, chap, big_endian):
    body = _frame(b'CHAP', chap, big_endian) + _frame(b'TIT2', b'\x03Album', big_endian)
    path = tmp_path / 'a.mp3'
    path.write_bytes(b'ID3' + bytes([4, 0, 0]) + _synchsafe(len(body)) + body + b'\xff\xfb\x90\x00' * 8)
    return path


def _title_len(tag):
    frame = chapter_frames(tag)[0][b'c1'][0]
    return len(frame.data)


@pytest.mark.parametrize('title_len', [100, 143, 400, 5000])
def test_big_endian_v24_sizes_parse(tmp_path, title_len):
    chap = _chap(title_len, True)
    tag = read_tag(_write(tmp_path, chap, True))
    assert _title_len(tag) == len(chap)


def test_big_endian_frame_of_155_bytes(tmp_path):
    chap = _chap(155 - 3 - 16 - 10, True)
    assert len(chap) == 155
    assert _title_len(read_tag(_write(tmp_path, chap, True))) == 155


@pytest.mark.parametrize('title_len', [150, 300, 1000, 16000])
def test_synchsafe_v24_sizes_still_parse(tmp_path, title_len):
    chap = _chap(title_len, False)
    assert _title_len(read_tag(_write(tmp_path, chap, False))) == len(chap)


def test_invalid_size_without_landing_still_raises(tmp_path):
    path = _write(tmp_path, _chap(200, True), True)
    data = bytearray(path.read_bytes())
    data[10 + 4:10 + 8] = b'\x00\x00\x00\xff'
    path.write_bytes(data)
    with pytest.raises(ChapterTagError):
        read_tag(path)


def test_round_trip_writes_synchsafe(tmp_path, monkeypatch):
    chap = _chap(400, True)
    path = _write(tmp_path, chap, True)
    write_tag(path, read_tag(path))
    calls = []
    real = id3_chapters._synchsafe
    monkeypatch.setattr(id3_chapters, '_synchsafe', lambda raw: calls.append(raw) or real(raw))
    data = path.read_bytes()
    assert data[10 + 4:10 + 8] == _synchsafe(len(chap))
    assert _title_len(read_tag(path)) == len(chap)
    assert _synchsafe(len(chap)) in calls
