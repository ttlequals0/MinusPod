"""Pass-through import of the publisher's chapters and transcript."""
import json
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('passthrough_assets_test_', reset_storage=True)

from main_app import db, storage  # noqa: E402
from main_app.pipeline import passthrough_assets  # noqa: E402
from transcript_differential import UpstreamTranscript  # noqa: E402

SLUG, EP = 'pt-assets-feed', 'a1b2c3d4e5f6'
CHAPTERS = [{'startTime': 0, 'title': 'Intro'}, {'startTime': 60.5, 'title': ''}]
CUES = [{'start': 0.0, 'end': 4.0, 'text': 'Hello there'},
        {'start': 4.0, 'end': 9.0, 'text': 'Welcome back'}]


def _transcript(timed=True):
    return UpstreamTranscript(cues=CUES, timed=timed,
                              source_url='https://example.com/t.vtt', mime='text/vtt')


def _episode(chapters=False, transcript=False):
    return {
        'upstream_chapters_url': 'https://example.com/c.json' if chapters else None,
        'upstream_transcript_url': 'https://example.com/t.vtt' if transcript else None,
        'upstream_transcript_type': 'text/vtt',
    }


def _details():
    row = db.get_connection().execute(
        """SELECT ed.* FROM episode_details ed JOIN episodes e ON ed.episode_id = e.id
           WHERE e.episode_id = ?""", (EP,)).fetchone()
    return dict(row)


@pytest.fixture(autouse=True)
def _rows():
    db.create_podcast(SLUG, f'https://example.com/{SLUG}.xml', SLUG)
    db.upsert_episode(SLUG, EP, original_url='https://example.com/e.mp3', title='E',
                      status='processed')
    db.set_setting('vtt_transcripts_enabled', 'true', is_default=False)
    db.save_episode_details(SLUG, EP, transcript_text='whisper text')
    db.get_connection().execute(
        "UPDATE episode_details SET upstream_transcript_json = '{\"status\": \"ok\"}'")
    db.get_connection().commit()
    yield
    db.delete_podcast(SLUG)


def _run(episode, chapters=CHAPTERS, transcript=None):
    with patch.object(passthrough_assets, 'fetch_upstream_chapters',
                      return_value=chapters) as fc, \
            patch.object(passthrough_assets, 'fetch_upstream_transcript',
                         return_value=transcript) as ft:
        stats = passthrough_assets.import_upstream_assets(SLUG, EP, episode)
    return stats, fc, ft


def test_chapters_stored_in_standard_document_shape():
    stats, _, _ = _run(_episode(chapters=True))
    assert stats['upstream_chapters'] == 2
    stored = storage.get_chapters_json(SLUG, EP)
    assert stored['version'] == '1.2.0'
    assert [c['title'] for c in stored['chapters']] == ['Intro', 'Chapter 2']


@pytest.mark.parametrize('timed', [True, False])
def test_transcript_stores_every_form(timed):
    stats, _, _ = _run(_episode(transcript=True), transcript=_transcript(timed=timed))
    assert stats['upstream_transcript'] == ('timed' if timed else 'untimed')
    row = _details()
    assert row['transcript_source'] == 'upstream'
    assert row['upstream_transcript_json'] is None
    if timed:
        assert 'Hello there' in row['transcript_text']
        assert row['transcript_vtt'].startswith('WEBVTT')
        assert json.loads(row['original_segments_json']) == CUES
        assert json.loads(row['final_segments_json']) == CUES
    else:
        assert row['transcript_text'] == 'Hello there\nWelcome back'
        assert row['transcript_vtt'] is None
        assert row['original_segments_json'] is None
        assert row['final_segments_json'] is None


def test_cue_without_end_gets_numeric_end():
    cues = [{'start': 0.0, 'end': None, 'text': 'One'}, {'start': 3.0, 'end': None, 'text': 'Two'}]
    transcript = UpstreamTranscript(cues=cues, timed=True,
                                    source_url='https://example.com/t.json', mime='application/json')
    stats, _, _ = _run(_episode(transcript=True), transcript=transcript)
    assert stats['upstream_transcript'] == 'timed'
    ends = [s['end'] for s in json.loads(_details()['final_segments_json'])]
    assert ends == [3.0, 4.0]


def test_vtt_setting_off_skips_vtt():
    db.set_setting('vtt_transcripts_enabled', 'false', is_default=False)
    _run(_episode(transcript=True), transcript=_transcript())
    row = _details()
    assert row['transcript_vtt'] is None
    assert row['transcript_text']


def test_fetch_failures_save_nothing_and_do_not_raise():
    stats, _, _ = _run(_episode(chapters=True, transcript=True), chapters=None, transcript=None)
    assert stats == {'upstream_chapters': None, 'upstream_transcript': 'error'}
    row = _details()
    assert row['transcript_text'] == 'whisper text'
    assert row['transcript_source'] is None
    assert row['chapters_json'] is None


def test_exception_is_swallowed():
    with patch.object(passthrough_assets, 'fetch_upstream_chapters',
                      side_effect=RuntimeError('boom')), \
            patch.object(passthrough_assets, 'fetch_upstream_transcript',
                         side_effect=RuntimeError('boom')):
        stats = passthrough_assets.import_upstream_assets(
            SLUG, EP, _episode(chapters=True, transcript=True))
    assert stats == {'upstream_chapters': None, 'upstream_transcript': 'error'}


def test_no_urls_calls_no_fetchers():
    stats, fc, ft = _run(_episode())
    fc.assert_not_called()
    ft.assert_not_called()
    assert stats == {'upstream_chapters': None, 'upstream_transcript': 'none'}
