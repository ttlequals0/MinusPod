"""A span shorter than fpcalc's 3 s floor still fingerprints and lines up with the full-file fingerprint."""
import shutil
import subprocess

import pytest

from audio_analysis.cue_candidates import mark_dismissed_candidates
from audio_fingerprinter import AudioFingerprinter, FINGERPRINT_NOMINAL_FPS
from config import AUDIO_CUE_RECURRENCE_SIMILARITY

pytestmark = pytest.mark.skipif(
    not (shutil.which('ffmpeg') and shutil.which('fpcalc')),
    reason='ffmpeg and fpcalc required')

BURST_START_S = 12.0
BURST_LEN_S = 1.5


@pytest.fixture(scope='module')
def audio(tmp_path_factory):
    path = str(tmp_path_factory.mktemp('shortspan') / 'ep.wav')
    # 30 s of noise with a distinctive two-tone chirp burst at 12.0 s
    subprocess.run([
        'ffmpeg', '-y', '-loglevel', 'error',
        '-f', 'lavfi', '-i', 'anoisesrc=d=30:c=pink:r=16000:seed=7:a=0.2',
        '-f', 'lavfi', '-i', f'aevalsrc=0.6*sin(2*PI*(500*t+900*t*t)):d={BURST_LEN_S}:s=16000',
        '-filter_complex', f'[1:a]adelay={int(BURST_START_S * 1000)}:all=1[b];[0:a][b]amix=inputs=2:duration=first:normalize=0',
        '-ac', '1', '-ar', '16000', path], check=True)
    return path


@pytest.fixture
def fp():
    f = AudioFingerprinter()
    if not f.is_available():
        pytest.skip('fpcalc unavailable')
    return f


def test_short_span_returns_ints(audio, fp):
    result = fp.generate_raw_span_fingerprint(audio, BURST_START_S, BURST_START_S + BURST_LEN_S)
    assert result is not None
    ints, dur = result
    assert len(ints) >= 4
    assert dur == pytest.approx(len(ints) / FINGERPRINT_NOMINAL_FPS)
    assert dur == pytest.approx(BURST_LEN_S, abs=0.3)


def test_short_span_found_in_full_file_fingerprint(audio, fp):
    ints, _ = fp.generate_raw_span_fingerprint(audio, BURST_START_S, BURST_START_S + BURST_LEN_S)
    full = fp._generate_full_fingerprint(audio)
    cands = [{'start': BURST_START_S, 'end': BURST_START_S + BURST_LEN_S},
             {'start': 22.0, 'end': 23.5}]
    marked = mark_dismissed_candidates(
        cands, [{'id': 1, 'raw_ints': ints}], full, AUDIO_CUE_RECURRENCE_SIMILARITY)
    assert marked == 1
    assert cands[0].get('dismissed') and not cands[1].get('dismissed')


def test_short_span_at_file_start_and_end(audio, fp):
    assert fp.generate_raw_span_fingerprint(audio, 0.0, 1.5) is not None
    assert fp.generate_raw_span_fingerprint(audio, 28.5, 30.0) is not None


def test_long_span_not_windowed(audio, fp):
    ints, _ = fp.generate_raw_span_fingerprint(audio, 10.0, 16.0)
    # fpcalc withholds about 21 points of a clip; a long span is returned whole, not cut
    assert abs(len(ints) - (6.0 * FINGERPRINT_NOMINAL_FPS - 21)) <= 3
