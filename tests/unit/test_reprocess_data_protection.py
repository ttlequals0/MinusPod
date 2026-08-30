"""Issue #692: full/reprocess modes must not wipe episode_details before the
run produces fresh results. A crash (OOM kill, container restart) used to
leave the episode with no transcript and no markers; now the details clear is
deferred into the transcribe stage, immediately before the fresh save."""
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('reprocess_data_protection_')
from main_app import db
from main_app.processing import _download_and_transcribe
from reprocess_modes import (
    clear_episode_for_mode, batch_clear_episodes_for_mode,
    reset_episode_for_reprocess,
)

SLUG = 'reprocess-protect-feed'


def _seed_details(episode_id):
    db.save_episode_details(
        SLUG, episode_id,
        transcript_text='old transcript',
        transcript_vtt='WEBVTT',
        chapters_json='[{"title": "old"}]',
        ad_markers=[{'start': 1.0, 'end': 2.0}],
        first_pass_response='old response',
    )
    db.save_original_segments(SLUG, episode_id,
                              [{'start': 0.0, 'end': 1.0, 'text': 'old'}])


def _details_row(episode_id):
    db_id = db._get_episode_db_id(SLUG, episode_id)
    conn = db.get_connection()
    return conn.execute(
        "SELECT * FROM episode_details WHERE episode_id = ?", (db_id,)
    ).fetchone()


@pytest.fixture
def seeded_episode():
    db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Reprocess Protect')
    db.upsert_episode(SLUG, 'a1b2c3d4e5f6', title='Episode 1', status='processed',
                      original_url='https://example.com/ep1.mp3')
    _seed_details('a1b2c3d4e5f6')
    yield 'a1b2c3d4e5f6'
    db.delete_podcast(SLUG)


class TestClearHelpers:
    def test_details_clear_deferred_for_full(self, seeded_episode):
        clear_episode_for_mode(db, SLUG, seeded_episode, 'full')
        row = _details_row(seeded_episode)
        assert row['transcript_text'] == 'old transcript'
        assert row['ad_markers_json'] is not None

    def test_details_clear_deferred_for_reprocess(self, seeded_episode):
        clear_episode_for_mode(db, SLUG, seeded_episode, 'reprocess')
        assert _details_row(seeded_episode)['transcript_text'] == 'old transcript'

    def test_llm_clear_keeps_transcript(self, seeded_episode):
        clear_episode_for_mode(db, SLUG, seeded_episode, 'llm')
        row = _details_row(seeded_episode)
        assert row['transcript_text'] == 'old transcript'
        assert row['ad_markers_json'] is None

    def test_recut_clears_nothing(self, seeded_episode):
        clear_episode_for_mode(db, SLUG, seeded_episode, 'recut')
        assert _details_row(seeded_episode)['ad_markers_json'] is not None

    def test_batch_full_clear_is_noop(self, seeded_episode):
        batch_clear_episodes_for_mode(db, SLUG, [seeded_episode], 'full')
        assert _details_row(seeded_episode)['transcript_text'] == 'old transcript'


class TestResetForReprocess:
    def test_full_reset_keeps_prior_detection_data(self, seeded_episode):
        reset_episode_for_reprocess(db, SLUG, seeded_episode, 'full')
        row = _details_row(seeded_episode)
        assert row is not None
        assert row['transcript_text'] == 'old transcript'
        assert row['ad_markers_json'] is not None
        episode = db.get_episode(SLUG, seeded_episode)
        assert episode['status'] == 'pending'
        assert episode['reprocess_mode'] == 'full'


class TestTranscribeStageClear:
    """The fresh save in _download_and_transcribe wipes the stale row only
    once the new transcript exists in memory."""

    def _run_forced(self, episode_id, new_segments):
        with patch('main_app.processing.transcriber.transcribe_chunked',
                   return_value=new_segments), \
             patch('main_app.processing._retranscribe_tail_no_vad',
                   side_effect=lambda slug, ep, audio, segs, name, lang: (segs, 0)), \
             patch('main_app.processing._download_episode_audio',
                   return_value='/tmp/fake-audio.mp3'), \
             patch('main_app.processing.status_service'):
            return _download_and_transcribe(
                SLUG, episode_id, 'https://example.com/ep1.mp3', 'Reprocess Protect',
                force_transcription=True)

    def test_forced_run_clears_then_saves_fresh(self, seeded_episode):
        new_segments = [{'start': 0.0, 'end': 2.0, 'text': 'fresh transcript'}]
        audio_path, segments = self._run_forced(seeded_episode, new_segments)

        assert segments[0]['text'] == 'fresh transcript'
        row = _details_row(seeded_episode)
        # Fresh transcript saved; stale markers and LLM responses gone.
        assert 'fresh transcript' in row['transcript_text']
        assert row['ad_markers_json'] is None
        assert row['first_pass_response'] is None
        assert row['original_segments_json'] is not None

    def test_unforced_run_reuses_saved_transcript(self, seeded_episode):
        with patch('main_app.processing.transcriber.transcribe_chunked') as mock_tx, \
             patch('main_app.processing._download_episode_audio',
                   return_value='/tmp/fake-audio.mp3'), \
             patch('main_app.processing.status_service'):
            audio_path, segments = _download_and_transcribe(
                SLUG, seeded_episode, 'https://example.com/ep1.mp3', 'Reprocess Protect')
        mock_tx.assert_not_called()
        assert segments
        assert _details_row(seeded_episode)['transcript_text'] == 'old transcript'

    def test_skip_transcription_with_forced_mode_clears(self, seeded_episode):
        with patch('main_app.processing._download_episode_audio',
                   return_value='/tmp/fake-audio.mp3'), \
             patch('main_app.processing.status_service'):
            _download_and_transcribe(
                SLUG, seeded_episode, 'https://example.com/ep1.mp3', 'Reprocess Protect',
                skip_transcription=True, force_transcription=True)
        row = _details_row(seeded_episode)
        assert row is None
