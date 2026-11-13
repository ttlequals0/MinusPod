"""A fingerprint stage that cannot read the audio logs one labelled warning and never the path."""
import logging
import stat

import pytest

import audio_fingerprinter as af
from audio_fingerprinter import AudioFingerprinter

LABEL = 'example-show:a1b2c3d4e5f6'
KNOWN = [(1, 'AQAA', 10.0, None, None)]
GOOD_JSON = '{"duration": 100.0, "fingerprint": [1, 2, 3, 4]}'


def _script(tmp_path, body):
    path = tmp_path / 'fake-fpcalc'
    path.write_text('#!/bin/sh\n' + body + '\n')
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


@pytest.fixture
def run(tmp_path, monkeypatch, caplog):
    audio = tmp_path / 'private-dir' / 'episode-audio.mp3'

    def go(fpcalc_body=None, *, fpcalc_path=None):
        fp = AudioFingerprinter.__new__(AudioFingerprinter)
        fp.db = None
        fp._fpcalc_path = fpcalc_path or _script(tmp_path, fpcalc_body)
        monkeypatch.setattr(fp, '_get_audio_duration', lambda p: 100.0)
        with caplog.at_level(logging.DEBUG):
            caplog.clear()
            out = fp.find_matches(str(audio), known_fingerprints=KNOWN, episode_label=LABEL)
        loud = [r for r in caplog.records if r.levelno >= logging.WARNING]
        return out, loud, str(audio)
    return go


def _assert_one_labelled_warning(out, loud, audio):
    assert out == []
    assert len(loud) == 1 and loud[0].levelno == logging.WARNING
    text = loud[0].getMessage()
    assert LABEL in text
    assert audio not in text and 'episode-audio' not in text and 'private-dir' not in text


def test_nonzero_exit_with_no_output(run):
    _assert_one_labelled_warning(*run('echo "decode error in $2 $3 $4 $5 $6 $7" >&2; exit 3'))


def test_unparseable_output(run):
    _assert_one_labelled_warning(*run('echo "not json"; echo "bad $*" >&2'))


def test_decoder_missing(run, monkeypatch):
    monkeypatch.setattr(af, 'acoustid', None)
    _assert_one_labelled_warning(*run(f"echo '{GOOD_JSON}'"))


def test_exception_in_the_whole_file_path(run, tmp_path):
    _assert_one_labelled_warning(*run(fpcalc_path=str(tmp_path / 'private-dir' / 'no-such-fpcalc')))
