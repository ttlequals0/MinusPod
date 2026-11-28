"""Tests for unfetched-episode retention: never-downloaded processed episodes
outlive the normal retention window, and downloaded ones get a grace period
after their first download."""
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from database import Database


def _days_ago(days):
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')


def _seed(db, slug, ep_id, processed_days_ago, fetched_days_ago=None,
          status='processed', original_file=None):
    if not db.get_podcast_by_slug(slug):
        db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    db.upsert_episode(slug, ep_id, original_url=f'https://example.com/{ep_id}.mp3',
                      title=ep_id, status=status)
    conn = db.get_connection()
    conn.execute(
        "UPDATE episodes SET processed_file = ?, original_file = ?, processed_at = ?, "
        "first_fetched_at = ?, status = ? WHERE episode_id = ?",
        (f'{ep_id}.mp3', original_file, _days_ago(processed_days_ago),
         _days_ago(fetched_days_ago) if fetched_days_ago is not None else None,
         status, ep_id),
    )
    conn.commit()


def _swept(db):
    """Episode ids the sweep resets, using a storage stub."""
    storage = MagicMock()
    storage.cleanup_episode_files.return_value = 0
    storage.delete_original_only.return_value = (True, 0)
    before = {row['episode_id'] for row in db.get_connection().execute(
        "SELECT episode_id FROM episodes WHERE status != 'discovered'")}
    db.cleanup_old_episodes(storage=storage)
    after = {row['episode_id'] for row in db.get_connection().execute(
        "SELECT episode_id FROM episodes WHERE status != 'discovered'")}
    return before - after, storage


@pytest.fixture
def db(tmp_path):
    Database._instance = None
    instance = Database(data_dir=str(tmp_path))
    instance.set_setting('retention_days', '30', is_default=False)
    instance.set_setting('keep_original_audio', 'true', is_default=False)
    yield instance
    Database._instance = None


class TestSweep:
    def test_off_by_default_sweeps_unfetched(self, db):
        _seed(db, 'show', 'old-unfetched', processed_days_ago=40)
        swept, _ = _swept(db)
        assert swept == {'old-unfetched'}

    def test_unfetched_kept_until_extended_window(self, db):
        db.set_setting('unfetched_retention_days', '90', is_default=False)
        _seed(db, 'show', 'unfetched-40', processed_days_ago=40)
        _seed(db, 'show', 'unfetched-100', processed_days_ago=100)
        swept, _ = _swept(db)
        assert swept == {'unfetched-100'}

    def test_fetched_swept_after_grace(self, db):
        db.set_setting('unfetched_retention_days', '90', is_default=False)
        db.set_setting('fetched_retention_grace_days', '3', is_default=False)
        _seed(db, 'show', 'fetched-long-ago', processed_days_ago=40, fetched_days_ago=35)
        _seed(db, 'show', 'fetched-yesterday', processed_days_ago=40, fetched_days_ago=1)
        swept, _ = _swept(db)
        assert swept == {'fetched-long-ago'}

    def test_grace_outlasts_extended_window(self, db):
        # Fetched on the last day of the extended window: still kept for grace.
        db.set_setting('unfetched_retention_days', '90', is_default=False)
        db.set_setting('fetched_retention_grace_days', '3', is_default=False)
        _seed(db, 'show', 'late-fetch', processed_days_ago=91, fetched_days_ago=1)
        swept, _ = _swept(db)
        assert swept == set()

    def test_zero_grace_follows_normal_window(self, db):
        db.set_setting('unfetched_retention_days', '90', is_default=False)
        db.set_setting('fetched_retention_grace_days', '0', is_default=False)
        _seed(db, 'show', 'fetched', processed_days_ago=40, fetched_days_ago=0)
        swept, _ = _swept(db)
        assert swept == {'fetched'}

    def test_never_shortens_normal_window(self, db):
        db.set_setting('unfetched_retention_days', '10', is_default=False)
        _seed(db, 'show', 'unfetched-20', processed_days_ago=20)
        _seed(db, 'show', 'fetched-20', processed_days_ago=20, fetched_days_ago=19)
        swept, _ = _swept(db)
        assert swept == set()

    def test_extension_uses_per_feed_window(self, db):
        # Per-feed 7-day window; extension (60) still applies on top of it.
        db.set_setting('unfetched_retention_days', '60', is_default=False)
        _seed(db, 'short', 'unfetched-20', processed_days_ago=20)
        _seed(db, 'short', 'fetched-20', processed_days_ago=20, fetched_days_ago=19)
        db.update_podcast('short', retention_days_override=7)
        swept, _ = _swept(db)
        assert swept == {'fetched-20'}

    def test_failed_episodes_not_extended(self, db):
        db.set_setting('unfetched_retention_days', '90', is_default=False)
        _seed(db, 'show', 'failed-40', processed_days_ago=40, status='failed')
        swept, _ = _swept(db)
        assert swept == {'failed-40'}

    def test_extended_episode_still_loses_original(self, db):
        db.set_setting('unfetched_retention_days', '90', is_default=False)
        db.set_setting('original_retention_days', '7', is_default=False)
        _seed(db, 'show', 'kept', processed_days_ago=40, original_file='kept-original.mp3')
        swept, storage = _swept(db)
        assert swept == set()
        storage.delete_original_only.assert_called_once_with('show', 'kept')

    def test_retention_reset_clears_fetch_stamp(self, db):
        _seed(db, 'show', 'old', processed_days_ago=40, fetched_days_ago=39)
        _swept(db)
        assert db.get_episode('show', 'old')['first_fetched_at'] is None


class TestMarkEpisodeFetched:
    def test_first_fetch_only(self, db):
        _seed(db, 'show', 'ep', processed_days_ago=1)
        assert db.mark_episode_fetched('show', 'ep') is True
        first = db.get_episode('show', 'ep')['first_fetched_at']
        assert first is not None
        assert db.mark_episode_fetched('show', 'ep') is False
        assert db.get_episode('show', 'ep')['first_fetched_at'] == first

    def test_unknown_episode(self, db):
        assert db.mark_episode_fetched('nope', 'nope') is False


class TestMigrationBackfill:
    def test_backfills_from_download_history(self, tmp_path):
        Database._instance = None
        db = Database(data_dir=str(tmp_path))
        _seed(db, 'show', 'downloaded', processed_days_ago=5)
        _seed(db, 'show', 'never', processed_days_ago=5)
        podcast_id = db.get_podcast_by_slug('show')['id']
        conn = db.get_connection()
        conn.execute(
            "INSERT INTO episode_downloads (podcast_id, episode_id, client_ip, "
            "first_seen_at, last_seen_at) VALUES (?, 'downloaded', '10.0.0.1', ?, ?)",
            (podcast_id, _days_ago(4), _days_ago(2)))
        conn.execute("ALTER TABLE episodes DROP COLUMN first_fetched_at")
        conn.commit()
        Database._instance = None
        db = Database(data_dir=str(tmp_path))
        try:
            assert db.get_episode('show', 'downloaded')['first_fetched_at'] == _days_ago(4)
            assert db.get_episode('show', 'never')['first_fetched_at'] is None
        finally:
            Database._instance = None
