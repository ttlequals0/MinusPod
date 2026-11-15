"""utils.audio.ffmpeg_energy_reader: 16 kHz s16le from ffmpeg to 10 ms RMS frames in dBFS."""
import array
import types

from tests.app_bootstrap import bootstrap

bootstrap('ffmpeg_energy_reader_test_')

from utils import audio


def _reader(monkeypatch, samples, returncode=0):
    raw = array.array('h', samples).tobytes()
    monkeypatch.setattr(audio.subprocess_registry, 'tracked_run',
                        lambda *a, **k: types.SimpleNamespace(returncode=returncode, stdout=raw))
    return audio.ffmpeg_energy_reader('x.mp3')


def test_frames_are_rms_dbfs_and_drop_the_partial_tail(monkeypatch):
    frames = _reader(monkeypatch, [16384] * 160 + [0] * 160 + [1] * 50)(0.0, 1.0)
    assert len(frames) == 2
    assert abs(frames[0] - (-6.0206)) < 1e-3
    assert frames[1] == -120.0


def test_unreadable_audio_gives_none(monkeypatch):
    assert _reader(monkeypatch, [1] * 320, returncode=1)(0.0, 1.0) is None
    assert _reader(monkeypatch, [1] * 320)(1.0, 1.0) is None
