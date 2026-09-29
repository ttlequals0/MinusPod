"""remove_ads renders a filler whose format differs from the episode (#796).

Requires ffmpeg/ffprobe on PATH. Skipped if unavailable.
"""
import json
import shutil
import subprocess

import pytest

from audio_processor import AudioProcessor

pytestmark = pytest.mark.skipif(
    shutil.which('ffmpeg') is None or shutil.which('ffprobe') is None,
    reason='ffmpeg/ffprobe required',
)

_DURATION_TOL_S = 1.0


def _tone(path, duration, rate, channels):
    subprocess.run(
        ['ffmpeg', '-y', '-f', 'lavfi', '-i',
         f'sine=frequency=440:duration={duration}:sample_rate={rate}',
         '-ar', str(rate), '-ac', str(channels), str(path)],
        check=True, capture_output=True,
    )


def _format(path):
    out = subprocess.run(
        ['ffprobe', '-v', 'error', '-select_streams', 'a:0',
         '-show_entries', 'stream=sample_rate,channels', '-of', 'json',
         str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    stream = json.loads(out)['streams'][0]
    return int(stream['sample_rate']), int(stream['channels'])


@pytest.mark.parametrize('rate,channels', [(44100, 2), (22050, 1)])
def test_mismatched_filler_renders_at_episode_format(tmp_path, rate, channels):
    filler = tmp_path / 'filler.mp3'
    _tone(filler, 1.08, 48000, 2)
    episode = tmp_path / 'episode.wav'
    _tone(episode, 90.0, rate, channels)
    output = tmp_path / 'out.mp3'

    processor = AudioProcessor(replace_audio_path=str(filler))
    applied = processor.remove_ads(
        str(episode),
        [{'start': 10.0, 'end': 25.0, 'confidence': 0.9},
         {'start': 70.0, 'end': 85.0, 'confidence': 0.9}],
        str(output),
    )
    assert applied is not None and len(applied) == 2
    # The second cut leaves <30s, so it runs to end of episode.
    assert applied[-1]['end'] == pytest.approx(90.0, abs=0.1)
    assert _format(output) == (rate, channels)

    in_dur = processor.get_audio_duration(str(episode))
    out_dur = processor.get_audio_duration(str(output))
    cut_total = sum(a['end'] - a['start'] for a in applied)
    expected = in_dur - cut_total + len(applied) * processor.get_beep_duration()
    assert abs(out_dur - expected) <= _DURATION_TOL_S
