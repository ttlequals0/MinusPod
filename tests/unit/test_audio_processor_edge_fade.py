"""A cut edge placed in a pause fades inside that pause; every other edge keeps today's fades."""
import shutil
from pathlib import Path

import pytest

import audio_processor
from audio_processor import AudioProcessor, edge_fade

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def source(tmp_path):
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg is unavailable')
    path = tmp_path / 'source.mp3'
    path.write_bytes((ROOT / 'assets/replace.mp3').read_bytes() * 90)
    return path


def _graph(source, tmp_path, monkeypatch, ads):
    recorded = []
    original = audio_processor.tracked_run

    def record(command, **kwargs):
        recorded.append(command)
        return original(command, **kwargs)

    monkeypatch.setattr(audio_processor, 'tracked_run', record)
    assert AudioProcessor().remove_ads(str(source), ads, str(tmp_path / 'out.mp3')) is not None
    command = next(c for c in recorded if '-filter_complex' in c)
    graph = command[command.index('-filter_complex') + 1]
    return [b for b in graph.split(';') if b.startswith('[0:a]')]


def test_edge_fade_helper():
    assert edge_fade(0.5, {}, 'start') == 0.5
    assert edge_fade(0.5, {'edge_pause_s': {'start': 0.2}}, 'start') == 0.2
    assert edge_fade(0.5, {'edge_pause_s': {'start': 0.2}}, 'end') == 0.5
    assert edge_fade(0.5, {'edge_pause_s': {'start': 0.01}}, 'start') == 0.05
    assert edge_fade(0.8, {'edge_pause_s': {'end': 3.0}}, 'end') == 0.8
    assert edge_fade(0.0, {'edge_pause_s': {'end': 0.3}}, 'end') == 0.0


def test_fade_clamped_to_pause_for_snapped_edge(source, tmp_path, monkeypatch):
    ad = {'start': 20, 'end': 30, 'edge_pause_s': {'start': 0.2, 'end': 0.3}}
    content = _graph(source, tmp_path, monkeypatch, [ad])
    assert 'afade=t=out' in content[0] and ':d=0.2' in content[0].split('afade=t=out')[1]
    assert 'afade=t=in:d=0.3' in content[1]


def test_default_fades_unchanged(source, tmp_path, monkeypatch):
    content = _graph(source, tmp_path, monkeypatch, [{'start': 20, 'end': 30}])
    assert ':d=0.5' in content[0].split('afade=t=out')[1]
    assert 'afade=t=in:d=0.8' in content[1]


def test_pause_survives_the_gap_merge():
    cuts = AudioProcessor().compute_applied_cuts(
        [{'start': 20, 'end': 30, 'edge_pause_s': {'start': 0.2, 'end': 0.3}},
         {'start': 30.4, 'end': 45, 'edge_pause_s': {'start': 0.4, 'end': 0.6}}], 100)
    assert len(cuts) == 1
    assert cuts[0]['edge_pause_s'] == {'start': 0.2, 'end': 0.6}
