"""Exact MP3 duration when ffprobe only estimates it from the first frame (issue #860)."""
import json
import os
import shutil
import subprocess
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from utils.audio import _ESTIMATED_DURATION, AudioMetadata, get_audio_duration, probe_render_input

ESTIMATE_LINE = 'Estimating duration from bitrate, this may be inaccurate\n'
PROGRESS = 'out_time_us=1328431020\nprogress=end\n'


def _proc(cmd, returncode=0, stdout='', stderr=''):
    return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)


def _runner(calls, probe, ffmpeg_returncode=0):
    def run(cmd, *args, **kwargs):
        calls.append(cmd)
        if cmd[0] == 'ffprobe':
            return probe(cmd)
        return _proc(cmd, 0, PROGRESS) if ffmpeg_returncode == 0 else _proc(cmd, 1, '', 'boom')
    return run


def _text_probe(stderr):
    return lambda cmd: _proc(cmd, stdout='1326.983856\n', stderr=stderr)


def test_estimated_duration_is_replaced_by_decoded_length():
    calls = []
    with patch('utils.audio.tracked_run', side_effect=_runner(calls, _text_probe(ESTIMATE_LINE))):
        assert get_audio_duration('/x.mp3') == pytest.approx(1328.431020)
    ffmpeg = [c for c in calls if c[0] == 'ffmpeg']
    assert len(ffmpeg) == 1
    assert '-f' in ffmpeg[0] and 'null' in ffmpeg[0] and '-progress' in ffmpeg[0]


def test_header_duration_is_kept_without_the_warning():
    calls = []
    with patch('utils.audio.tracked_run', side_effect=_runner(calls, _text_probe(''))):
        assert get_audio_duration('/x.mp3') == pytest.approx(1326.983856)
    assert all(c[0] == 'ffprobe' for c in calls)


def test_failed_decode_falls_back_to_probed_duration():
    calls = []
    with patch('utils.audio.tracked_run', side_effect=_runner(calls, _text_probe(ESTIMATE_LINE), ffmpeg_returncode=1)):
        assert get_audio_duration('/x.mp3') == pytest.approx(1326.983856)


def test_probe_render_input_uses_decoded_length():
    info = {
        'format': {'duration': '1326.983856'},
        'streams': [{'sample_rate': '44100', 'channels': 2, 'channel_layout': 'stereo'}],
        'chapters': [],
    }

    def probe(cmd):
        return subprocess.CompletedProcess(cmd, 0, json.dumps(info).encode(), ESTIMATE_LINE.encode())

    calls = []
    with patch('utils.audio.tracked_run', side_effect=_runner(calls, probe)):
        result = probe_render_input('/x.mp3')
    assert result.duration == pytest.approx(1328.431020)
    assert result.audio_format == (44100, 2, 'stereo')


def test_decode_runs_once_per_unchanged_file(tmp_path):
    audio = tmp_path / 'a.mp3'
    audio.write_bytes(b'x')
    AudioMetadata.invalidate(str(audio))
    calls = []
    with patch('utils.audio.tracked_run', side_effect=_runner(calls, _text_probe(ESTIMATE_LINE))):
        assert get_audio_duration(str(audio)) == pytest.approx(1328.431020)
        assert get_audio_duration(str(audio)) == pytest.approx(1328.431020)
    assert len([c for c in calls if c[0] == 'ffmpeg']) == 1


def test_failed_decode_is_not_retried_for_an_unchanged_file(tmp_path):
    audio = tmp_path / 'b.mp3'
    audio.write_bytes(b'x')
    AudioMetadata.invalidate(str(audio))
    calls = []
    with patch('utils.audio.tracked_run', side_effect=_runner(calls, _text_probe(ESTIMATE_LINE), ffmpeg_returncode=1)):
        assert get_audio_duration(str(audio)) == pytest.approx(1326.983856)
        assert get_audio_duration(str(audio)) == pytest.approx(1326.983856)
    assert len([c for c in calls if c[0] == 'ffmpeg']) == 1
    assert len([c for c in calls if c[0] == 'ffprobe']) == 1


@pytest.mark.skipif(not shutil.which('ffmpeg'), reason='ffmpeg is unavailable')
def test_installed_ffprobe_still_warns_for_a_headerless_mp3(tmp_path):
    audio = tmp_path / 'headerless.mp3'
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3',
                    '-c:a', 'libmp3lame', '-q:a', '9', '-write_xing', '0', str(audio)], check=True)
    probe = subprocess.run(['ffprobe', '-v', 'warning', '-show_entries', 'format=duration', str(audio)],
                           capture_output=True, text=True, check=True)
    assert _ESTIMATED_DURATION in probe.stderr
    assert get_audio_duration(str(audio)) == pytest.approx(3.0, abs=0.1)
