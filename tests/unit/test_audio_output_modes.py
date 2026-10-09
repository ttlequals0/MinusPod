"""Replacement sound choices preserve actual cut and beep timelines."""

import shutil
from pathlib import Path

import pytest

import audio_processor

from audio_processor import AudioProcessor
from api.episodes import chapters_only_decisions
from utils.audio import get_audio_duration
from utils.time import adjust_timestamp

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def source(tmp_path):
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg is unavailable')
    path = tmp_path / 'source.mp3'
    path.write_bytes((ROOT / 'assets/replace.mp3').read_bytes() * 90)
    return path


def test_defaults_retain_replacement_sound():
    processor = AudioProcessor()
    assert processor.replacement_sound_enabled is True
    assert processor.mp3_stream_copy_enabled is False
    cuts = processor.compute_applied_cuts([{'start': 20, 'end': 30}], 100)
    assert cuts[0]['replacement_duration'] == processor.get_beep_duration()


def test_sound_off_never_reads_replacement_for_remove(monkeypatch):
    processor = AudioProcessor(replacement_sound_enabled=False)
    monkeypatch.setattr(processor, 'get_beep_duration', lambda: pytest.fail('Remove must not read filler'))
    cuts = processor.compute_applied_cuts([{'start': 20, 'end': 30}], 100)
    assert cuts[0]['replacement_duration'] == 0
    assert adjust_timestamp(40, cuts, 99) == 30


def test_sound_off_actual_remove_without_replacement_file(source, tmp_path):
    output = tmp_path / 'output.mp3'
    processor = AudioProcessor(replace_audio_path=str(tmp_path / 'absent.mp3'),
                               replacement_sound_enabled=False)
    applied = processor.remove_ads(str(source), [{'start': 20, 'end': 30}], str(output))
    assert applied == [{'start': 20, 'end': 30, 'replacement_duration': 0.0}]
    assert get_audio_duration(str(output)) == pytest.approx(get_audio_duration(str(source)) - 10, abs=.1)


@pytest.mark.parametrize('beeps', [1, 2])
def test_sound_off_mixed_actions_split_only_actual_fillers(source, tmp_path, monkeypatch, beeps):
    output = tmp_path / 'output.mp3'
    processor = AudioProcessor(replacement_sound_enabled=False)
    recorded = []
    original_tracked = audio_processor.tracked_run

    def record(command, **kwargs):
        recorded.append(command)
        return original_tracked(command, **kwargs)

    monkeypatch.setattr(audio_processor, 'tracked_run', record)
    ads = [{'start': 10, 'end': 20}, {'start': 25, 'end': 30, 'beep': True, 'confidence': .99}]
    if beeps == 2:
        ads.append({'start': 35, 'end': 40, 'beep': True, 'confidence': .99})
    applied = processor.remove_ads(str(source), ads, str(output))
    assert applied is not None
    assert [c['replacement_duration'] for c in applied] == [0.0, *([5.0] * beeps)]
    command = recorded[0]
    graph = command[command.index('-filter_complex') + 1]
    assert graph.count('volume=0.4') == beeps
    assert ('asplit=2' in graph) is (beeps == 2)
    content = [branch for branch in graph.split(';') if branch.startswith('[0:a]')]
    assert 'afade' not in content[0]
    assert 'afade=t=out' in content[1] and 'afade=t=in' not in content[1]
    assert 'd=0.0' not in graph
    assert get_audio_duration(str(output)) == pytest.approx(get_audio_duration(str(source)) - 10, abs=.1)


def test_sound_off_all_audio_removed_preserves_existing_output(source, tmp_path):
    output = tmp_path / 'output.mp3'
    original = (ROOT / 'assets/replace.mp3').read_bytes()
    output.write_bytes(original)
    processor = AudioProcessor(replacement_sound_enabled=False)
    assert processor.remove_ads(str(source), [{'start': 0, 'end': 100}], str(output)) is None
    assert output.read_bytes() == original


def test_no_cuts_preserves_encoded_bytes(source, tmp_path):
    output = tmp_path / 'output.mp3'
    processor = AudioProcessor(replacement_sound_enabled=False, mp3_stream_copy_enabled=True)
    assert processor.remove_ads(str(source), [], str(output)) == []
    assert output.read_bytes() == source.read_bytes()


def test_chapters_only_comparison_uses_effective_replacement_sound():
    markers = [{'start': 20, 'end': 30, 'action_applied': 'remove'}]
    silent = [{'start': 20, 'end': 30, 'replacement_duration': 0.0}]
    sounded = [dict(silent[0], replacement_duration=1.08)]
    assert chapters_only_decisions(markers, silent, 100, replacement_sound_enabled=False)
    assert not chapters_only_decisions(markers, sounded, 100, replacement_sound_enabled=False)
    assert not chapters_only_decisions(markers, [{'start': 20, 'end': 30}], 100,
                                      replacement_sound_enabled=False)
