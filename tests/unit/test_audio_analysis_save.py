"""A locked audio-analysis save must not leave the thread's connection in a transaction."""
import sqlite3

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('audio_analysis_save_test_')

from main_app import processing  # noqa: E402


def _seed(db):
    db.create_podcast('example-podcast', 'https://example.com/feed', 'Example')
    db.upsert_episode(slug='example-podcast', episode_id='a1b2c3d4e5f6',
                      original_url='https://example.com/ep.mp3', title='Ep')


def test_locked_update_rolls_back_and_frees_the_connection(temp_db, monkeypatch):
    db = temp_db
    _seed(db)
    db.save_episode_audio_analysis('example-podcast', 'a1b2c3d4e5f6', '{"v": 1}')
    conn = db.get_connection()
    real_execute = conn.execute

    def locked(sql, *args):
        if sql.startswith('UPDATE episode_details SET audio_analysis_json'):
            raise sqlite3.OperationalError('database is locked')
        return real_execute(sql, *args)

    monkeypatch.setattr(conn, 'execute', locked)
    with pytest.raises(sqlite3.OperationalError):
        db.save_episode_audio_analysis('example-podcast', 'a1b2c3d4e5f6', '{"v": 2}')
    assert not conn.in_transaction
    monkeypatch.undo()
    assert db.get_episode_audio_analysis('example-podcast', 'a1b2c3d4e5f6') == '{"v": 1}'


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
