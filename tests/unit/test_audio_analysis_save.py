"""A locked episode_details save must not leave the thread's connection in a transaction."""
import sqlite3

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('audio_analysis_save_test_')

from main_app import processing  # noqa: E402


@pytest.mark.parametrize('save, read', [
    ('save_episode_audio_analysis', 'get_episode_audio_analysis'),
    ('save_episode_dai_differential', 'get_episode_dai_differential'),
])
def test_locked_save_rolls_back_and_frees_the_connection(temp_db, mock_episode, save, read):
    slug, ep = mock_episode['slug'], mock_episode['episode_id']
    getattr(temp_db, save)(slug, ep, '{"v": 1}')
    conn = temp_db.get_connection()
    conn.execute("PRAGMA busy_timeout = 100")
    blocker = sqlite3.connect(str(temp_db.db_path))
    try:
        blocker.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError):
            getattr(temp_db, save)(slug, ep, '{"v": 2}')
        assert not conn.in_transaction
    finally:
        blocker.rollback()
        blocker.close()
        conn.execute("PRAGMA busy_timeout = 30000")
    assert getattr(temp_db, read)(slug, ep) == '{"v": 1}'
    getattr(temp_db, save)(slug, ep, '{"v": 3}')
    assert getattr(temp_db, read)(slug, ep) == '{"v": 3}'


def test_stage_failure_clears_a_leaked_transaction(monkeypatch):
    cleared = []
    monkeypatch.setattr(processing.db, 'clear_leaked_transaction',
                        lambda log, where: cleared.append(where))
    monkeypatch.setattr(processing.status_service, 'update_job_stage', lambda *a, **k: None)

    class Boom:
        def analyze(self, *a, **k):
            raise RuntimeError('boom')

    monkeypatch.setattr(processing, 'audio_analyzer', Boom())
    assert processing._run_audio_analysis('example-podcast', 'a1b2c3d4e5f6', '/nonexistent.mp3', []) is None
    assert cleared == ['audio analysis']
