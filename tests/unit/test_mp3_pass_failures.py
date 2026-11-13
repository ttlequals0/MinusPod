"""convert_to_mp3 and normalize_audio fail soft and leave no temp file behind."""
import glob
import os
import subprocess
import tempfile
from types import SimpleNamespace

import pytest

import audio_processor
from audio_processor import AudioProcessor


@pytest.fixture
def source(tmp_path, monkeypatch):
    path = tmp_path / 'in.mp3'
    path.write_bytes(b'x')
    monkeypatch.setattr(audio_processor, 'probe_render_input', lambda p: None)
    monkeypatch.setattr(audio_processor, 'source_chapter_tag', lambda p, c: None)
    return str(path)


def _run(proc, name, source):
    return proc.convert_to_mp3(source) if name == 'convert' else proc.normalize_audio(source)


def _temp_outputs():
    return set(glob.glob(os.path.join(tempfile.gettempdir(), '*.converted.mp3')) +
               glob.glob(os.path.join(tempfile.gettempdir(), '*.normalized.mp3')))


@pytest.mark.parametrize('name', ['convert', 'normalize'])
def test_ffmpeg_failure_returns_none_and_removes_the_temp_file(name, source, monkeypatch, caplog):
    before = _temp_outputs()
    monkeypatch.setattr(audio_processor, 'tracked_run',
                        lambda cmd, **k: SimpleNamespace(returncode=1, stderr=b'boom'))
    with caplog.at_level('ERROR'):
        assert _run(AudioProcessor(), name, source) is None
    assert f'FFMPEG {name} failed: boom' in caplog.text
    assert _temp_outputs() == before


@pytest.mark.parametrize('name', ['convert', 'normalize'])
def test_timeout_returns_none(name, source, monkeypatch, caplog):
    def timeout(cmd, **k):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(audio_processor, 'tracked_run', timeout)
    with caplog.at_level('ERROR'):
        assert _run(AudioProcessor(), name, source) is None
    assert f'FFMPEG {name} timed out' in caplog.text


@pytest.mark.parametrize('name', ['convert', 'normalize'])
def test_missing_input_returns_none(name, tmp_path, caplog):
    with caplog.at_level('ERROR'):
        assert _run(AudioProcessor(), name, str(tmp_path / 'missing.mp3')) is None
    assert f'{name.capitalize()} input not found' in caplog.text
