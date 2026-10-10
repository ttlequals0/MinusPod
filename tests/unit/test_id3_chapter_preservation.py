import re
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

import pytest

from audio_processor import AudioProcessor
from embedded_chapters import embed_chapters, probe_chapters, source_chapter_tag
from id3_chapters import (
    ChapterTagError, Frame, Tag, chapter_frames, read_tag, remap_tag, write_tag,
    merge_chapter_frames, set_chapters,
)
from utils.audio import get_audio_duration

ROOT = Path(__file__).resolve().parents[2]


def _size(value):
    return bytes([(value >> 21) & 127, (value >> 14) & 127, (value >> 7) & 127, value & 127])


def _frame(name, data, version):
    length = struct.pack('>I', len(data)) if version == 3 else _size(len(data))
    return name + length + b'\0\0' + data


def _fixture(tmp_path, version):
    image = (ROOT / 'frontend/public/recents-artwork.png').read_bytes()
    subframes = (
        _frame(b'TIT2', b'\x01' + 'Intro'.encode('utf-16'), version)
        + _frame(b'APIC', b'\0image/png\0\x03\0' + image, version)
        + _frame(b'WXXX', b'\0\0https://example.com/chapter', version)
        + _frame(b'XABC', b'opaque\xff\xe1\0payload', version)
    )
    first = b'first\0' + struct.pack('>IIII', 125, 20000, 100, 200) + subframes
    removed = b'ad\0' + struct.pack('>IIII', 21000, 29000, 200, 300)
    last = b'last\0' + struct.pack('>IIII', 30000, 40000, 300, 400) + subframes
    inner = b'inner\0\x01\x02ad\0last\0' + _frame(b'XABC', b'inner opaque', version)
    outer = b'top\0\x03\x02first\0inner\0' + _frame(b'TIT2', b'\0Contents', version)
    metadata = b''.join(_frame(name, value, version) for name, value in (
        (b'CHAP', first), (b'CHAP', removed), (b'CHAP', last),
        (b'CTOC', outer), (b'CTOC', inner), (b'XABC', b'untouched metadata'),
    ))
    original = (ROOT / 'assets/replace.mp3').read_bytes()
    if original.startswith(b'ID3'):
        count = sum(byte << shift for byte, shift in zip(original[6:10], (21, 14, 7, 0), strict=True))
        original = original[10 + count:]
    path = tmp_path / 'episode.mp3'
    path.write_bytes(b'ID3' + bytes([version, 0, 0]) + _size(len(metadata)) + metadata + original)
    return path, subframes, original


@pytest.mark.parametrize('version', [3, 4])
def test_remap_preserves_subframes_hierarchy_and_audio_bytes(tmp_path, version):
    path, subframes, audio = _fixture(tmp_path, version)
    original = read_tag(path)
    mapped = remap_tag(original, [{'start': 20, 'end': 30, 'replacement_duration': 0}], 2, 30)
    write_tag(path, mapped)
    current = read_tag(path)
    chapters, tocs = chapter_frames(current)
    assert set(chapters) == {b'first', b'last'}
    assert chapters[b'first'][2:4] == (125, 20000)
    assert chapters[b'last'][2:4] == (20000, 30000)
    for frame, position, _, _ in chapters.values():
        assert frame.data[position + 8:position + 16] == b'\xff' * 8
        assert frame.data[position + 16:] == subframes
    assert tocs[b'top'][2] == [b'first', b'inner']
    assert tocs[b'inner'][2] == [b'last']
    assert tocs[b'top'][0].data[tocs[b'top'][3]:] == original.frames[3].data[original.frames[3].data.index(b'inner\0') + 6:]
    assert current.frames[-1] == original.frames[-1]
    content = path.read_bytes()
    offset = 10 + sum(byte << shift for byte, shift in zip(content[6:10], (21, 14, 7, 0), strict=True))
    assert content[offset:] == audio
    before = content
    write_tag(path, current)
    assert path.read_bytes() == before


@pytest.mark.parametrize('version, flags', [(3, 0x80), (4, 0x80), (4, 0x10)])
def test_unsync_and_footer_roundtrip(tmp_path, version, flags):
    path, subframes, _ = _fixture(tmp_path, version)
    tag = read_tag(path)
    transformed = Tag(version, 0, flags, b'', tag.frames)
    write_tag(path, transformed)
    loaded = read_tag(path)
    assert loaded.frames == tag.frames
    mapped = remap_tag(loaded, [], 0, 40)
    write_tag(path, mapped)
    assert chapter_frames(read_tag(path))[0][b'first'][0].data.endswith(subframes)


@pytest.mark.parametrize('version, flags', [(3, b'\0\x80'), (3, b'\0\x40'), (4, b'\0\x08'), (4, b'\0\x04')])
def test_unsupported_chapter_encoding_leaves_audio_untouched(tmp_path, version, flags):
    path, _, _ = _fixture(tmp_path, version)
    tag = read_tag(path)
    bad = Tag(version, 0, 0, b'', (Frame(b'CHAP', flags, tag.frames[0].data),))
    before = path.read_bytes()
    with pytest.raises(ChapterTagError, match='Encrypted or compressed'):
        write_tag(path, bad)
    assert path.read_bytes() == before


def test_duplicate_ids_and_cycle_are_rejected(tmp_path):
    path, _, _ = _fixture(tmp_path, 4)
    tag = read_tag(path)
    with pytest.raises(ChapterTagError, match='Duplicate chapter element'):
        chapter_frames(Tag(4, 0, 0, b'', tag.frames + (tag.frames[0],)))
    loop = Frame(b'CTOC', b'\0\0', b'loop\0\x03\x01loop\0')
    with pytest.raises(ChapterTagError, match='Cyclic'):
        chapter_frames(Tag(4, 0, 0, b'', (loop,)))


def _five_bytes(value):
    return bytes((value >> shift) & 127 for shift in (28, 21, 14, 7, 0))


def test_independent_v3_tag_unsync_input(tmp_path):
    path, subframes, audio = _fixture(tmp_path, 3)
    content = path.read_bytes()
    body = content[10:-len(audio)]
    encoded = re.sub(rb'\xff(?=[\x00\xe0-\xff]|$)', b'\xff\0', body)
    path.write_bytes(b'ID3\x03\0\x80' + _size(len(encoded)) + encoded + audio)
    tag = read_tag(path)
    frame, position, _, _ = chapter_frames(tag)[0][b'first']
    assert frame.data[position + 16:] == subframes
    write_tag(path, remap_tag(tag, [], 0, 40))
    assert chapter_frames(read_tag(path))[0][b'first'][0].data.endswith(subframes)


def test_independent_v4_frame_unsync_grouping_and_dli(tmp_path):
    path, subframes, audio = _fixture(tmp_path, 4)
    tag = read_tag(path)
    chapter = tag.frames[0].data
    data = b'\x09' + _size(len(chapter)) + chapter
    encoded = re.sub(rb'\xff(?=[\x00\xe0-\xff]|$)', b'\xff\0', data)
    body = b'CHAP' + _size(len(encoded)) + b'\0\x43' + encoded
    path.write_bytes(b'ID3\x04\0\0' + _size(len(body)) + body + audio)
    original = read_tag(path)
    mapped = remap_tag(original, [], 0, 40)
    write_tag(path, mapped)
    loaded = read_tag(path)
    frame, position, start, end = chapter_frames(loaded)[0][b'first']
    prefix, content = frame.content(4)
    assert prefix[0] == 9 and prefix[1:] == _size(len(content))
    assert content[position + 16:] == subframes
    assert (start, end) == (125, 20000)


@pytest.mark.parametrize('version', [3, 4])
def test_extended_header_crc_recomputed_independently(tmp_path, version):
    path, _, audio = _fixture(tmp_path, version)
    content = path.read_bytes()
    frames = content[10:-len(audio)]
    padding = b'\0' * 7
    if version == 3:
        extended = struct.pack('>I', 10) + b'\x80\0' + struct.pack('>I', len(padding)) + struct.pack('>I', zlib.crc32(frames))
    else:
        extended = _size(12) + b'\x01\x20\x05' + _five_bytes(zlib.crc32(frames + padding))
    body = extended + frames + padding
    path.write_bytes(b'ID3' + bytes([version, 0, 0x40]) + _size(len(body)) + body + audio)
    write_tag(path, remap_tag(read_tag(path), [], 0, 40))
    content = path.read_bytes()
    size = sum(byte << shift for byte, shift in zip(content[6:10], (21, 14, 7, 0), strict=True))
    body = content[10:10 + size]
    if version == 3:
        assert body[6:10] == b'\0' * 4
        assert body[10:14] == struct.pack('>I', zlib.crc32(body[14:]))
    else:
        assert body[7:12] == _five_bytes(zlib.crc32(body[12:]))


def test_restricted_tag_fails_before_no_cut_copy(tmp_path):
    path, _, audio = _fixture(tmp_path, 4)
    content = path.read_bytes()
    extended = _size(8) + b'\x01\x10\x01\0'
    body = extended + content[10:-len(audio)]
    path.write_bytes(b'ID3\x04\0\x40' + _size(len(body)) + body + audio)
    output = tmp_path / 'served.mp3'
    output.write_bytes(audio)
    before = path.read_bytes()
    with pytest.raises(ChapterTagError, match='Restricted'):
        AudioProcessor().remove_ads(str(path), [], str(output))
    assert path.read_bytes() == before
    assert output.read_bytes() == audio


@pytest.mark.parametrize('end', [15000, 35000, 0xffffffff])
def test_embedding_retains_publisher_explicit_end_and_gap(tmp_path, end):
    path, _, _ = _fixture(tmp_path, 4)
    tag = read_tag(path)
    first = tag.frames[0]
    position = len(b'first\0')
    first = Frame(first.name, first.flags, first.data[:position + 4] + struct.pack('>I', end) + first.data[position + 8:])
    tag = Tag(4, 0, 0, b'', (first, *tag.frames[1:]))
    updated = set_chapters(tag, [{'start': 1, 'end': 40, 'title': 'Changed', '_id3_id': b'first'.hex()}])
    assert chapter_frames(updated)[0][b'first'][2:4] == (125, end)


def test_generated_chapter_is_inserted_into_nested_ordered_table(tmp_path):
    path, _, _ = _fixture(tmp_path, 4)
    tag = read_tag(path)
    spans = [{'start': start / 1000, 'end': end / 1000, '_id3_id': identifier.hex()}
             for identifier, (_, _, start, end) in chapter_frames(tag)[0].items()]
    spans.append({'start': 25, 'end': 26, 'title': 'Ad'})
    updated = set_chapters(tag, spans)
    chapters, tables = chapter_frames(updated)
    generated = next(identifier for identifier in chapters if identifier.startswith(b'minuspod-'))
    assert tables[b'top'][2] == [b'first', b'inner']
    assert tables[b'inner'][2] == [b'ad', generated, b'last']
    original_table = chapter_frames(tag)[1][b'inner']
    assert tables[b'inner'][0].data[tables[b'inner'][3]:] == original_table[0].data[original_table[3]:]


def test_new_root_references_only_unparented_nodes(tmp_path):
    path, _, _ = _fixture(tmp_path, 4)
    tag = read_tag(path)
    root = tag.frames[3]
    tag = Tag(4, 0, 0, b'', (*tag.frames[:3], Frame(root.name, root.flags, root.data.replace(b'top\0\x03', b'top\0\x01')), *tag.frames[4:]))
    spans = [{'start': start / 1000, 'end': end / 1000, '_id3_id': identifier.hex()}
             for identifier, (_, _, start, end) in chapter_frames(tag)[0].items()]
    spans.append({'start': 25, 'end': 26, 'title': 'Ad'})
    chapters, tables = chapter_frames(set_chapters(tag, spans))
    generated = next(identifier for identifier in chapters if identifier.startswith(b'minuspod-'))
    assert tables[b'minuspod-toc'][2] == [b'top', generated]
    assert tables[b'top'][2] == [b'first', b'inner']
    assert tables[b'inner'][2] == [b'ad', b'last']


def test_merge_keeps_output_global_title_and_artwork(tmp_path):
    path, subframes, _ = _fixture(tmp_path, 4)
    source = read_tag(path)
    title = Frame(b'TIT2', b'\0\0', b'\x03Output title')
    artwork = Frame(b'APIC', b'\0\0', b'\0image/png\0\x03\0' + (ROOT / 'frontend/public/recents-artwork.png').read_bytes())
    target = Tag(4, 0, 0, b'', (title, artwork, Frame(b'TXXX', b'\0\0', b'\0watermark\0processed')))
    merged = merge_chapter_frames(target, source)
    assert merged.frames[:3] == target.frames
    first, position, _, _ = chapter_frames(merged)[0][b'first']
    assert first.data[position + 16:] == subframes
    assert not any(frame.name == b'XABC' for frame in merged.frames)
    with pytest.raises(ChapterTagError, match='versions differ'):
        merge_chapter_frames(Tag(3, 0, 0, b'', target.frames), source)


def test_raw_chapters_are_found_without_ffprobe_detection(tmp_path):
    path, _, _ = _fixture(tmp_path, 4)
    assert set(chapter_frames(source_chapter_tag(path, []))[0]) == {b'first', b'ad', b'last'}
    original = path.read_bytes()
    position = original.index(b'CHAP')
    malformed = bytearray(original)
    malformed[position + 9] = 8
    path.write_bytes(malformed)
    with pytest.raises(ChapterTagError, match='Encrypted or compressed'):
        AudioProcessor().remove_ads(str(path), [], str(tmp_path / 'output.mp3'))
    assert path.read_bytes() == malformed


def test_unrelated_v2_tag_does_not_block_no_cut_copy(tmp_path):
    path = tmp_path / 'legacy.mp3'
    original = b'ID3\x02\0\0' + _size(0) + (ROOT / 'assets/replace.mp3').read_bytes()
    path.write_bytes(original)
    output = tmp_path / 'output.mp3'
    assert AudioProcessor().remove_ads(str(path), [], str(output)) == []
    assert output.read_bytes() == original


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg is unavailable')
@pytest.mark.parametrize('version', [3, 4])
@pytest.mark.parametrize('irrelevant', ['empty_frame', 'padding', 'unknown_flags'])
def test_playable_mp3_without_chapters_does_not_require_strict_metadata(tmp_path, version, irrelevant):
    source, _ = _long_fixture(tmp_path, version)
    content = source.read_bytes()
    size = sum(byte << shift for byte, shift in zip(content[6:10], (21, 14, 7, 0), strict=True))
    audio = content[10 + size:]
    body = _frame(b'TIT2', b'\0Episode', version)
    flags = 0
    if irrelevant == 'empty_frame':
        body = _frame(b'COMM', b'', version) + body
    elif irrelevant == 'padding':
        body += b'\0\0invalid padding'
    else:
        flags = 1
    source.write_bytes(b'ID3' + bytes([version, 0, flags]) + _size(len(body)) + body + audio)
    assert source_chapter_tag(source, False) is None
    processor = AudioProcessor(bitrate='64k')
    converted = processor.convert_to_mp3(str(source))
    normalized = processor.normalize_audio(str(source))
    try:
        assert converted is not None and get_audio_duration(converted) > 59
        assert normalized is not None and get_audio_duration(normalized) > 59
        cut = tmp_path / 'cut.mp3'
        assert processor.remove_ads(str(source), [{'start': 20, 'end': 30}], str(cut))
        assert get_audio_duration(str(cut)) > 50
    finally:
        for output in (converted, normalized):
            if output:
                Path(output).unlink(missing_ok=True)


@pytest.mark.parametrize('version', [3, 4])
def test_malformed_irrelevant_length_cannot_hide_real_chapter(tmp_path, version):
    path, _, audio = _fixture(tmp_path, version)
    original = path.read_bytes()
    body = original[10:-len(audio)]
    length = struct.pack('>I', len(body) + 100) if version == 3 else _size(len(body) + 100)
    body = b'COMM' + length + b'\0\0' + body
    path.write_bytes(b'ID3' + bytes([version, 0, 0]) + _size(len(body)) + body + audio)
    output = tmp_path / 'served.mp3'
    output.write_bytes(audio)
    with pytest.raises(ChapterTagError, match='frame length'):
        AudioProcessor().remove_ads(str(path), [], str(output))
    assert output.read_bytes() == audio


@pytest.mark.parametrize('version', [3, 4])
def test_malformed_frame_identifier_cannot_hide_real_chapter(tmp_path, version):
    path, _, audio = _fixture(tmp_path, version)
    body = path.read_bytes()[10:-len(audio)]
    length = struct.pack('>I', len(body)) if version == 3 else _size(len(body))
    body = b'comm' + length + b'\0\0' + body
    path.write_bytes(b'ID3' + bytes([version, 0, 0]) + _size(len(body)) + body + audio)
    with pytest.raises(ChapterTagError, match='frame identifier'):
        source_chapter_tag(path, False)


def test_valid_nonchapter_payload_containing_chapter_word_is_not_an_artifact(tmp_path):
    path = tmp_path / 'plain.mp3'
    body = _frame(b'COMM', b'\0eng\0CHAP and CTOC are words in this comment', 4)
    path.write_bytes(b'ID3\x04\0\0' + _size(len(body)) + body + (ROOT / 'assets/replace.mp3').read_bytes())
    assert source_chapter_tag(path, False) is None


def _long_fixture(tmp_path, version):
    path, subframes, _ = _fixture(tmp_path, version)
    tag = read_tag(path)
    audio = tmp_path / 'long.mp3'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-stream_loop', '-1', '-i', str(ROOT / 'assets/replace.mp3'),
                    '-t', '60', '-vn', '-c:a', 'libmp3lame', '-b:a', '64k', str(audio)],
                   check=True, capture_output=True)
    write_tag(audio, Tag(version, 0, 0, b'', (*tag.frames, Frame(b'TIT2', b'\0\0', b'\0Publisher title'))))
    return audio, subframes


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg is unavailable')
@pytest.mark.parametrize('version', [3, 4])
def test_actual_cut_convert_leveling_and_embed_keep_rich_chapters(tmp_path, version):
    audio, subframes = _long_fixture(tmp_path, version)
    processor = AudioProcessor(replace_audio_path=str(ROOT / 'assets/replace.mp3'), bitrate='64k')
    cut = tmp_path / 'cut.mp3'
    applied = processor.remove_ads(str(audio), [{'start': 20, 'end': 30}], str(cut))
    assert applied and len(applied) == 1
    loaded = read_tag(cut)
    chapters, tables = chapter_frames(loaded)
    assert loaded.version == version
    assert set(chapters) == {b'first', b'last'}
    assert tables[b'inner'][2] == [b'last']
    assert chapters[b'last'][2] == round((20 + applied[0]['replacement_duration']) * 1000)
    for operation in ('convert_to_mp3', 'normalize_audio'):
        output = tmp_path / f'{operation}.mp3'
        if operation == 'normalize_audio':
            normalized = processor.normalize_audio(str(cut))
            assert normalized is not None
            shutil.move(normalized, output)
        else:
            converted = processor.convert_to_mp3(str(cut))
            assert converted is not None
            shutil.move(converted, output)
        current = read_tag(output)
        assert current.version == version
        current_chapters = chapter_frames(current)[0]
        assert {identifier: row[2:4] for identifier, row in current_chapters.items()} == {
            identifier: row[2:4] for identifier, row in chapters.items()}
        assert any(frame.name == b'TIT2' and b'Publisher title' in frame.data for frame in current.frames)
        for frame, position, _, _ in current_chapters.values():
            assert frame.data[position + 16:] == subframes
        entries = [{'startTime': ch['start'], 'title': ch['title'], '_id3_id': ch['_id3_id']}
                   for ch in probe_chapters(str(output))]
        assert embed_chapters(str(output), entries, duration=get_audio_duration(str(output)))
        first_embed = chapter_frames(read_tag(output))
        assert embed_chapters(str(output), entries, duration=get_audio_duration(str(output)))
        assert chapter_frames(read_tag(output)) == first_embed


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg is unavailable')
@pytest.mark.parametrize('version', [3, 4])
@pytest.mark.parametrize('sound', [False, True])
def test_verified_copy_preserves_rich_chapters_on_actual_timeline(tmp_path, version, sound):
    source, subframes = _long_fixture(tmp_path, version)
    output = tmp_path / 'copy.mp3'
    processor = AudioProcessor(replacement_sound_enabled=sound, mp3_stream_copy_enabled=True)
    applied = processor.remove_ads(str(source), [{'start': 20, 'end': 30}], str(output))
    assert applied is not None and applied[0]['start'] != 20
    chapters, tables = chapter_frames(read_tag(output))
    assert set(chapters) == {b'first', b'last'}
    for frame, position, _, _ in chapters.values():
        assert frame.data[position + 16:] == subframes
    assert tables[b'inner'][2] == [b'last']
    shift = applied[0]['end'] - applied[0]['start'] - applied[0]['replacement_duration']
    assert applied[0]['start'] < 30 < applied[0]['end']
    assert chapters[b'last'][2] == round(applied[0]['start'] * 1000)
    assert chapters[b'last'][3] == round((40 - shift) * 1000)
