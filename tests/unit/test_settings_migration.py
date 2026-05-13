"""Tests for the ad_detection_max_tokens -> detection_max_tokens settings migration."""
import sqlite3
import tempfile
import os

import pytest

from database import Database


@pytest.fixture
def fresh_db_dir():
    d = tempfile.mkdtemp(prefix="minuspod_migration_test_")
    Database._instance = None
    yield d
    Database._instance = None
    # cleanup
    import shutil
    shutil.rmtree(d, ignore_errors=True)


class TestAdDetectionMaxTokensMigration:
    def test_old_key_present_migrates_to_new_key(self, fresh_db_dir):
        # Seed pre-existing settings row with the old key
        db_path = os.path.join(fresh_db_dir, "podcast.db")
        # Trigger first-time init so the schema exists
        db1 = Database(data_dir=fresh_db_dir)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO settings (key, value, is_default) VALUES (?, ?, ?)",
                ('ad_detection_max_tokens', '2048', 0),
            )
            # Clear the new key so migration can move into it.
            conn.execute("DELETE FROM settings WHERE key = ?", ('detection_max_tokens',))
            conn.commit()

        # Force a fresh init pass (singleton reset triggers _init_db again)
        Database._instance = None
        db2 = Database(data_dir=fresh_db_dir)

        # New key should now hold the old value, old key gone.
        assert db2.get_setting('detection_max_tokens') == '2048'
        assert db2.get_setting('ad_detection_max_tokens') is None

    def test_no_old_key_is_noop(self, fresh_db_dir):
        Database._instance = None
        db = Database(data_dir=fresh_db_dir)
        assert db.get_setting('ad_detection_max_tokens') is None
        # Migration ran without error.

    def test_both_keys_present_drops_old_keeps_new(self, fresh_db_dir):
        # When both keys somehow co-exist (e.g. a manual seed plus a partial
        # prior migration), the new key's value wins and the old key is dropped.
        db_path = os.path.join(fresh_db_dir, "podcast.db")
        Database(data_dir=fresh_db_dir)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO settings (key, value, is_default) VALUES (?, ?, ?)",
                ('ad_detection_max_tokens', '1024', 0),
            )
            conn.execute(
                "INSERT OR REPLACE INTO settings (key, value, is_default) VALUES (?, ?, ?)",
                ('detection_max_tokens', '8192', 0),
            )
            conn.commit()

        Database._instance = None
        db2 = Database(data_dir=fresh_db_dir)
        assert db2.get_setting('detection_max_tokens') == '8192'  # new key wins
        assert db2.get_setting('ad_detection_max_tokens') is None  # old key gone

    def test_idempotent_on_second_init(self, fresh_db_dir):
        db_path = os.path.join(fresh_db_dir, "podcast.db")
        db1 = Database(data_dir=fresh_db_dir)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "INSERT INTO settings (key, value, is_default) VALUES (?, ?, ?)",
                ('ad_detection_max_tokens', '8192', 0),
            )
            conn.execute("DELETE FROM settings WHERE key = ?", ('detection_max_tokens',))
            conn.commit()

        Database._instance = None
        Database(data_dir=fresh_db_dir)
        Database._instance = None
        db3 = Database(data_dir=fresh_db_dir)
        assert db3.get_setting('detection_max_tokens') == '8192'
        assert db3.get_setting('ad_detection_max_tokens') is None
