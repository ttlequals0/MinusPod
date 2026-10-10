"""MP3 frame copying verified against the retained source's decoded samples."""

import bisect
import json
import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from config import FFMPEG_LONG_TIMEOUT, FFPROBE_TIMEOUT
from utils.ffmpeg_run import SAFE_MEDIA_INPUT_ARGS
from utils.subprocess_registry import tracked_run

logger = logging.getLogger(__name__)
MAX_FRAMES = 1_000_000
MAX_PCM_BYTES = 4 * 1024 * 1024 * 1024


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
            side_length = 17 if channels == 1 else 32
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
        xing = 4 + (17 if channels == 1 else 32)
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
            side = source.read(17 if stream.channels == 1 else 32)
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


def _bridge(source, frame, seed, rate):
    source.seek(frame.offset)
    data = source.read(frame.size)
    bits = int.from_bytes(data[:4], 'big')
    bits = (bits & ~0xf000) | (14 << 12)
    size = 144000 * 320 // rate + ((bits >> 9) & 1)
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


def _write(source_path, output_path, stream, intervals, filler_path, filler):
    mapping = []
    with open(source_path, 'rb') as source, open(output_path, 'wb') as target:
        remaining = stream.prefix
        while remaining:
            data = source.read(min(remaining, 65536))
            if not data:
                raise CopyUnavailable('Truncated MP3 prefix')
            target.write(data)
            remaining -= len(data)
        replacements = {}
        for lo, hi in intervals:
            seed = _reservoir_seed(source, stream.frames, hi)
            if filler is None and (stream.frames[hi].reservoir or stream.frames[hi].capacity < 511):
                replacements[lo - 1] = _bridge(source, stream.frames[lo - 1], seed, stream.rate)
        index = 0
        total = 0
        positions = []
        audio_crc = 0
        cuts = dict(intervals)
        while index < len(stream.frames):
            if index in cuts:
                hi = cuts[index]
                if filler is not None:
                    seed = _reservoir_seed(source, stream.frames, hi)
                    with open(filler_path, 'rb') as clip:
                        for clip_index, frame in enumerate(filler.frames):
                            if clip_index == len(filler.frames) - 1:
                                data = _bridge(clip, frame, seed, stream.rate)
                            else:
                                clip.seek(frame.offset)
                                data = clip.read(frame.size)
                            positions.append(total)
                            target.write(data)
                            total += len(data)
                            audio_crc = _crc(data, audio_crc)
                            mapping.append(('filler', clip_index))
                index = hi
                continue
            frame = stream.frames[index]
            source.seek(frame.offset)
            data = replacements.get(index) or source.read(frame.size)
            positions.append(total)
            target.write(data)
            total += len(data)
            if stream.xing is None or index:
                audio_crc = _crc(data, audio_crc)
            mapping.append(('source', index))
            index += 1
        source.seek(stream.tail)
        shutil.copyfileobj(source, target, 65536)
    if stream.xing is not None:
        first = stream.frames[0]
        with open(output_path, 'r+b') as target:
            target.seek(first.offset)
            data = bytearray(target.read(first.size))
            xing = stream.xing
            data[xing:xing + 4] = b'Xing'
            data[xing + 8:xing + 12] = (len(mapping) - 1).to_bytes(4, 'big')
            data[xing + 12:xing + 16] = total.to_bytes(4, 'big')
            for i in range(100):
                data[xing + 16 + i] = min(255, positions[i * len(mapping) // 100] * 256 // total)
            data[xing + 148:xing + 152] = total.to_bytes(4, 'big')
            data[xing + 152:xing + 154] = audio_crc.to_bytes(2, 'big')
            data[xing + 154:xing + 156] = _crc(data[:xing + 154]).to_bytes(2, 'big')
            target.seek(first.offset)
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
        else:
            expected = filler_bits[index]
        if actual != expected:
            raise CopyUnavailable('MP3 coded payload changed')


def _decode(path, output, timeout):
    result = tracked_run(['ffmpeg', *SAFE_MEDIA_INPUT_ARGS, '-v', 'error', '-y', '-i', path,
                          '-map', '0:a:0', '-c:a', 'pcm_s32le', '-f', 's32le',
                          '-fs', str(MAX_PCM_BYTES), str(output)],
                         capture_output=True, timeout=timeout)
    if result.returncode or result.stderr:
        raise CopyUnavailable('MP3 decode validation failed')


def _compare(source, output, start, length, block):
    source.seek(start * block)
    remaining = length * block
    while remaining:
        data = source.read(min(65536, remaining))
        if not data or output.read(len(data)) != data:
            raise CopyUnavailable('MP3 retained samples differ')
        remaining -= len(data)


def _validate(source_path, output_path, stream, cuts, directory, filler_path, filler):
    first = int(stream.xing is not None)
    samples = (len(stream.frames) - first) * stream.samples - stream.skip - stream.padding
    filler_samples = len(filler.frames) * filler.samples if filler is not None else 0
    removed = sum(hi - lo for lo, hi in cuts)
    output_samples = samples - removed + len(cuts) * filler_samples
    block = stream.channels * 4
    required = (samples + output_samples + filler_samples) * block
    if samples <= 0 or max(samples, output_samples) * block > MAX_PCM_BYTES:
        raise CopyUnavailable('PCM validation size limit exceeded')
    if shutil.disk_usage(directory).free < required + os.path.getsize(source_path):
        raise CopyUnavailable('Insufficient disk space for PCM validation')
    probe = tracked_run(['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_packets',
                         '-read_intervals', '%+#1', '-of', 'json', source_path],
                        capture_output=True, timeout=FFPROBE_TIMEOUT)
    if probe.returncode:
        raise CopyUnavailable('MP3 timing probe failed')
    packets = json.loads(probe.stdout).get('packets', [])
    if len(packets) != 1:
        raise CopyUnavailable('MP3 timing unavailable')
    actual_skip = sum(item.get('skip_samples', 0) for item in packets[0].get('side_data_list', []))
    if actual_skip != stream.skip or float(packets[0].get('pts_time', 'nan')) != 0.0:
        raise CopyUnavailable('MP3 decoder timing mismatch')
    source_pcm, output_pcm, filler_pcm = [Path(directory) / name for name in ('source.pcm', 'output.pcm', 'filler.pcm')]
    timeout = FFMPEG_LONG_TIMEOUT + int(max(samples, output_samples) / stream.rate / 12)
    _decode(source_path, source_pcm, timeout)
    _decode(output_path, output_pcm, timeout)
    if source_pcm.stat().st_size != samples * block or output_pcm.stat().st_size != output_samples * block:
        raise CopyUnavailable('MP3 decoded duration mismatch')
    if filler is not None:
        _decode(filler_path, filler_pcm, timeout)
        if filler_pcm.stat().st_size != filler_samples * block:
            raise CopyUnavailable('MP3 filler duration mismatch')
    # Two frames cover one-granule MDCT overlap and the synthesis filter history.
    warmup = 2 * stream.samples
    with source_pcm.open('rb') as source, output_pcm.open('rb') as output:
        cursor = 0
        for lo, hi in cuts:
            _compare(source, output, cursor, lo - cursor, block)
            if filler is not None:
                output.seek(warmup * block, 1)
                with filler_pcm.open('rb') as clip:
                    _compare(clip, output, warmup, filler_samples - warmup, block)
            output.seek(warmup * block, 1)
            cursor = hi + warmup
        _compare(source, output, cursor, samples - cursor, block)


def copy_mp3(source_path, output_path, cuts, barriers=(), replacement_path=None):
    """Return verified frame-snapped cuts, or None without changing the output."""
    try:
        stream = _scan(source_path)
        first = int(stream.xing is not None)
        count = len(stream.frames) - first
        boundaries = [max(0, i * stream.samples - stream.skip) for i in range(count + 1)]
        intervals = []
        snapped = []
        previous = 0
        warmup = 2 * stream.samples
        for cut in cuts:
            indices = []
            for edge in ('start', 'end'):
                sample = cut[edge] * stream.rate
                upper = bisect.bisect_left(boundaries, sample)
                candidates = range(max(0, upper - 1), min(len(boundaries), upper + 1))
                indices.append(min(candidates, key=lambda i: abs(boundaries[i] - sample)))
            lo, hi = indices
            if lo <= previous or hi <= lo or hi >= count or boundaries[lo] - boundaries[previous] <= warmup:
                raise CopyUnavailable('Unsupported MP3 edge or short kept span')
            start, end = boundaries[lo] / stream.rate, boundaries[hi] / stream.rate
            if any(b['start'] < end and b['end'] > start for b in barriers):
                raise CopyUnavailable('Snapped MP3 cut crosses protected audio')
            snapped.append(dict(cut, start=start, end=end, replacement_duration=0.0))
            intervals.append((lo + first, hi + first))
            previous = hi
        decoded_samples = count * stream.samples - stream.skip - stream.padding
        if decoded_samples - boundaries[previous] <= warmup:
            raise CopyUnavailable('Insufficient final retained audio')
        with tempfile.TemporaryDirectory(prefix='mp3-copy-', dir=str(Path(output_path).parent)) as directory:
            filler = None
            filler_path = None
            if replacement_path is not None:
                filler_path = str(Path(directory) / 'filler.mp3')
                probe = tracked_run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                     '-of', 'json', replacement_path], capture_output=True, timeout=FFPROBE_TIMEOUT)
                duration = float(json.loads(probe.stdout)['format']['duration'])
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
            output_samples = decoded_samples - sum((hi - lo) * stream.samples for lo, hi in intervals) + len(cuts) * filler_samples
            block = stream.channels * 4
            if max(decoded_samples, output_samples) * block > MAX_PCM_BYTES:
                raise CopyUnavailable('PCM validation size limit exceeded')
            required = (decoded_samples + output_samples + filler_samples) * block + os.path.getsize(source_path)
            if shutil.disk_usage(directory).free < required:
                raise CopyUnavailable('Insufficient disk space for PCM validation')
            staged = str(Path(directory) / 'candidate.mp3')
            _write(source_path, staged, stream, intervals, filler_path, filler)
            pcm_cuts = [(boundaries[lo - first], boundaries[hi - first]) for lo, hi in intervals]
            _validate(source_path, staged, stream, pcm_cuts, directory, filler_path, filler)
            os.replace(staged, output_path)
        return snapped
    except (CopyUnavailable, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        logger.info('MP3 stream copy unavailable: %s', error)
        return None
