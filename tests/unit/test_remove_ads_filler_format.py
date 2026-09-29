"""remove_ads conforms each filler branch to the episode format before concat (#796)."""
import re
from unittest.mock import MagicMock

import pytest

import audio_processor
from audio_processor import AudioProcessor


def _filter_graph(monkeypatch, probe, ads):
    p = AudioProcessor()
    monkeypatch.setattr(p, 'get_audio_duration',
                        MagicMock(side_effect=[600.0, 600.0]))
    monkeypatch.setattr(p, 'get_beep_duration', MagicMock(return_value=1.0))
    run = MagicMock(return_value=MagicMock(returncode=0))
    monkeypatch.setattr(audio_processor, 'tracked_run', run)
    monkeypatch.setattr(audio_processor, 'probe_chapters',
                        MagicMock(return_value=[]))
    monkeypatch.setattr(audio_processor, 'probe_audio_format',
                        MagicMock(return_value=probe))
    assert p.remove_ads('/nonexistent-in.mp3', ads,
                        '/nonexistent-out.mp3') is not None
    cmd = run.call_args[0][0]
    return cmd[cmd.index('-filter_complex') + 1]


def _branches(graph):
    parts = graph.split(';')
    beeps = [b for b in parts if re.search(r'\[beep\d+\]$', b)]
    content = [b for b in parts if b.startswith('[0:a]')]
    return beeps, content


ONE_AD = [{'start': 100.0, 'end': 160.0}]
TWO_ADS = [{'start': 100.0, 'end': 160.0}, {'start': 300.0, 'end': 360.0}]
ASETNSAMPLES = ',asetnsamples=n=1152:p=1[out]'
STEREO_CHAIN = ('aresample=44100,aformat=sample_fmts=fltp:'
                'sample_rates=44100:channel_layouts=stereo')


@pytest.mark.parametrize('ads', [ONE_AD, TWO_ADS])
def test_filler_branches_conformed_to_episode(monkeypatch, ads):
    graph = _filter_graph(monkeypatch, (44100, 2, 'stereo'), ads)
    beeps, content = _branches(graph)
    assert len(beeps) == len(ads)
    for b in beeps:
        assert re.search(re.escape(',' + STEREO_CHAIN) + r'\[beep\d+\]$', b), b
    assert graph.endswith(ASETNSAMPLES)
    assert content
    for c in content:
        assert 'aformat' not in c and 'aresample' not in c


def test_probe_failure_keeps_current_graph(monkeypatch):
    graph = _filter_graph(monkeypatch, None, TWO_ADS)
    assert 'aformat' not in graph
    assert 'aresample' not in graph
    assert graph.endswith(ASETNSAMPLES)


def test_mono_without_layout_falls_back_to_mono(monkeypatch):
    graph = _filter_graph(monkeypatch, (22050, 1, ''), ONE_AD)
    beeps, _ = _branches(graph)
    assert beeps[0].endswith(
        ',aresample=22050,aformat=sample_fmts=fltp:sample_rates=22050:'
        'channel_layouts=mono[beep1]')


def test_unknown_layout_omits_channel_layouts(monkeypatch):
    graph = _filter_graph(monkeypatch, (48000, 6, ''), ONE_AD)
    beeps, _ = _branches(graph)
    assert beeps[0].endswith(
        ',aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000[beep1]')
    assert 'channel_layouts' not in graph
