"""WHISPER_DEVICE validation: an unrecognized value transcribes on CPU (issue #605)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from config import WHISPER_DEVICES, resolve_whisper_device  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv('WHISPER_DEVICE', raising=False)


@pytest.mark.parametrize('value,expected', [
    ('cuda', 'cuda'),
    ('cpu', 'cpu'),
    ('CUDA', 'cuda'),
    (' cuda ', 'cuda'),
])
def test_accepted_values_normalize(monkeypatch, value, expected):
    monkeypatch.setenv('WHISPER_DEVICE', value)
    assert resolve_whisper_device() == expected


@pytest.mark.parametrize('value', ['gpu', 'auto', 'nvidia', 'cuda:0', ''])
def test_unrecognized_values_fall_back_to_cpu(monkeypatch, value):
    # CTranslate2 takes only cpu/cuda from us; anything else killed model init
    # before this guard existed, leaving gaps in the transcript.
    monkeypatch.setenv('WHISPER_DEVICE', value)
    assert resolve_whisper_device() == 'cpu'


def test_unset_defaults_to_cpu():
    assert resolve_whisper_device() == 'cpu'


def test_unrecognized_value_is_logged(monkeypatch, caplog):
    monkeypatch.setenv('WHISPER_DEVICE', 'gpu')
    with caplog.at_level('WARNING'):
        resolve_whisper_device()
    assert 'gpu' in caplog.text
    assert 'cpu' in caplog.text


def test_every_accepted_value_is_one_ctranslate2_takes():
    assert set(WHISPER_DEVICES) == {'cpu', 'cuda'}


def test_transcriber_uses_the_resolver_everywhere():
    # All three device reads must go through validation, not raw os.getenv.
    from pathlib import Path
    source = (Path(__file__).resolve().parents[2] / 'src' / 'transcriber.py').read_text()
    assert 'os.getenv("WHISPER_DEVICE"' not in source
    assert source.count('resolve_whisper_device()') >= 3
