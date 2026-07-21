"""Unit tests for the per-feed chapters mode (issue #560).

'auto' (default) preserves publisher-embedded chapters when enough of them
survive the cut: it probes the PROCESSED file, which the ffmpeg cut step has
already remapped onto the cut timeline (audio_processor.py), instead of
generating new ones with the chapter LLM. 'generate' keeps the pre-#560
behavior unconditionally; 'off' skips the chapter step entirely.
"""
from unittest.mock import MagicMock

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('chapters_mode_test_')

import chapters_generator
from config import (
    CHAPTERS_MODE_AUTO,
    CHAPTERS_MODE_GENERATE,
    CHAPTERS_MODE_OFF,
    resolve_chapters_mode,
)
from main_app import processing


# ---------- resolve_chapters_mode ----------

def test_missing_row_resolves_auto():
    assert resolve_chapters_mode({}) == CHAPTERS_MODE_AUTO
    assert resolve_chapters_mode(None) == CHAPTERS_MODE_AUTO


def test_null_column_resolves_auto():
    assert resolve_chapters_mode({'chapters_mode': None}) == CHAPTERS_MODE_AUTO


def test_explicit_values_pass_through():
    assert resolve_chapters_mode({'chapters_mode': 'generate'}) == CHAPTERS_MODE_GENERATE
    assert resolve_chapters_mode({'chapters_mode': 'off'}) == CHAPTERS_MODE_OFF
    assert resolve_chapters_mode({'chapters_mode': 'auto'}) == CHAPTERS_MODE_AUTO


def test_invalid_value_falls_back_to_auto():
    assert resolve_chapters_mode({'chapters_mode': 'bogus'}) == CHAPTERS_MODE_AUTO


# ---------- _generate_assets chapter block ----------

def _db(chapters_mode=None, chapters_enabled=None):
    db = MagicMock()

    def get_setting(key):
        if key == 'chapters_enabled':
            return chapters_enabled
        if key == 'vtt_transcripts_enabled':
            return 'false'
        return None

    db.get_setting.side_effect = get_setting
    db.get_podcast_by_slug.return_value = {'chapters_mode': chapters_mode}
    return db


def _run(monkeypatch, db, publisher_chapters, generator_chapters=None, podcast_row=None):
    """Invoke the real _generate_assets with all IO seams mocked, returning
    (storage_mock, probe_mock, generator_class_mock, embed_mock)."""
    storage_mock = MagicMock()
    probe_mock = MagicMock(return_value=publisher_chapters)
    embed_mock = MagicMock()
    transcript_gen_class = MagicMock()
    transcript_gen_class.return_value.compute_final_segments.return_value = []
    transcript_gen_class.return_value.generate_text.return_value = None

    generator_class = MagicMock()
    generator_class.return_value.generate_chapters.return_value = (
        generator_chapters if generator_chapters is not None
        else {'chapters': [{'startTime': 0, 'title': 'Generated'}]}
    )

    monkeypatch.setattr(processing, 'db', db)
    monkeypatch.setattr(processing, 'storage', storage_mock)
    monkeypatch.setattr(processing, 'probe_chapters', probe_mock)
    monkeypatch.setattr(processing, 'embed_chapters', embed_mock)
    monkeypatch.setattr(processing, 'get_replacement_duration', lambda: 2.0)
    monkeypatch.setattr('transcript_generator.TranscriptGenerator', transcript_gen_class)
    monkeypatch.setattr(chapters_generator, 'ChaptersGenerator', generator_class)

    processing._generate_assets(
        'testslug', 'ep1', segments=[], all_cuts=[], episode_description='desc',
        podcast_name='Pod', episode_title='Title', regenerate_chapters=True,
        audio_path='/tmp/fake-processed.mp3', audio_duration=100.0,
        podcast_row=podcast_row,
    )
    return storage_mock, probe_mock, generator_class, embed_mock


def test_auto_preserves_publisher_chapters_no_llm_no_embed(monkeypatch):
    publisher = [
        {'start': 0.0, 'end': 30.0, 'title': 'Intro'},
        {'start': 100.4, 'end': 200.0, 'title': ''},
    ]
    db = _db(chapters_mode='auto')
    storage_mock, probe_mock, generator_class, embed_mock = _run(monkeypatch, db, publisher)

    probe_mock.assert_called_once_with('/tmp/fake-processed.mp3')
    generator_class.return_value.generate_chapters.assert_not_called()
    embed_mock.assert_not_called()
    storage_mock.save_chapters_and_applied_cuts.assert_called_once_with(
        'testslug', 'ep1',
        {'version': '1.2.0', 'chapters': [
            # startTime floors at 1, not 0 (some podcast apps require it).
            {'startTime': 1, 'title': 'Intro'},
            {'startTime': 100, 'title': 'Chapter 2'},
        ]},
        [],
    )


def test_auto_with_zero_publisher_chapters_falls_back_to_generate(monkeypatch):
    db = _db(chapters_mode='auto')
    storage_mock, probe_mock, generator_class, embed_mock = _run(monkeypatch, db, [])

    generator_class.return_value.generate_chapters.assert_called_once()
    storage_mock.save_chapters_and_applied_cuts.assert_called_once()


def test_auto_with_one_publisher_chapter_falls_back_to_generate(monkeypatch):
    db = _db(chapters_mode='auto')
    publisher = [{'start': 0.0, 'end': 30.0, 'title': 'Only One'}]
    storage_mock, probe_mock, generator_class, embed_mock = _run(monkeypatch, db, publisher)

    generator_class.return_value.generate_chapters.assert_called_once()
    storage_mock.save_chapters_and_applied_cuts.assert_called_once()


def test_auto_probe_failure_skips_chapter_step_without_generating(monkeypatch):
    # probe_chapters returns None (not []) on a transient ffprobe failure,
    # distinct from "definitively no chapters" (embedded_chapters.py). This
    # must NOT fall through to generate+embed, which would overwrite the ID3
    # frames the cut step already wrote correctly (issue #500's failure mode).
    db = _db(chapters_mode='auto')
    storage_mock, probe_mock, generator_class, embed_mock = _run(monkeypatch, db, None)

    probe_mock.assert_called_once_with('/tmp/fake-processed.mp3')
    generator_class.return_value.generate_chapters.assert_not_called()
    storage_mock.save_chapters_and_applied_cuts.assert_not_called()
    embed_mock.assert_not_called()


def test_mode_off_skips_generator_and_save(monkeypatch):
    db = _db(chapters_mode='off')
    publisher = [
        {'start': 0.0, 'end': 30.0, 'title': 'Intro'},
        {'start': 100.0, 'end': 200.0, 'title': 'Body'},
    ]
    storage_mock, probe_mock, generator_class, embed_mock = _run(monkeypatch, db, publisher)

    generator_class.return_value.generate_chapters.assert_not_called()
    storage_mock.save_chapters_and_applied_cuts.assert_not_called()
    probe_mock.assert_not_called()


def test_mode_generate_runs_generator_regardless_of_publisher_chapters(monkeypatch):
    db = _db(chapters_mode='generate')
    publisher = [
        {'start': 0.0, 'end': 30.0, 'title': 'Intro'},
        {'start': 100.0, 'end': 200.0, 'title': 'Body'},
    ]
    storage_mock, probe_mock, generator_class, embed_mock = _run(monkeypatch, db, publisher)

    probe_mock.assert_not_called()
    generator_class.return_value.generate_chapters.assert_called_once()
    storage_mock.save_chapters_and_applied_cuts.assert_called_once()
    embed_mock.assert_called_once()


def test_global_chapters_enabled_false_unchanged(monkeypatch):
    db = _db(chapters_mode='auto', chapters_enabled='false')
    publisher = [
        {'start': 0.0, 'end': 30.0, 'title': 'Intro'},
        {'start': 100.0, 'end': 200.0, 'title': 'Body'},
    ]
    storage_mock, probe_mock, generator_class, embed_mock = _run(monkeypatch, db, publisher)

    probe_mock.assert_not_called()
    generator_class.return_value.generate_chapters.assert_not_called()
    storage_mock.save_chapters_and_applied_cuts.assert_not_called()


def test_passed_in_podcast_row_skips_refetch(monkeypatch):
    # get_podcast_by_slug would resolve 'auto' if it were consulted; passing
    # podcast_row explicitly must both win and avoid the extra DB call.
    db = _db(chapters_mode='auto')
    storage_mock, probe_mock, generator_class, embed_mock = _run(
        monkeypatch, db, [], podcast_row={'chapters_mode': 'off'})

    db.get_podcast_by_slug.assert_not_called()
    generator_class.return_value.generate_chapters.assert_not_called()
    storage_mock.save_chapters_and_applied_cuts.assert_not_called()
