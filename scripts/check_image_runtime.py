import argparse
import ctypes
import importlib
import json
import locale
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
import socket
from pathlib import Path


def run(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout


parser = argparse.ArgumentParser()
parser.add_argument('--expected-families', nargs='+', default=[
    'media-security', 'core-security', 'misc-security', 'aux-security', 'graphics-security',
])
options = parser.parse_args()
assert os.getuid() == 1000
provenance = {}
for record in sorted(Path('/usr/share/minuspod').glob('*-security/built-packages.json')):
    run('/opt/venv/bin/python', str(record.parent / 'build_media_security_packages.py'), '--verify-installed')
    provenance[record.parent.name] = json.loads(record.read_text())
assert set(provenance) == set(options.expected_families), sorted(provenance)
modules = ['torch', 'ctranslate2', 'faster_whisper', 'av', 'numpy', 'scipy', 'onnxruntime']
versions = {}
for name in modules:
    module = importlib.import_module(name)
    versions[name] = getattr(module, '__version__', 'imported')
for library in ['libc.so.6', 'libp11-kit.so.0', 'libXrender.so.1', 'libpcre2-8.so.0', 'libglib-2.0.so.0', 'libgobject-2.0.so.0', 'libgio-2.0.so.0', 'libcjson.so.1', 'libcjson_utils.so.1', 'libcairo.so.2', 'libcairo-gobject.so.2', 'libx264.so.165', 'libavcodec.so.62', 'libavformat.so.62', 'libavfilter.so.11', 'libavdevice.so.62', 'libavutil.so.60', 'libswresample.so.6', 'libswscale.so.9', 'libass.so.9', 'libsrt-gnutls.so.1.5', 'libsndfile.so.1']:
    ctypes.CDLL(library)
for binary in ['/usr/bin/ffmpeg', '/usr/bin/ffprobe', '/usr/bin/fpcalc']:
    assert 'not found' not in run('ldd', binary), binary
pcre = ctypes.CDLL('libpcre2-8.so.0')
pcre.pcre2_config_8.argtypes = [ctypes.c_uint, ctypes.c_void_p]
pcre.pcre2_compile_8.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_uint, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p]
pcre.pcre2_compile_8.restype = ctypes.c_void_p
pcre.pcre2_jit_compile_8.argtypes = [ctypes.c_void_p, ctypes.c_uint]
pcre.pcre2_match_data_create_from_pattern_8.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
pcre.pcre2_match_data_create_from_pattern_8.restype = ctypes.c_void_p
pcre.pcre2_jit_match_8.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]
pcre.pcre2_match_data_free_8.argtypes = [ctypes.c_void_p]
pcre.pcre2_code_free_8.argtypes = [ctypes.c_void_p]
jit_available = ctypes.c_uint()
assert pcre.pcre2_config_8(1, ctypes.byref(jit_available)) == 0 and jit_available.value == 1
pattern = rb'^(feed|episode)-[0-9]+$'
error, offset = ctypes.c_int(), ctypes.c_size_t()
code = pcre.pcre2_compile_8(pattern, len(pattern), 0, ctypes.byref(error), ctypes.byref(offset), None)
assert code and pcre.pcre2_jit_compile_8(code, 1) == 0
match = pcre.pcre2_match_data_create_from_pattern_8(code, None)
assert match
try:
    assert pcre.pcre2_jit_match_8(code, b'feed-42', 7, 0, 0, match, None) > 0
finally:
    pcre.pcre2_match_data_free_8(match)
    pcre.pcre2_code_free_8(code)
assert socket.getaddrinfo('localhost', 8000)
with ThreadPoolExecutor(max_workers=4) as pool:
    assert list(pool.map(lambda value: value * value, range(16))) == [value * value for value in range(16)]
assert run('/bin/sh', '-c', 'printf subprocess') == 'subprocess'
assert subprocess.check_output(['grep', '-P', r'^(feed|episode)-[0-9]+$'], input='feed-42\nother\n', text=True) == 'feed-42\n'
locale.setlocale(locale.LC_ALL, 'C.UTF-8')
with tempfile.TemporaryDirectory() as directory:
    directory = Path(directory)
    text = directory / 'utf8.txt'
    text.write_text('ordinary UTF-8 text', encoding='utf-8')
    libc = ctypes.CDLL('libc.so.6')
    libc.fopen.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    libc.fopen.restype = ctypes.c_void_p
    libc.fgetwc.argtypes = [ctypes.c_void_p]
    libc.fgetwc.restype = ctypes.c_uint
    libc.fclose.argtypes = [ctypes.c_void_p]
    text_handle = libc.fopen(str(text).encode(), b'r,ccs=UTF-8')
    assert text_handle and libc.fgetwc(text_handle) == ord('o')
    assert libc.fclose(text_handle) == 0
    cairo = ctypes.CDLL('libcairo.so.2')
    cairo.cairo_image_surface_create.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
    cairo.cairo_image_surface_create.restype = ctypes.c_void_p
    cairo.cairo_create.argtypes = [ctypes.c_void_p]
    cairo.cairo_create.restype = ctypes.c_void_p
    cairo.cairo_set_source_rgb.argtypes = [ctypes.c_void_p, ctypes.c_double, ctypes.c_double, ctypes.c_double]
    cairo.cairo_paint.argtypes = [ctypes.c_void_p]
    cairo.cairo_surface_write_to_png.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
    cairo.cairo_destroy.argtypes = [ctypes.c_void_p]
    cairo.cairo_surface_destroy.argtypes = [ctypes.c_void_p]
    surface = cairo.cairo_image_surface_create(0, 128, 96)
    context = cairo.cairo_create(surface)
    try:
        cairo.cairo_set_source_rgb(context, 0.2, 0.4, 0.6)
        cairo.cairo_paint(context)
        assert cairo.cairo_surface_write_to_png(surface, str(directory / 'render.png').encode()) == 0
    finally:
        cairo.cairo_destroy(context)
        cairo.cairo_surface_destroy(surface)
    video = directory / 'video.mkv'
    run('ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc=size=128x96:rate=10:duration=1', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(video))
    run('ffmpeg', '-v', 'error', '-i', str(video), '-f', 'null', '-')
    source = directory / 'source.wav'
    run('ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=12', '-c:a', 'pcm_s16le', str(source))
    for codec, suffix in [('libmp3lame', 'mp3'), ('aac', 'm4a'), ('libopus', 'ogg'), ('flac', 'flac'), ('pcm_s16le', 'wav')]:
        encoded = directory / ('encoded.' + suffix)
        copied = directory / ('copied.' + suffix)
        run('ffmpeg', '-v', 'error', '-i', str(source), '-c:a', codec, '-metadata', 'title=Media regression', str(encoded))
        run('ffmpeg', '-v', 'error', '-i', str(encoded), '-c:a', 'copy', '-map_metadata', '0', str(copied))
        run('ffmpeg', '-v', 'error', '-i', str(copied), '-f', 'null', '-')
        info = json.loads(run('ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(copied)))
        assert 11.9 <= float(info['format']['duration']) <= 12.1, suffix
        tags = info['format'].get('tags', {}) | info['streams'][0].get('tags', {})
        assert tags.get('title', tags.get('TITLE')) == 'Media regression', suffix
    fingerprint = json.loads(run('fpcalc', '-json', str(source)))
    assert fingerprint['fingerprint']
    class AudioInfo(ctypes.Structure):
        _fields_ = [('frames', ctypes.c_longlong), ('samplerate', ctypes.c_int), ('channels', ctypes.c_int), ('format', ctypes.c_int), ('sections', ctypes.c_int), ('seekable', ctypes.c_int)]

    sndfile = ctypes.CDLL('libsndfile.so.1')
    sndfile.sf_open.argtypes = [ctypes.c_char_p, ctypes.c_int, ctypes.POINTER(AudioInfo)]
    sndfile.sf_open.restype = ctypes.c_void_p
    sndfile.sf_readf_short.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_short), ctypes.c_longlong]
    sndfile.sf_readf_short.restype = ctypes.c_longlong
    sndfile.sf_close.argtypes = [ctypes.c_void_p]
    info = AudioInfo()
    handle = sndfile.sf_open(str(source).encode(), 0x10, ctypes.byref(info))
    assert handle and info.frames == 12 * 48000 and info.samplerate == 48000
    samples = (ctypes.c_short * 48000)()
    assert sndfile.sf_readf_short(handle, samples, 48000) == 48000
    assert sndfile.sf_close(handle) == 0
print(json.dumps({'uid': os.getuid(), 'imports': versions, 'audio_formats': ['MP3', 'AAC', 'Opus', 'FLAC', 'PCM'], 'stream_copy': 'passed', 'metadata': 'passed', 'fpcalc': 'passed', 'shared_libraries': 'passed', 'installed_provenance': {name: len(records) for name, records in provenance.items()}, 'nss_threads_subprocess': 'passed', 'glibc_utf8_conversion': 'passed', 'pcre2_native_jit': 'passed', 'h264_encode_decode': 'passed', 'cairo_render': 'passed'}, indent=2))
