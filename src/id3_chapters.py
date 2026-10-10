"""Preserve ID3 chapter payloads while changing their timeline."""
import os
import re
import shutil
import tempfile
import zlib
from dataclasses import dataclass, replace
from functools import cached_property

from utils.time import adjust_timestamp, span_inside_any_cut

MAX_TAG_BYTES = 64 * 1024 * 1024
MAX_FRAMES = 4096
MAX_TOC_DEPTH = 32
UNKNOWN_OFFSET = b'\xff' * 8


class ChapterTagError(ValueError):
    pass


def _synchsafe(data):
    if any(byte & 0x80 for byte in data):
        raise ChapterTagError('Invalid ID3 synchsafe integer')
    value = 0
    for byte in data:
        value = (value << 7) | byte
    return value


def _pack_synchsafe(value, length=4):
    if value < 0 or value >= 1 << (7 * length):
        raise ChapterTagError('ID3 size is out of range')
    return bytes((value >> (7 * index)) & 0x7f for index in reversed(range(length)))


def _unsync(data):
    out = bytearray()
    for index, byte in enumerate(data):
        out.append(byte)
        if byte == 0xff and (index + 1 == len(data) or data[index + 1] == 0 or data[index + 1] >= 0xe0):
            out.append(0)
    return bytes(out)


def _deunsync(data):
    return data.replace(b'\xff\x00', b'\xff')


@dataclass(frozen=True)
class Frame:
    name: bytes
    flags: bytes
    data: bytes

    def content(self, version):
        flags = self.flags[1]
        if flags & (0xc0 if version == 3 else 0x0c):
            raise ChapterTagError('Encrypted or compressed chapter frames are unsupported')
        offset = int(bool(flags & (0x20 if version == 3 else 0x40)))
        if version == 4 and flags & 1:
            if len(self.data) < offset + 4:
                raise ChapterTagError('Truncated ID3 data length indicator')
            length = _synchsafe(self.data[offset:offset + 4])
            offset += 4
            if length != len(self.data) - offset:
                raise ChapterTagError('Invalid ID3 data length indicator')
        if len(self.data) < offset:
            raise ChapterTagError('Truncated ID3 grouping field')
        return self.data[:offset], self.data[offset:]

    def with_content(self, version, content):
        prefix, _ = self.content(version)
        if version == 4 and self.flags[1] & 1:
            prefix = prefix[:-4] + _pack_synchsafe(len(content))
        return replace(self, data=prefix + content)


@dataclass(frozen=True)
class Tag:
    version: int
    revision: int
    flags: int
    extended: bytes
    frames: tuple

    @cached_property
    def chapter_index(self):
        return _parse_chapter_frames(self)


def _frames(data, version, *, all_unsynced=False, limit=MAX_FRAMES):
    frames = []
    position = 0
    while position < len(data):
        if not data[position]:
            if any(data[position:]):
                raise ChapterTagError('Invalid ID3 padding')
            break
        if len(data) - position < 10 or len(frames) >= limit:
            raise ChapterTagError('Invalid or excessive ID3 frames')
        header = data[position:position + 10]
        if not re.fullmatch(rb'[A-Z0-9]{4}', header[:4]):
            raise ChapterTagError('Invalid ID3 frame identifier')
        size = int.from_bytes(header[4:8], 'big') if version == 3 else _synchsafe(header[4:8])
        position += 10
        if not size or size > len(data) - position:
            raise ChapterTagError('Invalid ID3 frame length')
        payload = data[position:position + size]
        if version == 4 and (all_unsynced or header[9] & 2):
            payload = _deunsync(payload)
        frames.append(Frame(header[:4], header[8:10], payload))
        position += size
    return tuple(frames)


def _serialize_frames(frames, tag):
    if len(frames) > MAX_FRAMES:
        raise ChapterTagError('Excessive ID3 frames')
    result = bytearray()
    for frame in frames:
        payload = frame.data
        if tag.version == 4 and (tag.flags & 0x80 or frame.flags[1] & 2):
            payload = _unsync(payload)
        size = len(payload).to_bytes(4, 'big') if tag.version == 3 else _pack_synchsafe(len(payload))
        result.extend(frame.name + size + frame.flags + payload)
    return bytes(result)


def _extended_length(data, version):
    if len(data) < 4:
        raise ChapterTagError('Truncated ID3 extended header')
    size = int.from_bytes(data[:4], 'big') + 4 if version == 3 else _synchsafe(data[:4])
    if size > len(data) or size < (10 if version == 3 else 6):
        raise ChapterTagError('Invalid ID3 extended header length')
    if version == 3:
        if data[4:6] not in (b'\0\0', b'\x80\0') or size != (14 if data[4] & 0x80 else 10):
            raise ChapterTagError('Unsupported ID3 extended header flags')
    else:
        if data[4] != 1 or data[5] & ~0x70:
            raise ChapterTagError('Unsupported ID3 extended header flags')
        position = 6
        for flag, length in ((0x40, 0), (0x20, 5), (0x10, 1)):
            if data[5] & flag:
                if position >= size or data[position] != length or position + 1 + length > size:
                    raise ChapterTagError('Invalid ID3 extended header field')
                position += 1 + length
        if position != size:
            raise ChapterTagError('Invalid ID3 extended header data')
    return size


def _has_chapter_artifacts(data, version, flags):
    position = 0
    count = 0
    if flags & 0x40:
        if len(data) < 4:
            return b'CHAP' in data or b'CTOC' in data
        try:
            position = int.from_bytes(data[:4], 'big') + 4 if version == 3 else _synchsafe(data[:4])
        except ChapterTagError:
            return b'CHAP' in data or b'CTOC' in data
        if position < (10 if version == 3 else 6) or position > len(data):
            return b'CHAP' in data or b'CTOC' in data
    while position < len(data):
        if count == MAX_FRAMES:
            return b'CHAP' in data[position:] or b'CTOC' in data[position:]
        header = data[position:position + 10]
        if header[:4] in (b'CHAP', b'CTOC'):
            return True
        if len(header) < 10 or not re.fullmatch(rb'[A-Z0-9]{4}', header[:4]):
            return b'CHAP' in data[position:] or b'CTOC' in data[position:]
        try:
            size = int.from_bytes(header[4:8], 'big') if version == 3 else _synchsafe(header[4:8])
        except ChapterTagError:
            return b'CHAP' in data[position:] or b'CTOC' in data[position:]
        if size > len(data) - position - 10:
            return b'CHAP' in data[position:] or b'CTOC' in data[position:]
        position += 10 + size
        count += 1
    return False


def read_tag(path, *, chapters_only=False):
    with open(path, 'rb') as source:
        header = source.read(10)
        if not header.startswith(b'ID3'):
            return None
        if len(header) != 10 or header[3] not in (3, 4) or header[4] == 0xff:
            raise ChapterTagError('Unsupported ID3 chapter tag version')
        version, revision, flags = header[3:6]
        size = _synchsafe(header[6:10])
        if size > MAX_TAG_BYTES:
            raise ChapterTagError('ID3 tag exceeds the size limit')
        data = source.read(size)
        if len(data) != size:
            raise ChapterTagError('Truncated ID3 tag')
        footer = source.read(10) if flags & 0x10 else None
    if version == 3 and flags & 0x80:
        data = _deunsync(data)
    if chapters_only and not _has_chapter_artifacts(data, version, flags):
        return None
    if flags & (0x1f if version == 3 else 0x0f):
        raise ChapterTagError('Unsupported ID3 tag flags')
    if flags & 0x10 and footer != b'3DI' + header[3:]:
        raise ChapterTagError('Invalid ID3 footer')
    ext_size = _extended_length(data, version) if flags & 0x40 else 0
    tag = Tag(version, revision, flags, data[:ext_size],
              _frames(data[ext_size:], version, all_unsynced=bool(flags & 0x80)))
    chapter_frames(tag)
    return tag


def _identifier(content, position=0):
    end = content.find(b'\0', position)
    if end < position or end == position:
        raise ChapterTagError('Invalid chapter element identifier')
    return content[position:end], end + 1


def chapter_frames(tag):
    return tag.chapter_index


def _parse_chapter_frames(tag):
    chapters, tocs = {}, {}
    remaining = MAX_FRAMES - len(tag.frames)
    for frame in tag.frames:
        if frame.name not in (b'CHAP', b'CTOC'):
            continue
        _, content = frame.content(tag.version)
        identifier, position = _identifier(content)
        if identifier in chapters or identifier in tocs:
            raise ChapterTagError('Duplicate chapter element identifier')
        if frame.name == b'CHAP':
            if len(content) - position < 16:
                raise ChapterTagError('Truncated chapter times')
            start = int.from_bytes(content[position:position + 4], 'big')
            end = int.from_bytes(content[position + 4:position + 8], 'big')
            if end != 0xffffffff and end < start:
                raise ChapterTagError('Invalid chapter time range')
            remaining -= len(_frames(content[position + 16:], tag.version, limit=remaining))
            chapters[identifier] = (frame, position, start, end)
        else:
            if len(content) - position < 2 or content[position] & ~3:
                raise ChapterTagError('Invalid chapter table flags')
            count = content[position + 1]
            children = []
            cursor = position + 2
            for _ in range(count):
                child, cursor = _identifier(content, cursor)
                if child in children:
                    raise ChapterTagError('Duplicate chapter table reference')
                children.append(child)
            remaining -= len(_frames(content[cursor:], tag.version, limit=remaining))
            tocs[identifier] = (frame, position, children, cursor)

    depths = {}

    def visit(identifier, ancestors):
        if identifier in ancestors or len(ancestors) >= MAX_TOC_DEPTH:
            raise ChapterTagError('Cyclic or excessive chapter table nesting')
        if identifier in depths:
            return depths[identifier]
        depth = 0
        if identifier in tocs:
            for child in tocs[identifier][2]:
                if child not in chapters and child not in tocs:
                    raise ChapterTagError('Unknown chapter table reference')
                depth = max(depth, 1 + visit(child, ancestors + (identifier,)))
        if depth >= MAX_TOC_DEPTH:
            raise ChapterTagError('Cyclic or excessive chapter table nesting')
        depths[identifier] = depth
        return depth

    for identifier in tocs:
        visit(identifier, ())
    return chapters, tocs


def remap_tag(tag, cuts, replacement_duration, new_duration):
    chapters, tocs = chapter_frames(tag)
    kept = {}
    for identifier, (frame, position, start, end) in chapters.items():
        start_seconds = start / 1000
        end_seconds = end / 1000 if end != 0xffffffff else None
        if end_seconds is not None and span_inside_any_cut(start_seconds, end_seconds, cuts):
            continue
        new_start = min(new_duration, adjust_timestamp(start_seconds, cuts, replacement_duration))
        new_end = min(new_duration, adjust_timestamp(end_seconds, cuts, replacement_duration)) if end_seconds is not None else None
        if new_start >= new_duration or (new_end is not None and new_end <= new_start):
            continue
        _, content = frame.content(tag.version)
        times = round(new_start * 1000).to_bytes(4, 'big') + (round(new_end * 1000) if new_end is not None else 0xffffffff).to_bytes(4, 'big')
        kept[identifier] = frame.with_content(tag.version, content[:position] + times + UNKNOWN_OFFSET + content[position + 16:])

    return _replace_chapters(tag, kept, tocs)


def _replace_chapters(tag, kept, tocs, generated=()):
    def preserve_toc(identifier):
        if identifier in kept:
            return True
        frame, position, children, cursor = tocs[identifier]
        surviving = [child for child in children if child in kept or (child in tocs and preserve_toc(child))]
        if not surviving:
            return False
        _, content = frame.content(tag.version)
        payload = content[:position + 1] + bytes([len(surviving)]) + b''.join(child + b'\0' for child in surviving) + content[cursor:]
        kept[identifier] = frame.with_content(tag.version, payload)
        return True

    for identifier in tocs:
        preserve_toc(identifier)
    if generated:
        current = replace(tag, frames=tuple(kept.values()))
        chapters, parsed_tables = chapter_frames(current)
        tables = dict(parsed_tables)
        ranges = {}

        def timeline(identifier):
            if identifier not in ranges:
                if identifier in chapters:
                    ranges[identifier] = chapters[identifier][2:4]
                else:
                    spans = [timeline(child) for child in tables[identifier][2]]
                    ranges[identifier] = (min(span[0] for span in spans), max(span[1] for span in spans))
            return ranges[identifier]

        def insert(identifier, child):
            frame, position, children, cursor = tables[identifier]
            children = list(children)
            start = timeline(child)[0]
            for existing in children:
                if existing in tables and timeline(existing)[0] <= start < timeline(existing)[1]:
                    insert(existing, child)
                    return
            if len(children) == 255:
                raise ChapterTagError('Excessive chapter table children')
            index = next((index for index, existing in enumerate(children)
                          if timeline(existing)[0] > start), len(children))
            children.insert(index, child)
            _, content = frame.content(tag.version)
            kept[identifier] = frame.with_content(tag.version, content[:position + 1] + bytes([len(children)])
                                                 + b''.join(node + b'\0' for node in children) + content[cursor:])
            tables[identifier] = (frame, position, children, cursor)

        roots = [identifier for identifier in tocs
                 if identifier in kept and kept[identifier].content(tag.version)[1][tocs[identifier][1]] & 2]
        if roots:
            for child in generated:
                insert(roots[0], child)
        else:
            identifier = b'minuspod-toc'
            while identifier in kept or identifier in tocs:
                identifier += b'-'
            referenced = {child for table in tables.values() for child in table[2]}
            children = [node for node in kept if node not in referenced and node not in generated]
            for child in generated:
                index = next((index for index, existing in enumerate(children)
                              if timeline(existing)[0] > timeline(child)[0]), len(children))
                children.insert(index, child)
            if len(children) > 255:
                raise ChapterTagError('Excessive chapter table children')
            payload = identifier + b'\0\x03' + bytes([len(children)]) + b''.join(child + b'\0' for child in children)
            kept[identifier] = Frame(b'CTOC', b'\0\0', payload)
    frames = []
    written = set()
    for frame in tag.frames:
        if frame.name in (b'CHAP', b'CTOC'):
            _, content = frame.content(tag.version)
            identifier, _ = _identifier(content)
            if identifier in kept:
                frames.append(kept[identifier])
                written.add(identifier)
        else:
            frames.append(frame)
    frames.extend(frame for identifier, frame in kept.items() if identifier not in written)
    return replace(tag, frames=tuple(frames))


def set_chapters(tag, spans):
    chapters, tocs = chapter_frames(tag)
    kept = {}
    generated = []
    for index, span in enumerate(spans):
        known = span.get('_id3_id')
        if known:
            try:
                identifier = bytes.fromhex(known)
                frame, position, _, _ = chapters[identifier]
            except (ValueError, TypeError, KeyError) as error:
                raise ChapterTagError('Publisher chapter identity is unavailable') from error
            _, content = frame.content(tag.version)
            payload = content[:position]
            suffix = content[position + 16:]
            times = content[position:position + 8]
        else:
            identifier = f'minuspod-{index}'.encode('ascii')
            while identifier in chapters or identifier in tocs or identifier in kept:
                identifier += b'-'
            payload = identifier + b'\0'
            title = span.get('title') or ''
            text = b'\x01' + title.encode('utf-16') if tag.version == 3 else b'\x03' + title.encode('utf-8')
            suffix = _serialize_frames((Frame(b'TIT2', b'\0\0', text),), replace(tag, flags=tag.flags & ~0x80))
            frame = Frame(b'CHAP', b'\0\0', b'')
            generated.append(identifier)
            times = round(span['start'] * 1000).to_bytes(4, 'big') + round(span['end'] * 1000).to_bytes(4, 'big')
        if identifier in kept:
            raise ChapterTagError('Duplicate chapter identity in output')
        kept[identifier] = frame.with_content(tag.version, payload + times + UNKNOWN_OFFSET + suffix)
    return _replace_chapters(tag, kept, tocs, generated)


def _extended_for_write(tag, body):
    extended = bytearray(tag.extended)
    if not extended:
        return b''
    if tag.version == 3:
        extended[6:10] = b'\0' * 4
        if extended[4] & 0x80:
            extended[10:14] = zlib.crc32(body).to_bytes(4, 'big')
    else:
        position = 6
        for flag in (0x40, 0x20, 0x10):
            if extended[5] & flag:
                length = extended[position]
                if flag == 0x20:
                    extended[position + 1:position + 6] = _pack_synchsafe(zlib.crc32(body), 5)
                if flag == 0x10:
                    raise ChapterTagError('Restricted ID3 tags cannot be rewritten safely')
                position += length + 1
    return bytes(extended)


def validate_for_write(tag):
    chapter_frames(tag)
    _extended_for_write(tag, b'')


def merge_chapter_frames(target, source):
    if target is None:
        target = Tag(source.version, 0, 0, b'', ())
    if target.version != source.version:
        raise ChapterTagError('Source and output chapter tag versions differ')
    frames = tuple(frame for frame in target.frames if frame.name not in (b'CHAP', b'CTOC'))
    chapters = tuple(frame for frame in source.frames if frame.name in (b'CHAP', b'CTOC'))
    return replace(target, frames=frames + chapters)


def _text(data):
    if not data or data[0] not in (0, 1, 2, 3):
        return ''
    encoding = ('latin1', 'utf-16', 'utf-16-be', 'utf-8')[data[0]]
    try:
        return data[1:].decode(encoding).rstrip('\0')
    except UnicodeError:
        return ''


def _description_end(data, position, encoding):
    width = 2 if encoding in (1, 2) else 1
    while position + width <= len(data):
        if data[position:position + width] == b'\0' * width:
            return position + width
        position += width
    return None


def chapters_from_tag(tag):
    chapters, _ = chapter_frames(tag)
    result = []
    for identifier, (frame, position, start, end) in chapters.items():
        _, content = frame.content(tag.version)
        entry = {'start': start / 1000, 'end': end / 1000,
                 'title': '', '_id3_id': identifier.hex(),
                 '_id3_end': end / 1000 if end != 0xffffffff else None}
        for subframe in _frames(content[position + 16:], tag.version):
            try:
                _, data = subframe.content(tag.version)
            except ChapterTagError:
                continue
            if subframe.name == b'TIT2' and not entry['title']:
                entry['title'] = _text(data)
            elif subframe.name == b'WXXX' and data and data[0] in (0, 1, 2, 3):
                offset = _description_end(data, 1, data[0])
                url = data[offset:].decode('latin1').rstrip('\0') if offset is not None else ''
                if url.startswith(('http://', 'https://')) and 'url' not in entry:
                    entry['url'] = url
            elif subframe.name == b'APIC' and data and data[0] in (0, 1, 2, 3):
                mime_end = data.find(b'\0', 1)
                if mime_end < 0 or mime_end + 2 >= len(data):
                    continue
                offset = _description_end(data, mime_end + 2, data[0])
                if offset is not None and '_id3_image' not in entry:
                    entry['_id3_image'] = data[offset:]
        result.append(entry)
    return sorted(result, key=lambda item: item['start'])


def write_tag(path, tag):
    validate_for_write(tag)
    body = _serialize_frames(tag.frames, tag)
    body = _extended_for_write(tag, body) + body
    if tag.version == 3 and tag.flags & 0x80:
        body = _unsync(body)
    if len(body) > MAX_TAG_BYTES:
        raise ChapterTagError('ID3 tag exceeds the size limit')
    header = b'ID3' + bytes([tag.version, tag.revision, tag.flags]) + _pack_synchsafe(len(body))
    with open(path, 'rb') as source:
        original_header = source.read(10)
        skip = 0
        if original_header.startswith(b'ID3'):
            if len(original_header) != 10:
                raise ChapterTagError('Truncated output ID3 tag')
            skip = 10 + _synchsafe(original_header[6:10]) + (10 if original_header[5] & 0x10 else 0)
            if skip > os.fstat(source.fileno()).st_size:
                raise ChapterTagError('Truncated output ID3 tag')
        source.seek(skip)
        descriptor, temporary = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(path)), suffix='.id3tmp')
        try:
            with os.fdopen(descriptor, 'wb') as target:
                target.write(header + body)
                if tag.flags & 0x10:
                    target.write(b'3DI' + header[3:])
                shutil.copyfileobj(source, target)
            os.chmod(temporary, os.stat(path).st_mode)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
