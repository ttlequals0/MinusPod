"""Tests for the processing_history.app_version column (2.78.4).

Covers: the additive migration on an existing DB (no data loss),
record_processing_history stamping the running app version, and a
fresh DB already having the column via TABLE_DDL.
"""
import sqlite3

import pytest

from version import __version__


@pytest.fixture
def legacy_db_path(tmp_path):
    """A processing_history table as it existed before 2.78.4 (no app_version)."""
    path = tmp_path / 'podcast.db'
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE podcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT UNIQUE NOT NULL,
            source_url TEXT NOT NULL,
            title TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE processing_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            podcast_id INTEGER NOT NULL,
            podcast_slug TEXT NOT NULL,
            podcast_title TEXT,
            episode_id TEXT NOT NULL,
            episode_title TEXT,
            processed_at TEXT,
            processing_duration_seconds REAL,
            status TEXT NOT NULL,
            ads_detected INTEGER DEFAULT 0,
            error_message TEXT,
            reprocess_number INTEGER DEFAULT 1
        )
    """)
    conn.execute(
        "INSERT INTO podcasts (slug, source_url, title) VALUES (?, ?, ?)",
        ('legacy-show', 'https://example.com/legacy.xml', 'Legacy Show'),
    )
    conn.execute(
        "INSERT INTO processing_history "
        "(podcast_id, podcast_slug, episode_id, processed_at, status, ads_detected) "
        "VALUES (1, 'legacy-show', 'ep1', '2026-01-01T00:00:00Z', 'completed', 3)"
    )
    conn.commit()
    conn.close()
    return path


def _column_names(conn, table):
    return {row['name'] for row in conn.execute(f"PRAGMA table_info({table})")}


class TestMigrationAddsAppVersionColumn:
    def test_existing_db_gains_column_rows_untouched(self, legacy_db_path, monkeypatch):
        from database import Database

        Database._instance = None
        monkeypatch.setenv('DATA_DIR', str(legacy_db_path.parent))
        db = Database(data_dir=str(legacy_db_path.parent))

        conn = db.get_connection()
        assert 'app_version' in _column_names(conn, 'processing_history')

        row = conn.execute(
            "SELECT * FROM processing_history WHERE episode_id = 'ep1'"
        ).fetchone()
        assert row['app_version'] is None
        assert row['status'] == 'completed'
        assert row['ads_detected'] == 3

        Database._instance = None


class TestRecordProcessingHistoryStampsVersion:
    def test_stamps_running_version(self, temp_db):
        temp_db.create_podcast('version-show', 'https://example.com/f.xml', 'Version Show')
        podcast = temp_db.get_podcast_by_slug('version-show')

        temp_db.record_processing_history(
            podcast_id=podcast['id'], podcast_slug='version-show',
            podcast_title='Version Show', episode_id='ep1', episode_title='One',
            status='completed', ads_detected=2,
        )

        runs = temp_db.get_episode_processing_runs(podcast['id'], 'ep1')
        assert runs[0]['app_version'] == __version__


class TestFreshDbHasColumn:
    def test_fresh_db_processing_history_has_app_version(self, temp_db):
        conn = temp_db.get_connection()
        assert 'app_version' in _column_names(conn, 'processing_history')
