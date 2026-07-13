"""remove_ads logs expected-vs-actual render drift (spec 1.5).

Expected output duration is input - sum(applied cuts) + n_cuts * beep;
drift beyond RENDER_DRIFT_WARN_SECONDS warns with the applied cut list so
production overshoot reports are attributable from logs alone.
"""
import logging
from unittest.mock import MagicMock

import audio_processor
from audio_processor import AudioProcessor


def _run(monkeypatch, output_duration):
    p = AudioProcessor()
    monkeypatch.setattr(p, 'get_audio_duration',
                        MagicMock(side_effect=[600.0, output_duration]))
    monkeypatch.setattr(p, 'get_beep_duration', MagicMock(return_value=1.0))
    monkeypatch.setattr(audio_processor, 'tracked_run',
                        MagicMock(return_value=MagicMock(returncode=0)))
    # The fake input path has no chapters to probe; keep the chapter branch
    # out of these log assertions.
    monkeypatch.setattr(audio_processor, 'probe_chapters',
                        MagicMock(return_value=[]))
    applied = p.remove_ads('/nonexistent-in.mp3',
                           [{'start': 100.0, 'end': 160.0}],
                           '/nonexistent-out.mp3')
    assert applied is not None
    return applied


def test_drift_within_tolerance_logs_info_only(monkeypatch, caplog):
    # expected = 600 - 60 + 1 * 1.0 = 541.0 -> drift +0.00s
    with caplog.at_level(logging.INFO, logger='audio_processor'):
        _run(monkeypatch, 541.0)
    assert any('render drift +0.00s' in r.message for r in caplog.records)
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


def test_drift_beyond_tolerance_warns_with_cut_list(monkeypatch, caplog):
    # expected 541.0, actual 548.0 -> drift +7.0s (the production symptom)
    with caplog.at_level(logging.INFO, logger='audio_processor'):
        _run(monkeypatch, 548.0)
    warn = next(r for r in caplog.records if r.levelno == logging.WARNING)
    assert 'Cut-render drift +7.00s' in warn.message
    assert '(100.0, 160.0)' in warn.message
