"""Staggered feed refresh: due-feed selection, the dashboard freshness
indicator, and the per-tick batch size."""
from datetime import datetime, timedelta, timezone

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('staggered_refresh_test_')

from main_app.background import _due_batch_size  # noqa: E402
from config import FEED_REFRESH_OUTAGE_MIN_FEEDS  # noqa: E402
from utils.time import ISO_FORMAT  # noqa: E402


def _iso_ago(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime(ISO_FORMAT)


def _iso_ahead(seconds):
    return _iso_ago(-seconds)


class TestDueFeedSelection:
    def test_only_feeds_past_their_next_refresh_are_due(self, temp_db):
        temp_db.create_podcast('fresh', 'https://example.com/fresh.xml', title='Fresh')
        temp_db.update_podcast('fresh', last_refresh_attempt_at=_iso_ago(60),
                               next_refresh_at=_iso_ahead(840))
        temp_db.create_podcast('stale', 'https://example.com/stale.xml', title='Stale')
        temp_db.update_podcast('stale', last_refresh_attempt_at=_iso_ago(3600),
                               next_refresh_at=_iso_ago(2700))

        assert temp_db.get_due_feed_slugs(limit=10) == ['stale']

    def test_a_failed_feed_is_not_due_again_until_its_retry(self, temp_db):
        temp_db.create_podcast('failing', 'https://example.com/f.xml', title='Failing')
        temp_db.update_podcast('failing', last_refresh_attempt_at=_iso_ago(60),
                               next_refresh_at=_iso_ahead(3000))

        assert temp_db.get_due_feed_slugs(limit=10) == []

    def test_unscheduled_feeds_are_due_first(self, temp_db):
        temp_db.create_podcast('old', 'https://example.com/old.xml', title='Old')
        temp_db.update_podcast('old', last_refresh_attempt_at=_iso_ago(3600),
                               next_refresh_at=_iso_ago(2700))
        temp_db.create_podcast('new-feed', 'https://example.com/new.xml', title='New')

        due = temp_db.get_due_feed_slugs(limit=10)

        assert due[0] == 'new-feed'
        assert set(due) == {'new-feed', 'old'}

    def test_limit_caps_the_batch_most_overdue_first(self, temp_db):
        for i in range(5):
            slug = f'feed-{i}'
            temp_db.create_podcast(slug, f'https://example.com/{i}.xml', title=slug)
            temp_db.update_podcast(slug, last_refresh_attempt_at=_iso_ago(3600 + i),
                                   next_refresh_at=_iso_ago(2700 + i))

        assert temp_db.get_due_feed_slugs(limit=2) == ['feed-4', 'feed-3']

    def test_local_and_recents_feeds_are_never_due(self, temp_db):
        temp_db.create_podcast('local', 'https://example.com/l.xml', title='Local',
                               feed_type='local')
        temp_db.create_podcast('recents', 'https://example.com/r.xml', title='Recents',
                               feed_type='recents')

        assert temp_db.get_due_feed_slugs(limit=10) == []


class TestAdaptiveRefreshBaseline:
    GATE = 'adaptive_refresh_baseline_v1'

    @pytest.fixture
    def legacy_path(self, tmp_path):
        from database import Database
        Database._instance = None
        db = Database(data_dir=str(tmp_path))
        db.set_setting('rss_refresh_interval_minutes', '10', is_default=False)
        for slug, attempt in (('never', None), ('recent', _iso_ago(120)), ('stale', _iso_ago(7200))):
            db.create_podcast(slug, f'https://example.com/{slug}.xml', title=slug)
            db.update_podcast(slug, last_refresh_attempt_at=attempt)
        conn = db.get_connection()
        conn.execute("UPDATE podcasts SET next_refresh_at = NULL")
        conn.execute("DELETE FROM schema_migrations WHERE name = ?", (self.GATE,))
        conn.commit()
        yield tmp_path
        Database._instance = None

    @staticmethod
    def _reopen(path):
        from database import Database
        Database._instance = None
        return Database(data_dir=str(path))

    def test_existing_feeds_become_due_as_before_in_batches(self, legacy_path):
        db = self._reopen(legacy_path)
        assert db.get_podcast_row('never')['next_refresh_at'] < _iso_ago(10_000_000)
        assert db.get_podcast_row('recent')['next_refresh_at'] == _iso_ahead(480)
        assert db.get_due_feed_slugs(limit=10) == ['never', 'stale']
        assert db.get_due_feed_slugs(limit=1) == ['never']

    def test_baseline_runs_once(self, legacy_path):
        db = self._reopen(legacy_path)
        conn = db.get_connection()
        conn.execute("UPDATE podcasts SET next_refresh_at = NULL WHERE slug = 'recent'")
        conn.commit()
        db = self._reopen(legacy_path)
        assert db.get_podcast_row('recent')['next_refresh_at'] is None
        assert db.get_connection().execute(
            "SELECT 1 FROM schema_migrations WHERE name = ?", (self.GATE,)).fetchone()


class TestFreshnessIndicators:
    def test_min_ignores_feeds_that_never_succeeded(self, temp_db):
        # A never-succeeded feed must not blank the dashboard for the rest.
        temp_db.create_podcast('a', 'https://example.com/a.xml', title='A')
        temp_db.update_podcast('a', last_checked_at=_iso_ago(60))
        temp_db.create_podcast('b', 'https://example.com/b.xml', title='B')  # never succeeded

        assert temp_db.get_feeds_min_last_checked_at() == _iso_ago(60)

    def test_min_is_the_oldest_successful_refresh(self, temp_db):
        older = _iso_ago(600)
        temp_db.create_podcast('a', 'https://example.com/a.xml', title='A')
        temp_db.update_podcast('a', last_checked_at=older)
        temp_db.create_podcast('b', 'https://example.com/b.xml', title='B')
        temp_db.update_podcast('b', last_checked_at=_iso_ago(60))

        assert temp_db.get_feeds_min_last_checked_at() == older

    def test_min_none_when_no_feed_has_succeeded(self, temp_db):
        temp_db.create_podcast('b', 'https://example.com/b.xml', title='B')
        assert temp_db.get_feeds_min_last_checked_at() is None

    def test_max_is_the_most_recent_successful_refresh(self, temp_db):
        temp_db.create_podcast('a', 'https://example.com/a.xml', title='A')
        temp_db.update_podcast('a', last_checked_at=_iso_ago(600))
        temp_db.create_podcast('b', 'https://example.com/b.xml', title='B')
        newest = _iso_ago(60)
        temp_db.update_podcast('b', last_checked_at=newest)

        # One stale feed does not blank the health-panel value.
        assert temp_db.get_feeds_last_successful_refresh_at() == newest


class TestRefreshDueFeeds:
    def test_no_due_feeds_short_circuits_without_a_batch(self, monkeypatch):
        import main_app.feeds as feeds
        from unittest.mock import MagicMock

        monkeypatch.setattr(feeds.db, 'get_due_feed_slugs', lambda *_a, **_k: [])
        batch = MagicMock()
        monkeypatch.setattr(feeds, '_run_refresh_batch', batch)

        result = feeds.refresh_due_feeds(3)

        batch.assert_not_called()
        assert result['success'] is True and result['failed'] == 0

    def test_forwards_due_slugs_to_the_batch_runner(self, monkeypatch):
        import main_app.feeds as feeds
        from unittest.mock import MagicMock

        monkeypatch.setattr(feeds.db, 'get_due_feed_slugs',
                            lambda *_a, **_k: ['x', 'y'])
        batch = MagicMock(return_value={'ok': True})
        monkeypatch.setattr(feeds, '_run_refresh_batch', batch)

        feeds.refresh_due_feeds(2)

        assert batch.call_args.kwargs['slugs'] == ['x', 'y']
        assert batch.call_args.args[0] is False  # never a forced refetch


class TestDueBatchSize:
    def test_spreads_feeds_across_the_interval(self):
        # 30 feeds, 900s interval, 60s tick -> 15 ticks -> ceil(30/15) = 2,
        # floored at the outage sample size.
        assert _due_batch_size(900, 30) == max(2, FEED_REFRESH_OUTAGE_MIN_FEEDS)

    def test_floored_at_the_outage_sample_size(self):
        assert _due_batch_size(900, 1) == FEED_REFRESH_OUTAGE_MIN_FEEDS

    def test_large_backlog_scales_up(self):
        # 900 feeds / 15 ticks = 60 per tick.
        assert _due_batch_size(900, 900) == 60

    def test_sub_tick_interval_covers_everything_each_tick(self):
        # An interval shorter than one tick still refreshes the whole set.
        assert _due_batch_size(30, 100) == 100
