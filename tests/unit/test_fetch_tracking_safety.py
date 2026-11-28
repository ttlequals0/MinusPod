"""Upgrade baseline, clock boundaries and client-fetch rules for dormant feeds and unfetched retention."""
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap(
    'fetch_tracking_safety_', passphrase='fetch-tracking-safety-passphrase')

from database import Database
from main_app import app, background, db as main_db
import main_app.routes as routes_module
from utils.time import ISO_FORMAT

NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
GATE = 'fetch_tracking_baseline_v1'


def _ago(days, now=NOW):
    return (now - timedelta(days=days)).strftime(ISO_FORMAT)


@pytest.fixture
def legacy_db(tmp_path):
    """A database in the pre-upgrade shape: no tracking columns, no baseline gate."""
    Database._instance = None
    db = Database(data_dir=str(tmp_path))
    db.create_podcast('old-show', 'https://example.com/old.xml', 'Old Show')
    db.upsert_episode('old-show', 'ep1', original_url='https://example.com/1.mp3',
                      title='Ep 1', status='processed')
    conn = db.get_connection()
    conn.execute("UPDATE podcasts SET created_at = ?", (_ago(400),))
    conn.execute("UPDATE episodes SET processed_file = 'ep1.mp3', processed_at = ?", (_ago(90),))
    conn.execute("ALTER TABLE podcasts DROP COLUMN last_feed_request_at")
    conn.execute("ALTER TABLE episodes DROP COLUMN first_fetched_at")
    conn.execute("DELETE FROM schema_migrations WHERE name = ?", (GATE,))
    conn.commit()
    yield tmp_path
    Database._instance = None


class TestUpgradeBaseline:
    def _reopen(self, path):
        Database._instance = None
        return Database(data_dir=str(path))

    def test_old_feed_is_not_dormant_after_upgrade(self, legacy_db):
        db = self._reopen(legacy_db)
        db.set_setting('dormant_feed_days', '30', is_default=False)
        podcast = db.get_podcast_row('old-show')
        assert podcast['last_feed_request_at'] is not None
        assert db.is_feed_dormant(podcast) is False
        assert db.is_auto_process_enabled_for_podcast('old-show') is True

    def test_unknown_fetch_state_deletes_nothing(self, legacy_db):
        db = self._reopen(legacy_db)
        db.set_setting('retention_days', '30', is_default=False)
        db.set_setting('unfetched_retention_days', '365', is_default=False)
        storage = MagicMock()
        storage.cleanup_episode_files.return_value = 0
        db.cleanup_old_episodes(storage=storage)
        assert db.get_episode('old-show', 'ep1')['status'] == 'processed'
        storage.cleanup_episode_files.assert_not_called()

    def test_baseline_runs_once(self, legacy_db):
        db = self._reopen(legacy_db)
        first = db.get_podcast_row('old-show')['last_feed_request_at']
        conn = db.get_connection()
        conn.execute("UPDATE podcasts SET last_feed_request_at = ?", (_ago(5),))
        conn.commit()
        db = self._reopen(legacy_db)
        assert db.get_podcast_row('old-show')['last_feed_request_at'] == _ago(5)
        assert first is not None
        assert db.get_connection().execute(
            "SELECT 1 FROM schema_migrations WHERE name = ?", (GATE,)).fetchone()

    def test_failed_baseline_retries_next_start(self, legacy_db):
        with patch.object(Database, '_run_fetch_tracking_baseline',
                          lambda self, conn, gate=GATE: None):
            db = self._reopen(legacy_db)
        assert db.get_podcast_row('old-show')['last_feed_request_at'] is None
        db = self._reopen(legacy_db)
        assert db.get_podcast_row('old-show')['last_feed_request_at'] is not None


class TestDormancyBoundary:
    @pytest.fixture
    def db(self, tmp_path):
        Database._instance = None
        instance = Database(data_dir=str(tmp_path))
        instance.set_setting('dormant_feed_days', '30', is_default=False)
        yield instance
        Database._instance = None

    def _dormant(self, db, stamp):
        with patch('database.queue.utc_now', return_value=NOW):
            return db.is_feed_dormant(
                {'feed_type': 'subscribed', 'last_feed_request_at': stamp, 'created_at': _ago(999)})

    def test_exactly_n_days_is_active(self, db):
        assert self._dormant(db, _ago(30)) is False

    def test_one_second_past_n_days_is_dormant(self, db):
        stamp = (NOW - timedelta(days=30, seconds=1)).strftime(ISO_FORMAT)
        assert self._dormant(db, stamp) is True

    def test_off_when_zero(self, db):
        db.set_setting('dormant_feed_days', '0', is_default=False)
        assert self._dormant(db, _ago(900)) is False


class TestClientFetchRules:
    @pytest.fixture
    def feed(self, request):
        slug = f"track-{request.node.name.replace('_', '-')[:40]}"
        main_db.create_podcast(slug, f'https://example.com/{slug}.xml', 'Track')
        routes_module._feed_request_stamped_at.pop(slug, None)
        yield slug
        main_db.delete_podcast(slug)

    def _note(self, slug, **request_kwargs):
        with app.test_request_context('/', **request_kwargs):
            routes_module._note_feed_request(slug)

    def test_get_counts(self, feed):
        self._note(feed)
        assert main_db.get_podcast_row(feed)['last_feed_request_at'] is not None

    def test_head_does_not_count(self, feed):
        self._note(feed, method='HEAD')
        assert main_db.get_podcast_row(feed)['last_feed_request_at'] is None

    def test_same_origin_ui_fetch_does_not_count(self, feed):
        self._note(feed, headers={'Sec-Fetch-Site': 'same-origin'})
        assert main_db.get_podcast_row(feed)['last_feed_request_at'] is None

    def test_cross_site_browser_client_counts(self, feed):
        self._note(feed, headers={'Sec-Fetch-Site': 'cross-site'})
        assert main_db.get_podcast_row(feed)['last_feed_request_at'] is not None

    def test_stamp_failure_never_breaks_serving(self, feed):
        with patch.object(main_db, 'touch_feed_request', side_effect=RuntimeError('locked')):
            self._note(feed)

    def test_same_origin_audio_fetch_does_not_stamp(self):
        with app.test_request_context('/', headers={'Sec-Fetch-Site': 'same-origin'}), \
                patch.object(main_db, 'mark_episode_fetched') as mark:
            routes_module._note_episode_fetch('s', 'e')
        mark.assert_not_called()


class TestManualProcessingOnDormantFeed:
    def test_user_reprocess_runs_on_dormant_feed(self, monkeypatch):
        slug = 'dormant-manual'
        main_db.create_podcast(slug, 'https://example.com/m.xml', 'Manual')
        main_db.set_setting('dormant_feed_days', '30', is_default=False)
        try:
            conn = main_db.get_connection()
            conn.execute("UPDATE podcasts SET last_feed_request_at = ? WHERE slug = ?",
                         (_ago(400, datetime.now(timezone.utc)), slug))
            conn.commit()
            main_db.upsert_episode(slug, 'ep1', original_url='https://example.com/1.mp3',
                                   title='Ep', status='discovered',
                                   reprocess_requested_at='2026-01-01T00:00:00Z')
            started = []
            monkeypatch.setattr(background.db, 'close_claimed_queue_row', lambda *a, **k: None)
            import main_app.processing as processing
            monkeypatch.setattr(processing, 'start_background_processing',
                                lambda *a, **k: started.append(a) or (True, 'ok'))
            claim = {'id': 1, 'podcast_slug': slug, 'episode_id': 'ep1',
                     'original_url': 'https://example.com/1.mp3', 'title': 'Ep'}
            monkeypatch.setattr(background, '_wait_for_claimed_episode', lambda *a, **k: None)
            running = set()
            assert background._run_claimed_episode(claim, running) == 'started'
            for waiter in running:
                waiter.join(timeout=5)
            assert started
        finally:
            main_db.set_setting('dormant_feed_days', '0', is_default=False)
            main_db.delete_podcast(slug)


class TestRetentionProtections:
    @pytest.fixture
    def db(self, tmp_path):
        Database._instance = None
        instance = Database(data_dir=str(tmp_path))
        instance.set_setting('retention_days', '30', is_default=False)
        instance.set_setting('unfetched_retention_days', '60', is_default=False)
        yield instance
        Database._instance = None

    def _seed(self, db, slug, ep, status, days, feed_type=None):
        if not db.get_podcast_by_slug(slug):
            db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
        db.upsert_episode(slug, ep, original_url=f'https://example.com/{ep}.mp3',
                          title=ep, status=status)
        conn = db.get_connection()
        conn.execute("UPDATE episodes SET processed_file = ?, processed_at = ? WHERE episode_id = ?",
                     (f'{ep}.mp3', _ago(days, datetime.now(timezone.utc)), ep))
        if feed_type:
            conn.execute("UPDATE podcasts SET feed_type = ? WHERE slug = ?", (feed_type, slug))
        conn.commit()

    def _sweep(self, db):
        storage = MagicMock()
        storage.cleanup_episode_files.return_value = 0
        db.cleanup_old_episodes(storage=storage)

    def test_unfetched_boundary(self, db):
        self._seed(db, 'a', 'inside', 'processed', 59)
        self._seed(db, 'a', 'outside', 'processed', 61)
        self._sweep(db)
        assert db.get_episode('a', 'inside')['status'] == 'processed'
        assert db.get_episode('a', 'outside')['status'] == 'discovered'

    def test_outside_episode_can_be_reprocessed(self, db):
        self._seed(db, 'a', 'outside', 'processed', 61)
        self._sweep(db)
        episode = db.get_episode('a', 'outside')
        assert episode['status'] == 'discovered' and not episode['processed_file']

    @pytest.mark.parametrize('status', ['pending', 'processing'])
    def test_queued_or_processing_never_swept(self, db, status):
        self._seed(db, 'a', 'busy', status, 400)
        self._sweep(db)
        assert db.get_episode('a', 'busy')['status'] == status

    def test_local_feed_never_swept(self, db):
        self._seed(db, 'loc', 'ep', 'processed', 400, feed_type='local')
        self._sweep(db)
        assert db.get_episode('loc', 'ep')['status'] == 'processed'

    def test_archive_override_never_swept(self, db):
        self._seed(db, 'arc', 'ep', 'processed', 400)
        db.get_connection().execute("UPDATE podcasts SET retention_days_override = 0 WHERE slug = 'arc'")
        db.get_connection().commit()
        self._sweep(db)
        assert db.get_episode('arc', 'ep')['status'] == 'processed'


class TestNewAudioStartsUnfetched:
    def test_finalize_clears_the_previous_fetch_stamp(self):
        from main_app.pipeline import finalize
        main_db.create_podcast('refinalize', 'https://example.com/r.xml', 'Refinalize')
        try:
            main_db.upsert_episode('refinalize', 'a1b2c3d4e5f6', original_url='https://example.com/r.mp3',
                                   title='Ep', status='processed', processed_file='episodes/r.mp3')
            assert main_db.mark_episode_fetched('refinalize', 'a1b2c3d4e5f6') is True
            finalize._persist_episode_state(
                'refinalize', 'a1b2c3d4e5f6', 1, 0, 1, 100.0, 90.0, 2,
                approval=finalize._approval_stamp('refinalize', 'a1b2c3d4e5f6', False))
            row = main_db.get_episode('refinalize', 'a1b2c3d4e5f6')
            assert row['first_fetched_at'] is None
            assert main_db.mark_episode_fetched('refinalize', 'a1b2c3d4e5f6') is True
        finally:
            main_db.delete_podcast('refinalize')
