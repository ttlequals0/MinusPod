"""MP3 frame copying verified against the retained source's decoded samples."""

import bisect
import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path

from config import FFMPEG_LONG_TIMEOUT, FFPROBE_TIMEOUT
from utils.audio import get_audio_duration
from utils import subprocess_registry
from utils.ffmpeg_run import SAFE_MEDIA_INPUT_ARGS
from utils.subprocess_registry import tracked_run

logger = logging.getLogger(__name__)
MAX_FRAMES = 1_000_000
MAX_PCM_BYTES = 4 * 1024 * 1024 * 1024
# Leading frames the demuxer may drop as junk (see _validate) before the copy is refused.
LEAD_JUNK_MAX_FRAMES = 4


class CopyUnavailable(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Frame:
    offset: int
    size: int
    reservoir: int
    capacity: int


@dataclass(frozen=True)
class Stream:
    frames: tuple[Frame, ...]
    rate: int
    channels: int
    samples: int
    prefix: int
    tail: int
    xing: int | None
    skip: int
    padding: int

    @property
    def first(self):
        return int(self.xing is not None)

    @property
    def count(self):
        return len(self.frames) - self.first

    @property
    def decoded_samples(self):
        return self.count * self.samples - self.skip - self.padding


def _side_length(channels):
    return 17 if channels == 1 else 32


def _scan(path):
    frames = []
    size = os.path.getsize(path)
    with open(path, 'rb') as source:
        header = source.read(10)
        prefix = 0
        if header[:3] == b'ID3':
            if len(header) != 10:
                raise CopyUnavailable('Truncated MP3 tag')
            if header[3] not in (3, 4) or any(b & 128 for b in header[6:]):
                raise CopyUnavailable('Unsupported MP3 tag')
            prefix = 10 + sum(b << shift for b, shift in zip(header[6:], (21, 14, 7, 0), strict=True))
            prefix += 10 if header[3] == 4 and header[5] & 16 else 0
        source.seek(prefix)
        layout = None
        while source.tell() < size:
            offset = source.tell()
            header = source.read(4)
            if size - offset == 128 and header[:3] == b'TAG':
                break
            if len(header) != 4:
                raise CopyUnavailable('Truncated MP3 frame')
            bits = int.from_bytes(header, 'big')
            version, layer = (bits >> 19) & 3, (bits >> 17) & 3
            bitrate_index, rate_index = (bits >> 12) & 15, (bits >> 10) & 3
            if bits >> 21 != 0x7ff or version == 1 or layer != 1 or bitrate_index in (0, 15) or rate_index == 3:
                raise CopyUnavailable('Unsupported MP3 frame')
            if version != 3 or not bits & 0x10000:
                raise CopyUnavailable('Copy requires unprotected MPEG1 Layer III')
            rate = (44100, 48000, 32000)[rate_index]
            channels = 1 if (bits >> 6) & 3 == 3 else 2
            samples = 1152
            current = (version, rate, channels, samples)
            if layout is not None and current != layout:
                raise CopyUnavailable('Changing MP3 frame layout')
            layout = current
            bitrate = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320)[bitrate_index]
            length = 144000 * bitrate // rate + ((bits >> 9) & 1)
            side_length = _side_length(channels)
            data = source.read(length - 4)
            if len(data) != length - 4 or length <= 4 + side_length:
                raise CopyUnavailable('Truncated MP3 frame')
            side = data[:side_length]
            reservoir = (side[0] << 1) | (side[1] >> 7)
            frames.append(Frame(offset, length, reservoir, length - 4 - side_length))
            if len(frames) > MAX_FRAMES:
                raise CopyUnavailable('MP3 frame limit exceeded')
        if len(frames) < 3:
            raise CopyUnavailable('Too few MP3 frames')
        tail = source.tell() - 4 if size - offset == 128 and header[:3] == b'TAG' else size
        first = frames[0]
        source.seek(first.offset)
        data = source.read(first.size)
        xing = 4 + _side_length(channels)
        skip = padding = 0
        if data[xing:xing + 4] in (b'Xing', b'Info'):
            if len(data) < xing + 156 or int.from_bytes(data[xing + 4:xing + 8], 'big') != 15:
                raise CopyUnavailable('Unsupported Xing layout')
            if int.from_bytes(data[xing + 8:xing + 12], 'big') != len(frames) - 1:
                raise CopyUnavailable('Xing frame count mismatch')
            if data[xing + 120:xing + 124] not in (b'LAME', b'Lavf', b'Lavc'):
                raise CopyUnavailable('Unknown gapless encoder')
            delay = int.from_bytes(data[xing + 141:xing + 144], 'big')
            skip, padding = (delay >> 12) + 529, (delay & 4095) - 529
            if padding < 0:
                raise CopyUnavailable('Unsupported gapless padding')
        else:
            xing = None
            if b'VBRI' in data[:64]:
                raise CopyUnavailable('Unsupported VBRI layout')
    return Stream(tuple(frames), rate, channels, samples, prefix, tail, xing, skip, padding)


def _crc_table():
    table = []
    for byte in range(256):
        value = byte
        for _ in range(8):
            value = (value >> 1) ^ (0xa001 if value & 1 else 0)
        table.append(value)
    return tuple(table)


CRC_TABLE = _crc_table()


def _crc(data, initial=0):
    value = initial
    for byte in data:
        value = (value >> 8) ^ CRC_TABLE[(value ^ byte) & 255]
    return value


def _payload(source, frame):
    source.seek(frame.offset + frame.size - frame.capacity)
    return source.read(frame.capacity)


def _coded_length(side):
    bits = int.from_bytes(side, 'big')
    start = 18 if len(side) == 17 else 20
    return sum((bits >> (len(side) * 8 - position - 12)) & 4095
               for position in range(start, len(side) * 8, 59))


def _coded_frames(path, stream):
    history = b''
    with open(path, 'rb') as source:
        for frame in stream.frames:
            source.seek(frame.offset + 4)
            side = source.read(_side_length(stream.channels))
            length = _coded_length(side)
            payload = _payload(source, frame)
            if frame.reservoir > len(history):
                raise CopyUnavailable('Invalid MP3 reservoir reference')
            data = (history[-frame.reservoir:] if frame.reservoir else b'') + payload
            if length > len(data) * 8:
                raise CopyUnavailable('Truncated MP3 coded data')
            whole, remainder = divmod(length, 8)
            coded = (data[:whole], data[whole] >> (8 - remainder) if remainder else 0, length)
            yield side, coded
            history = (history + payload)[-511:]


def _reservoir_seed(source, frames, index):
    chunks = []
    remaining = 511
    for i in range(index - 1, -1, -1):
        data = _payload(source, frames[i])
        chunks.append(data[-remaining:])
        remaining -= min(remaining, len(data))
        if not remaining:
            return b''.join(reversed(chunks))
    raise CopyUnavailable('Insufficient original reservoir history')


def _bridge_header(source, frame, rate):
    """Frame header rewritten to 320 kbps, with the frame size that gives."""
    source.seek(frame.offset)
    bits = int.from_bytes(source.read(4), 'big')
    bits = (bits & ~0xf000) | (14 << 12)
    return bits, 144000 * 320 // rate + ((bits >> 9) & 1)


def _bridge(source, frame, seed, rate):
    bits, size = _bridge_header(source, frame, rate)
    source.seek(frame.offset)
    data = source.read(frame.size)
    side_length = frame.size - frame.capacity - 4
    coded_length = _coded_length(data[4:4 + side_length])
    used = max(0, (coded_length + 7) // 8 - frame.reservoir)
    if used > frame.capacity:
        raise CopyUnavailable('Invalid MP3 coded data extent')
    preserved = data[4:4 + side_length + used]
    padding = size - 4 - len(preserved) - len(seed)
    if padding < 0:
        raise CopyUnavailable('Insufficient legal MP3 bridge capacity')
    return bits.to_bytes(4, 'big') + preserved + bytes(padding) + seed


def _silence(source, frame, seed, rate, channels):
    """A silent 320 kbps frame whose payload ends with the reservoir seed."""
    bits, size = _bridge_header(source, frame, rate)
    padding = size - 4 - _side_length(channels) - len(seed)
    if padding < 0:
        raise CopyUnavailable('Insufficient legal MP3 bridge capacity')
    return bits.to_bytes(4, 'big') + bytes(_side_length(channels) + padding) + seed


def _write(source_path, output_path, stream, intervals, filler_path, filler):
    mapping = []
    positions = []
    first = stream.first
    end_cut = bool(intervals) and intervals[-1][1] == len(stream.frames)
    with open(source_path, 'rb') as source, open(output_path, 'wb') as target:
        remaining = stream.prefix
        while remaining:
            data = source.read(min(remaining, 65536))
            if not data:
                raise CopyUnavailable('Truncated MP3 prefix')
            target.write(data)
            remaining -= len(data)
        seeds = {lo: _reservoir_seed(source, stream.frames, hi)
                 for lo, hi in intervals if hi < len(stream.frames)}
        replacements = {}
        for lo, hi in intervals:
            if (filler is None and lo in seeds and lo != first
                    and (stream.frames[hi].reservoir or stream.frames[hi].capacity < 511)):
                replacements[lo - 1] = _bridge(source, stream.frames[lo - 1], seeds[lo], stream.rate)
        total = 0
        audio_crc = 0

        def emit(data, entry, counted=True):
            nonlocal total, audio_crc
            positions.append(total)
            target.write(data)
            total += len(data)
            if counted:
                audio_crc = _crc(data, audio_crc)
            mapping.append(entry)

        index = 0
        cuts = dict(intervals)
        while index < len(stream.frames):
            if index in cuts:
                hi = cuts[index]
                if filler is not None:
                    with open(filler_path, 'rb') as clip:
                        for clip_index, frame in enumerate(filler.frames):
                            if clip_index == len(filler.frames) - 1 and index in seeds:
                                data = _bridge(clip, frame, seeds[index], stream.rate)
                            else:
                                clip.seek(frame.offset)
                                data = clip.read(frame.size)
                            emit(data, ('filler', clip_index))
                elif index == first and index in seeds:
                    # The decoder drops the start skip from the first frame, so silence absorbs it.
                    emit(_silence(source, stream.frames[hi], seeds[index], stream.rate, stream.channels),
                         ('silence', None))
                index = hi
                continue
            frame = stream.frames[index]
            source.seek(frame.offset)
            emit(replacements.get(index) or source.read(frame.size), ('source', index),
                 counted=stream.xing is None or bool(index))
            index += 1
        source.seek(stream.tail)
        shutil.copyfileobj(source, target, 65536)
    if stream.xing is not None:
        head = stream.frames[0]
        with open(output_path, 'r+b') as target:
            target.seek(head.offset)
            data = bytearray(target.read(head.size))
            xing = stream.xing
            data[xing:xing + 4] = b'Xing'
            data[xing + 8:xing + 12] = (len(mapping) - 1).to_bytes(4, 'big')
            data[xing + 12:xing + 16] = total.to_bytes(4, 'big')
            for i in range(100):
                data[xing + 16 + i] = min(255, positions[i * len(mapping) // 100] * 256 // total)
            if end_cut:
                # Nothing follows the cut, so the decoder must trim no end padding.
                delay = int.from_bytes(data[xing + 141:xing + 144], 'big')
                data[xing + 141:xing + 144] = (((delay >> 12) << 12) | 529).to_bytes(3, 'big')
            data[xing + 148:xing + 152] = total.to_bytes(4, 'big')
            data[xing + 152:xing + 154] = audio_crc.to_bytes(2, 'big')
            data[xing + 154:xing + 156] = _crc(data[:xing + 154]).to_bytes(2, 'big')
            target.seek(head.offset)
            target.write(data)
    candidate = _scan(output_path)
    source_bits = iter(enumerate(_coded_frames(source_path, stream)))
    original_index, original = next(source_bits)
    filler_bits = list(_coded_frames(filler_path, filler)) if filler is not None else []
    for (kind, index), actual in zip(mapping, _coded_frames(output_path, candidate), strict=True):
        if kind == 'source':
            while original_index < index:
                original_index, original = next(source_bits)
            expected = original
        elif kind == 'silence':
            expected = (bytes(_side_length(stream.channels)), (b'', 0, 0))
        else:
            expected = filler_bits[index]
        if actual != expected:
            raise CopyUnavailable('MP3 coded payload changed')
    return mapping


def _decode_command(path):
    return ['ffmpeg', *SAFE_MEDIA_INPUT_ARGS, '-v', 'error', '-nostats', '-i', path,
            '-map', '0:a:0', '-c:a', 'pcm_s32le', '-f', 's32le', '-']


def _decode(path, output, timeout):
    command = _decode_command(path)[:-1] + ['-y', '-fs', str(MAX_PCM_BYTES), str(output)]
    result = tracked_run(command, capture_output=True, timeout=timeout)
    if result.returncode or result.stderr:
        raise CopyUnavailable('MP3 decode validation failed')


class _PcmPipe:
    """Forward-only reader over ffmpeg's decoded PCM, read from a pipe instead of temp files."""


    def __init__(self, path, block, timeout):
        self.block = block
        self.pos = 0
        self.timed_out = False
        self.proc = subprocess.Popen(
            _decode_command(path),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        subprocess_registry.register(self.proc)
        self.stderr = b''
        self.drain = threading.Thread(target=self._drain_stderr, daemon=True)
        self.drain.start()
        self.timer = threading.Timer(timeout, self._kill)
        self.timer.daemon = True
        self.timer.start()

    def _drain_stderr(self):
        # Read to EOF so a chatty decoder never blocks on a full pipe; keep the first 64 KB.
        kept = []
        size = 0
        while True:
            data = self.proc.stderr.read(1 << 16)
            if not data:
                break
            if size < 1 << 16:
                kept.append(data[:(1 << 16) - size])
                size += len(kept[-1])
        self.stderr = b''.join(kept)

    def _kill(self):
        self.timed_out = True
        self.proc.kill()

    def _read(self, size):
        chunks = []
        while size:
            data = self.proc.stdout.read(min(size, 1 << 16))
            if not data:
                break
            chunks.append(data)
            size -= len(data)
        return b''.join(chunks)

    def advance(self, sample):
        """Discard decoded audio up to ``sample``; never moves backwards."""
        if sample < self.pos:
            raise CopyUnavailable('MP3 validation spans out of order')
        remaining = (sample - self.pos) * self.block
        while remaining:
            data = self._read(min(remaining, 1 << 20))
            if not data:
                raise CopyUnavailable('MP3 decoded duration mismatch')
            remaining -= len(data)
        self.pos = sample

    def take(self, samples):
        data = self._read(samples * self.block)
        if len(data) != samples * self.block:
            raise CopyUnavailable('MP3 decoded duration mismatch')
        self.pos += samples
        return data

    def finish(self):
        """Drain to EOF and return the decoded length in samples."""
        total = self.pos * self.block
        while True:
            data = self._read(1 << 20)
            if not data:
                break
            total += len(data)
        self.proc.wait()
        self.timer.cancel()
        self.drain.join()
        if self.timed_out or self.proc.returncode or self.stderr:
            raise CopyUnavailable('MP3 decode validation failed')
        return total // self.block

    def close(self):
        self.timer.cancel()
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        subprocess_registry.unregister(self.proc)


def _first_packet(path):
    probe = tracked_run(['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_packets',
                         '-read_intervals', '%+#1', '-of', 'json', path],
                        capture_output=True, timeout=FFPROBE_TIMEOUT)
    if probe.returncode:
        raise CopyUnavailable('MP3 timing probe failed')
    packets = json.loads(probe.stdout).get('packets', [])
    if len(packets) != 1:
        raise CopyUnavailable('MP3 timing unavailable')
    return packets[0]


def _lead_frames(packet, frames, first):
    """Leading frames the demuxer skipped before its first packet, from the packet byte position."""

    try:
        pos = int(packet.get('pos'))
    except (TypeError, ValueError):
        return 0
    for lead in range(LEAD_JUNK_MAX_FRAMES + 1):
        index = first + lead
        if index >= len(frames):
            break
        if frames[index].offset == pos:
            return lead
    raise CopyUnavailable('MP3 decoder start unaccounted for')


class _PcmFile:
    """Seekable reader over a decoded PCM file, with the _PcmPipe advance/take interface."""

    def __init__(self, path, block):
        self.block = block
        self.file = open(path, 'rb')

    def advance(self, sample):
        self.file.seek(sample * self.block)

    def take(self, samples):
        return self.file.read(samples * self.block)

    def close(self):
        self.file.close()


def _compare_pipes(left, right, left_start, right_start, length):
    left.advance(left_start)
    right.advance(right_start)
    remaining = length
    while remaining:
        count = min(remaining, (1 << 16) // left.block)
        if left.take(count) != right.take(count):
            raise CopyUnavailable('MP3 retained samples differ')
        remaining -= count


def _validate(source_path, output_path, stream, mapping, directory, filler_path, filler, output_samples):
    samples = stream.decoded_samples
    filler_samples = len(filler.frames) * filler.samples if filler is not None else 0
    block = stream.channels * 4
    packet = _first_packet(source_path)
    actual_skip = sum(item.get('skip_samples', 0) for item in packet.get('side_data_list', []))
    if actual_skip != stream.skip or float(packet.get('pts_time', 'nan')) != 0.0:
        raise CopyUnavailable('MP3 decoder timing mismatch')
    # Leading frames the demuxer drops as junk are lost from source and copy
    # alike as long as the copy starts with the same frames, which keeps every
    # retained span aligned: model them as part of the decoder skip.
    lead = _lead_frames(packet, stream.frames, stream.first)
    skip_lead = 0
    if lead:
        copied_lead = _lead_frames(_first_packet(output_path), stream.frames, stream.first)
        if copied_lead != lead or not all(mapping[stream.first + i] == ('source', stream.first + i)
                                          for i in range(lead)):
            raise CopyUnavailable('MP3 decoded duration mismatch')
        skip_lead = lead * stream.samples
        samples -= skip_lead
        output_samples -= skip_lead
        logger.info('MP3 stream copy: decoder dropped %d leading frame(s) of source and copy alike', lead)
    timeout = FFMPEG_LONG_TIMEOUT + int(max(samples, output_samples) / stream.rate / 12)
    filler_pcm = Path(directory) / 'filler.pcm'
    if filler is not None:
        _decode(filler_path, filler_pcm, timeout)
        if filler_pcm.stat().st_size != filler_samples * block:
            raise CopyUnavailable('MP3 filler duration mismatch')
    # Two frames cover one-granule MDCT overlap and the synthesis filter history.
    warmup = 2 * stream.samples
    skip, size = stream.skip + skip_lead, stream.samples
    frames = mapping[stream.first:]
    with ExitStack() as stack:
        source = _PcmPipe(source_path, block, timeout)
        stack.callback(source.close)
        output = _PcmPipe(output_path, block, timeout)
        stack.callback(output.close)
        clip = _PcmFile(filler_pcm, block) if filler is not None else None
        if clip is not None:
            stack.callback(clip.close)
        k = 0
        while k < len(frames):
            kind, index = frames[k]
            end = k + 1
            if kind == 'source':
                a = index - stream.first
                while end < len(frames) and frames[end] == ('source', index + end - k):
                    end += 1
                off = max(warmup if k else 0, skip - k * size, skip - a * size)
                source_start = a * size + off - skip
                output_start = k * size + off - skip
                length = min((end - k) * size - off, samples - source_start, output_samples - output_start)
                if length > 0:
                    _compare_pipes(source, output, source_start, output_start, length)
            elif kind == 'filler':
                while end < len(frames) and frames[end][0] == 'filler':
                    end += 1
                off = max(warmup, skip - k * size)
                _compare_pipes(clip, output, off, k * size + off - skip, filler_samples - off)
            k = end
        if source.finish() != samples or output.finish() != output_samples:
            raise CopyUnavailable('MP3 decoded duration mismatch')


def copy_mp3(source_path, output_path, cuts, barriers=(), replacement_path=None, locked=()):
    """Return verified frame-snapped cuts, or None without changing the output."""
    try:
        stream = _scan(source_path)
        first, count = stream.first, stream.count
        boundaries = [max(0, i * stream.samples - stream.skip) for i in range(count + 1)]
        intervals = []
        snapped = []
        previous = 0
        warmup = 2 * stream.samples
        for number, cut in enumerate(cuts):
            start_locked, end_locked = locked[number] if locked else (False, False)
            indices = []
            for edge, edge_locked in (('start', start_locked), ('end', end_locked)):
                sample = cut[edge] * stream.rate
                if edge_locked:
                    indices.append(bisect.bisect_left(boundaries, sample) if edge == 'start'
                                   else bisect.bisect_right(boundaries, sample) - 1)
                    continue
                upper = bisect.bisect_left(boundaries, sample)
                candidates = range(max(0, upper - 1), min(len(boundaries), upper + 1))
                indices.append(min(candidates, key=lambda i: abs(boundaries[i] - sample)))
            lo, hi = indices
            if hi <= lo and (start_locked or end_locked):
                raise CopyUnavailable('Locked MP3 cut shorter than a frame')
            if lo < previous or (lo == previous and intervals) or hi <= lo or hi > count:
                raise CopyUnavailable('Unsupported MP3 edge')
            if lo == 0 and hi == count:
                raise CopyUnavailable('MP3 cut retains nothing')
            if lo > 0 and boundaries[lo] - boundaries[previous] <= warmup:
                raise CopyUnavailable('Short kept MP3 span')
            start = boundaries[lo] / stream.rate
            end = (stream.decoded_samples if hi == count else boundaries[hi]) / stream.rate
            if any(b['start'] < end and b['end'] > start for b in barriers):
                raise CopyUnavailable('Snapped MP3 cut crosses protected audio')
            snapped.append(dict(cut, start=start, end=end, replacement_duration=0.0))
            intervals.append((lo + first, hi + first))
            previous = hi
        has_start = bool(intervals) and intervals[0][0] == first
        has_end = bool(intervals) and intervals[-1][1] == len(stream.frames)
        if not has_end and stream.decoded_samples - boundaries[previous] <= warmup:
            # Tail under decoder warm-up: fold it into the last cut rather than refusing stream copy.
            last_end_locked = bool(locked) and locked[len(intervals) - 1][1]
            if not intervals or previous <= 0 or last_end_locked:
                raise CopyUnavailable('Insufficient final retained audio')
            trailing = stream.decoded_samples - boundaries[previous]
            tail_end = stream.decoded_samples / stream.rate
            if any(b['start'] < tail_end and b['end'] > snapped[-1]['start'] for b in barriers):
                raise CopyUnavailable('Snapped MP3 cut crosses protected audio')
            lo_f, _hi_f = intervals[-1]
            intervals[-1] = (lo_f, len(stream.frames))
            snapped[-1]['end'] = tail_end
            previous = count
            has_end = True
            logger.info('MP3 stream copy: extended final cut to the stream end '
                        '(%d trailing samples below warm-up)', trailing)
        with tempfile.TemporaryDirectory(prefix='mp3-copy-', dir=str(Path(output_path).parent)) as directory:
            filler = None
            filler_path = None
            if replacement_path is not None:
                filler_path = str(Path(directory) / 'filler.mp3')
                duration = get_audio_duration(replacement_path)
                if duration is None:
                    raise CopyUnavailable('Replacement audio duration unavailable')
                fade = max(0, duration - .5)
                result = tracked_run(['ffmpeg', *SAFE_MEDIA_INPUT_ARGS, '-v', 'error', '-y',
                                      '-i', replacement_path, '-vn', '-ar', str(stream.rate),
                                      '-ac', str(stream.channels), '-af',
                                      f'afade=t=in:d=0.5,afade=t=out:st={fade}:d=0.5,volume=0.4',
                                      '-c:a', 'libmp3lame', '-b:a', '128k', '-reservoir', '0',
                                      '-write_xing', '0', '-id3v2_version', '0', filler_path],
                                     capture_output=True, timeout=FFMPEG_LONG_TIMEOUT)
                if result.returncode:
                    raise CopyUnavailable('MP3 replacement encoding failed')
                filler = _scan(filler_path)
                if filler.channels != stream.channels or filler.rate != stream.rate or len(filler.frames) <= 2:
                    raise CopyUnavailable('MP3 replacement layout mismatch')
                if any(frame.reservoir for frame in filler.frames):
                    raise CopyUnavailable('MP3 replacement reservoir is enabled')
                filler_duration = len(filler.frames) * filler.samples / stream.rate
                snapped = [dict(cut, replacement_duration=filler_duration) for cut in snapped]
            filler_samples = len(filler.frames) * filler.samples if filler is not None else 0
            bridge_samples = filler_samples or stream.samples
            if has_start:
                # The decoder drops the start skip from the first output frame.
                if stream.skip >= bridge_samples:
                    raise CopyUnavailable('MP3 start skip exceeds the bridge')
                snapped[0]['replacement_duration'] = (bridge_samples - stream.skip) / stream.rate
            kept = count - sum(hi - lo for lo, hi in intervals)
            bridges = int(has_start and filler is None)
            output_samples = ((kept + bridges) * stream.samples + len(cuts) * filler_samples
                              - stream.skip - (0 if has_end else stream.padding))
            block = stream.channels * 4
            if stream.decoded_samples <= 0:
                raise CopyUnavailable('MP3 decoded length unavailable')
            if max(stream.decoded_samples, output_samples) * block > MAX_PCM_BYTES:
                raise CopyUnavailable('PCM validation size limit exceeded')
            # Disk needed: the filler PCM plus the staged mp3 (validation streams the rest).
            required = filler_samples * block + os.path.getsize(source_path)
            if shutil.disk_usage(directory).free < required:
                raise CopyUnavailable('Insufficient disk space for PCM validation')
            staged = str(Path(directory) / 'candidate.mp3')
            mapping = _write(source_path, staged, stream, intervals, filler_path, filler)
            _validate(source_path, staged, stream, mapping, directory, filler_path, filler, output_samples)
            os.replace(staged, output_path)
        return snapped
    except (CopyUnavailable, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        logger.info('MP3 stream copy unavailable: %s', error)
