"""Episode run-log storage settings: globals, per-feed override, resolution (#660)."""
import logging

import pytest

from tests.app_bootstrap import bootstrap

_data_dir = bootstrap('episode_log_settings_test_')

from config import (  # noqa: E402
    EPISODE_LOGS_OFF, EPISODE_LOGS_ON, EPISODE_LOGS_VALUES,
    EPISODE_LOG_RETENTION_DAYS_MAX,
    resolve_episode_log_level, resolve_episode_log_retention_days,
    resolve_episode_log_storage,
)
from database import Database  # noqa: E402


def _clear_setting(db, key):
    """Drop the boot-seeded row so the env seed is what resolution sees."""
    conn = db.get_connection()
    conn.execute('DELETE FROM settings WHERE key = ?', (key,))
    conn.commit()


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.delenv('EPISODE_LOG_RETENTION_DAYS', raising=False)
    monkeypatch.delenv('EPISODE_LOG_LEVEL', raising=False)
    Database._instance = None
    handle = Database(data_dir=str(tmp_path))
    yield handle
    Database._instance = None


class TestRetentionDays:
    def test_default_is_thirty_days(self, db):
        assert resolve_episode_log_retention_days(db) == 30

    def test_env_seed_is_used_when_unset(self, db, monkeypatch):
        _clear_setting(db, 'episode_log_retention_days')
        monkeypatch.setenv('EPISODE_LOG_RETENTION_DAYS', '7')
        assert resolve_episode_log_retention_days(db) == 7

    def test_db_value_beats_the_env_seed(self, db, monkeypatch):
        monkeypatch.setenv('EPISODE_LOG_RETENTION_DAYS', '7')
        db.set_setting('episode_log_retention_days', '90', is_default=False)
        assert resolve_episode_log_retention_days(db) == 90

    def test_value_is_clamped_to_the_allowed_range(self, db):
        db.set_setting('episode_log_retention_days', '5000', is_default=False)
        assert resolve_episode_log_retention_days(db) == EPISODE_LOG_RETENTION_DAYS_MAX
        db.set_setting('episode_log_retention_days', '-4', is_default=False)
        assert resolve_episode_log_retention_days(db) == 0

    def test_junk_falls_back_to_the_default(self, db):
        db.set_setting('episode_log_retention_days', 'forever', is_default=False)
        assert resolve_episode_log_retention_days(db) == 30

    def test_junk_env_seed_falls_back_to_the_default(self, db, monkeypatch):
        monkeypatch.setenv('EPISODE_LOG_RETENTION_DAYS', 'soon')
        assert resolve_episode_log_retention_days(db) == 30


class TestLogLevel:
    def test_default_is_debug(self, db):
        assert resolve_episode_log_level(db) == logging.DEBUG

    def test_info_setting_maps_to_info(self, db):
        db.set_setting('episode_log_level', 'info', is_default=False)
        assert resolve_episode_log_level(db) == logging.INFO

    def test_env_seed_is_used_when_unset(self, db, monkeypatch):
        _clear_setting(db, 'episode_log_level')
        monkeypatch.setenv('EPISODE_LOG_LEVEL', 'info')
        assert resolve_episode_log_level(db) == logging.INFO

    def test_junk_falls_back_to_debug(self, db):
        db.set_setting('episode_log_level', 'trace', is_default=False)
        assert resolve_episode_log_level(db) == logging.DEBUG


class TestHandleIsolation:
    """The passed handle is the only source read; a broken one falls to the
    env default rather than quietly answering from the singleton."""

    def test_a_failing_handle_falls_back_to_the_default(self, db):
        import sqlite3

        class Broken:
            def get_setting(self, key):
                raise sqlite3.OperationalError('database is locked')

        db.set_setting('episode_log_retention_days', '90', is_default=False)
        db.set_setting('episode_log_level', 'info', is_default=False)

        assert resolve_episode_log_retention_days(Broken()) == 30
        assert resolve_episode_log_level(Broken()) == logging.DEBUG

    def test_the_handle_is_required(self):
        import pytest as _pytest

        with _pytest.raises(TypeError):
            resolve_episode_log_retention_days()
        with _pytest.raises(TypeError):
            resolve_episode_log_level()


class TestStorageResolution:
    def test_enabled_by_default_for_a_feed_with_no_override(self, db):
        assert resolve_episode_log_storage(db, {'episode_logs': None}) is True

    def test_missing_row_still_follows_the_global(self, db):
        assert resolve_episode_log_storage(db, None) is True

    def test_feed_off_beats_the_global(self, db):
        assert resolve_episode_log_storage(db, {'episode_logs': EPISODE_LOGS_OFF}) is False

    def test_feed_on_is_stored(self, db):
        assert resolve_episode_log_storage(db, {'episode_logs': EPISODE_LOGS_ON}) is True

    def test_retention_zero_beats_feed_on(self, db):
        db.set_setting('episode_log_retention_days', '0', is_default=False)
        assert resolve_episode_log_storage(db, {'episode_logs': EPISODE_LOGS_ON}) is False
        assert resolve_episode_log_storage(db, {'episode_logs': None}) is False

    def test_unknown_feed_value_follows_the_global(self, db):
        assert resolve_episode_log_storage(db, {'episode_logs': 'maybe'}) is True

    def test_allowed_values(self):
        assert EPISODE_LOGS_VALUES == (EPISODE_LOGS_ON, EPISODE_LOGS_OFF)


class TestSchema:
    def test_podcasts_table_has_the_override_column(self, db):
        cols = {row[1] for row in
                db.get_connection().execute('PRAGMA table_info(podcasts)')}
        assert 'episode_logs' in cols

    def test_existing_database_gains_the_column(self, tmp_path):
        import sqlite3
        legacy = tmp_path / 'legacy'
        legacy.mkdir()
        conn = sqlite3.connect(legacy / 'podcast.db')
        conn.execute("""CREATE TABLE podcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            rss_url TEXT NOT NULL,
            title TEXT)""")
        conn.execute("INSERT INTO podcasts (slug, rss_url, title) "
                     "VALUES ('kept', 'https://example.com/f.xml', 'Kept')")
        conn.commit()
        conn.close()

        Database._instance = None
        handle = Database(data_dir=str(legacy))
        try:
            cols = {row[1] for row in
                    handle.get_connection().execute('PRAGMA table_info(podcasts)')}
            assert 'episode_logs' in cols
            rows = handle.get_connection().execute(
                'SELECT slug FROM podcasts').fetchall()
            assert [r[0] for r in rows] == ['kept']
        finally:
            Database._instance = None


class TestUpdateColumn:
    def test_update_podcast_persists_the_override(self, db):
        db.create_podcast('log-feed', 'https://example.com/feed.xml', 'Log Feed')
        assert db.update_podcast('log-feed', episode_logs=EPISODE_LOGS_OFF) is True
        assert db.get_podcast_by_slug('log-feed')['episode_logs'] == EPISODE_LOGS_OFF
        db.update_podcast('log-feed', episode_logs=None)
        assert db.get_podcast_by_slug('log-feed')['episode_logs'] is None
