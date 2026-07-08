"""Cut-renderer duration invariant on synthetic audio (spec 1.5).

remove_ads REPLACES each applied cut with the beep asset (1.08s), so the
exact invariant is:

    output_duration == input_duration - sum(applied cuts) + n_cuts * beep

within 1.0s. The naive `input - sum(cuts)` form is short one beep per cut
by design; that beep term is also why per-episode "timeSaved" reads ~1.1s
less than marker arithmetic per cut.

The synthetic 'ad' regions are digital silence baked into a sine tone, so:
  - any leftover ad audio in the output shows up as a >=2s silence
    (silencedetect probe), and
  - the beep slice at the cut point must be loud (volumedetect mean above
    -60 dB; the raw silent region measures ~-91 dB).

Requires ffmpeg/ffprobe on PATH. Skipped if unavailable.
"""
import re
import shutil
import subprocess

import pytest

from audio_processor import AudioProcessor

pytestmark = pytest.mark.skipif(
    shutil.which('ffmpeg') is None or shutil.which('ffprobe') is None,
    reason='ffmpeg/ffprobe required',
)

_DURATION_TOL_S = 1.0


@pytest.fixture
def processor():
    return AudioProcessor()


def _make_input(path, duration, silent_ranges):
    """Sine tone with digital-silence 'ad' regions baked in."""
    expr = '0.8*sin(440*2*PI*t)'
    for start, end in silent_ranges:
        expr = f'if(between(t,{start},{end}),0,{expr})'
    subprocess.run(
        ['ffmpeg', '-y', '-f', 'lavfi', '-i',
         f"aevalsrc='{expr}':d={duration}",
         '-ar', '44100', '-ac', '1', str(path)],
        check=True, capture_output=True,
    )


def _mean_volume_db(path, start, dur):
    out = subprocess.run(
        ['ffmpeg', '-ss', str(start), '-t', str(dur), '-i', str(path),
         '-af', 'volumedetect', '-f', 'null', '-'],
        capture_output=True, text=True,
    ).stderr
    m = re.search(r'mean_volume: (-?[\d.]+) dB', out)
    assert m, f'volumedetect produced no mean_volume: {out[-500:]}'
    return float(m.group(1))


def _silence_starts(path, noise_db=-45, min_dur=2.0):
    out = subprocess.run(
        ['ffmpeg', '-i', str(path),
         '-af', f'silencedetect=noise={noise_db}dB:d={min_dur}',
         '-f', 'null', '-'],
        capture_output=True, text=True,
    ).stderr
    return re.findall(r'silence_start: ([\d.]+)', out)


def _assert_invariant(processor, input_path, output_path, applied):
    in_dur = processor.get_audio_duration(str(input_path))
    out_dur = processor.get_audio_duration(str(output_path))
    cut_total = sum(a['end'] - a['start'] for a in applied)
    expected = in_dur - cut_total + len(applied) * processor.get_beep_duration()
    assert abs(out_dur - expected) <= _DURATION_TOL_S, (
        f'render drift {out_dur - expected:+.2f}s: in={in_dur:.2f}s '
        f'out={out_dur:.2f}s cuts={cut_total:.2f}s beeps={len(applied)}')


def test_mid_episode_cut_duration_and_beep(processor, tmp_path):
    input_path = tmp_path / 'input.wav'
    output_path = tmp_path / 'out.mp3'
    _make_input(input_path, 60.0, [(10.0, 30.0)])

    applied = processor.remove_ads(
        str(input_path),
        [{'start': 10.0, 'end': 30.0, 'confidence': 0.9}],
        str(output_path),
    )
    assert applied == [{'start': 10.0, 'end': 30.0, 'confidence': 0.9}]
    _assert_invariant(processor, input_path, output_path, applied)
    # The cut point carries the beep, not the (silent) ad audio.
    assert _mean_volume_db(output_path, 10.1, 0.8) > -60.0
    # No 2s+ silence anywhere: leftover ad audio would be silent.
    assert _silence_starts(output_path) == []


def test_end_of_episode_cut_extends_and_holds_invariant(processor, tmp_path):
    input_path = tmp_path / 'input.wav'
    output_path = tmp_path / 'out.mp3'
    _make_input(input_path, 60.0, [(40.0, 55.0)])

    applied = processor.remove_ads(
        str(input_path),
        [{'start': 40.0, 'end': 55.0, 'confidence': 0.9}],
        str(output_path),
    )
    # <30s would remain (POST_ROLL_TRIM_THRESHOLD), so the cut runs to EOF.
    assert applied[-1]['end'] == pytest.approx(60.0, abs=0.1)
    _assert_invariant(processor, input_path, output_path, applied)
    assert _silence_starts(output_path) == []


def test_two_cuts_scale_beep_count(processor, tmp_path):
    input_path = tmp_path / 'input.wav'
    output_path = tmp_path / 'out.mp3'
    _make_input(input_path, 120.0, [(10.0, 30.0), (60.0, 75.0)])

    applied = processor.remove_ads(
        str(input_path),
        [{'start': 10.0, 'end': 30.0, 'confidence': 0.9},
         {'start': 60.0, 'end': 75.0, 'confidence': 0.9}],
        str(output_path),
    )
    assert len(applied) == 2
    _assert_invariant(processor, input_path, output_path, applied)
    assert _silence_starts(output_path) == []
