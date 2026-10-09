"""Tests for original_segments_json and final_segments_json persistence."""
import json
import sqlite3

import pytest


SEGMENTS_A = [
    {'start': 0.0, 'end': 5.5, 'text': 'Hello world.'},
    {'start': 5.5, 'end': 12.3, 'text': 'This is a test.'},
]

SEGMENTS_B = [
    {'start': 0.0, 'end': 4.0, 'text': 'Different segments.'},
]


def test_processing_assets_preserve_omitted_fields_and_clear_explicit_null(temp_db, mock_episode):
    slug, ep_id = mock_episode['slug'], mock_episode['episode_id']
    chapters = {'version': '1.2.0', 'chapters': [{'startTime': 5, 'title': 'Intro'}]}
    cuts = [{'start': 10, 'end': 20, 'replacement_duration': 0.125}]
    temp_db.save_processing_assets(slug, ep_id, {
        'final_segments': SEGMENTS_A, 'chapters': chapters, 'applied_cuts': cuts,
        'transcript_vtt': 'WEBVTT\n', 'transcript_text': 'Hello world.',
    })
    temp_db.save_processing_assets(slug, ep_id, {'final_segments': SEGMENTS_B, 'transcript_text': None})
    episode = temp_db.get_episode(slug, ep_id)
    assert temp_db.get_final_segments(slug, ep_id) == SEGMENTS_B
    assert json.loads(episode['chapters_json']) == chapters
    assert temp_db.get_applied_cuts(slug, ep_id) == cuts
    assert episode['transcript_vtt'] == 'WEBVTT\n'
    assert episode['transcript_text'] is None


def test_processing_asset_failure_preserves_the_entire_previous_set(temp_db, mock_episode):
    slug, ep_id = mock_episode['slug'], mock_episode['episode_id']
    prior = {'final_segments': SEGMENTS_A, 'chapters': {'chapters': []}, 'applied_cuts': [],
             'transcript_vtt': 'WEBVTT\n', 'transcript_text': 'Hello world.'}
    temp_db.save_processing_assets(slug, ep_id, prior)
    conn = temp_db.get_connection()
    before = tuple(conn.execute('SELECT * FROM episode_details WHERE episode_id = ?', (mock_episode['id'],)).fetchone())
    conn.execute("""CREATE TRIGGER refuse_asset_update BEFORE UPDATE ON episode_details
                    BEGIN SELECT RAISE(ABORT, 'asset update refused'); END""")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match='asset update refused'):
        temp_db.save_processing_assets(slug, ep_id, {
            'final_segments': SEGMENTS_B, 'chapters': {'chapters': [{'startTime': 2}]},
            'applied_cuts': [{'start': 5, 'end': 8}], 'transcript_text': 'Replacement',
        })
    conn.rollback()
    assert tuple(conn.execute('SELECT * FROM episode_details WHERE episode_id = ?', (mock_episode['id'],)).fetchone()) == before


class TestOriginalSegments:
    def test_round_trip(self, temp_db, mock_episode):
        slug = mock_episode['slug']
        ep_id = mock_episode['episode_id']

        temp_db.save_original_segments(slug, ep_id, SEGMENTS_A)
        result = temp_db.get_original_segments(slug, ep_id)

        assert result == SEGMENTS_A

    def test_get_returns_none_when_unset(self, temp_db, mock_episode):
        result = temp_db.get_original_segments(mock_episode['slug'], mock_episode['episode_id'])
        assert result is None

    def test_write_once_does_not_overwrite(self, temp_db, mock_episode):
        slug = mock_episode['slug']
        ep_id = mock_episode['episode_id']

        temp_db.save_original_segments(slug, ep_id, SEGMENTS_A)
        temp_db.save_original_segments(slug, ep_id, SEGMENTS_B)

        result = temp_db.get_original_segments(slug, ep_id)
        assert result == SEGMENTS_A

    def test_unknown_episode_no_op(self, temp_db, mock_podcast):
        temp_db.save_original_segments(mock_podcast['slug'], 'nonexistent-id', SEGMENTS_A)
        result = temp_db.get_original_segments(mock_podcast['slug'], 'nonexistent-id')
        assert result is None


class TestFinalSegments:
    def test_round_trip(self, temp_db, mock_episode):
        slug = mock_episode['slug']
        ep_id = mock_episode['episode_id']

        temp_db.save_final_segments(slug, ep_id, SEGMENTS_A)
        result = temp_db.get_final_segments(slug, ep_id)

        assert result == SEGMENTS_A

    def test_get_returns_none_when_unset(self, temp_db, mock_episode):
        result = temp_db.get_final_segments(mock_episode['slug'], mock_episode['episode_id'])
        assert result is None

    def test_overwrite_on_reprocess(self, temp_db, mock_episode):
        slug = mock_episode['slug']
        ep_id = mock_episode['episode_id']

        temp_db.save_final_segments(slug, ep_id, SEGMENTS_A)
        temp_db.save_final_segments(slug, ep_id, SEGMENTS_B)

        result = temp_db.get_final_segments(slug, ep_id)
        assert result == SEGMENTS_B

    def test_independent_of_original(self, temp_db, mock_episode):
        slug = mock_episode['slug']
        ep_id = mock_episode['episode_id']

        temp_db.save_original_segments(slug, ep_id, SEGMENTS_A)
        temp_db.save_final_segments(slug, ep_id, SEGMENTS_B)

        assert temp_db.get_original_segments(slug, ep_id) == SEGMENTS_A
        assert temp_db.get_final_segments(slug, ep_id) == SEGMENTS_B


class TestTranscriptRepair:
    def test_repair_overwrites_the_write_once_originals(self, temp_db, mock_episode):
        slug, ep_id = mock_episode['slug'], mock_episode['episode_id']
        temp_db.save_original_segments(slug, ep_id, SEGMENTS_B)
        temp_db.save_original_transcript(slug, ep_id, 'old')
        temp_db.save_repaired_original_transcript(slug, ep_id, 'repaired', SEGMENTS_A)
        assert temp_db.get_original_segments(slug, ep_id) == SEGMENTS_A
        assert temp_db.get_original_transcript(slug, ep_id) == 'repaired'

    def test_repair_holes_append_and_replace(self, temp_db, mock_episode):
        slug, ep_id = mock_episode['slug'], mock_episode['episode_id']
        first = {'start': 10.0, 'end': 20.0, 'reason': 'quiet'}
        second = {'start': 30.0, 'end': 45.0, 'reason': 'no_speech'}
        assert temp_db.get_repair_holes(slug, ep_id) == []
        temp_db.add_repair_holes(slug, ep_id, [first])
        temp_db.add_repair_holes(slug, ep_id, [second])
        assert temp_db.get_repair_holes(slug, ep_id) == [first, second]
        temp_db.add_repair_holes(slug, ep_id, [], replace=True)
        assert temp_db.get_repair_holes(slug, ep_id) == []

    def test_repair_holes_column_added_to_an_existing_db(self, temp_db, mock_episode):
        slug, ep_id = mock_episode['slug'], mock_episode['episode_id']
        temp_db.save_original_transcript(slug, ep_id, 'kept')
        conn = temp_db.get_connection()
        conn.execute("ALTER TABLE episode_details DROP COLUMN repair_holes_json")
        conn.commit()
        temp_db._run_schema_migrations()
        cols = {r[1] for r in conn.execute("PRAGMA table_info(episode_details)")}
        assert 'repair_holes_json' in cols
        assert temp_db.get_original_transcript(slug, ep_id) == 'kept'
