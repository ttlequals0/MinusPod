"""Backoff for a feed whose body never parses."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import DEFAULT, MagicMock, patch

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('feed_parse_backoff_')
import main_app.feeds as feeds  # noqa: E402
from main_app.feeds import refresh_rss_feed  # noqa: E402


def _iso(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def _podcast_row(**overrides):
    row = {
        'id': 1, 'feed_url': 'https://example.com/rss', 'title': 'Example Show',
        'etag': '"abc123"', 'last_modified_header': None,
        'artwork_cached': True,
        'podping_checked_at': '2026-07-26T00:00:00Z',
        'channel_metadata_at': '2026-07-26T00:00:00Z',
        'parse_failure_count': 0, 'last_parse_failure_at': None,
    }
    row.update(overrides)
    return row


class TestParseBackoffSchedule(unittest.TestCase):
    def test_backoff_doubles_and_caps(self):
        self.assertEqual(feeds._parse_backoff_seconds(0), 0.0)
        self.assertEqual(feeds._parse_backoff_seconds(1),
                         feeds.PARSE_FAILURE_BACKOFF_BASE_SECONDS)
        self.assertEqual(feeds._parse_backoff_seconds(2),
                         feeds.PARSE_FAILURE_BACKOFF_BASE_SECONDS * 2)
        self.assertEqual(feeds._parse_backoff_seconds(20),
                         feeds.PARSE_FAILURE_BACKOFF_MAX_SECONDS)

    def test_remaining_is_zero_without_a_recorded_failure(self):
        self.assertEqual(feeds._parse_backoff_remaining(_podcast_row()), 0.0)

    def test_remaining_counts_down_from_the_last_failure(self):
        row = _podcast_row(
            parse_failure_count=1,
            last_parse_failure_at=_iso(datetime.now(timezone.utc)
                                       - timedelta(seconds=600)))
        remaining = feeds._parse_backoff_remaining(row)
        self.assertGreater(remaining, 0)
        self.assertLess(remaining, feeds.PARSE_FAILURE_BACKOFF_BASE_SECONDS)


class RefreshCase(unittest.TestCase):
    """One patch of the module collaborators every refresh test below needs."""

    def setUp(self):
        feeds._refresh_coalesce.invalidate()
        patcher = patch.multiple(
            'main_app.feeds', db=DEFAULT, rss_parser=DEFAULT, storage=DEFAULT,
            status_service=DEFAULT, pattern_service=DEFAULT)
        mocks = patcher.start()
        self.addCleanup(patcher.stop)
        self.db = mocks['db']
        self.rss_parser = mocks['rss_parser']
        self.storage = mocks['storage']

    def _rows(self, **overrides):
        row = _podcast_row(**overrides)
        self.db.get_podcast_row.return_value = row
        self.db.get_podcast_by_slug.return_value = row
        return row

    def _refresh(self, **kwargs):
        return refresh_rss_feed('example-podcast', 'https://example.com/rss',
                                **kwargs)


class SweepCase(unittest.TestCase):
    """One patch of the collaborators every refresh_all_feeds test needs."""

    def setUp(self):
        patcher = patch.multiple('main_app.feeds', db=DEFAULT, storage=DEFAULT,
                                 status_service=DEFAULT)
        mocks = patcher.start()
        self.addCleanup(patcher.stop)
        self.db = mocks['db']

    def _sweep(self, outcomes):
        self.db.get_podcast_by_slug.side_effect = lambda slug: {
            'id': 1, 'slug': slug, 'source_url': f'https://example.com/{slug}'}
        with patch.object(feeds, 'get_feed_map', return_value={
                    slug: {'in': f'https://example.com/{slug}'}
                    for slug in outcomes}), \
                patch.object(feeds, 'refresh_rss_feed',
                             side_effect=lambda slug, *a, **k: outcomes[slug]):
            return feeds.refresh_all_feeds()


class TestAttemptStamp(RefreshCase):
    def test_a_failed_refresh_still_stamps_the_attempt(self):
        # The attempt stamp is what keeps a failing feed from being re-selected
        # every tick under staggered refresh; it must land even when the fetch
        # raises, before last_checked_at (success-only) would be written.
        self._rows()
        self.rss_parser.fetch_feed_conditional.side_effect = RuntimeError('boom')

        outcome = self._refresh()

        assert outcome.success is False
        stamped = [c for c in self.db.update_podcast.call_args_list
                   if 'last_refresh_attempt_at' in c.kwargs]
        assert len(stamped) == 1
        assert stamped[0].kwargs['last_refresh_attempt_at'] is not None
        # No success write on a failed refresh.
        assert not [c for c in self.db.update_podcast.call_args_list
                    if 'last_checked_at' in c.kwargs]


class TestParseFailureRecording(RefreshCase):
    def test_unparseable_body_stamps_count_and_names_the_retry(self):
        self._rows(parse_failure_count=1)
        self.rss_parser.fetch_feed_conditional.return_value = ('<garbage', 'e', None)
        self.rss_parser.parse_feed.return_value = MagicMock(
            feed={}, entries=[], bozo=True)

        outcome = self._refresh()

        self.assertFalse(outcome.success)
        self.assertEqual(outcome.status, 'parse_failed')
        stamped = [c for c in self.db.update_podcast.call_args_list
                   if 'parse_failure_count' in c.kwargs]
        self.assertEqual(len(stamped), 1)
        self.assertEqual(stamped[0].kwargs['parse_failure_count'], 2)
        self.assertIsNotNone(stamped[0].kwargs['last_parse_failure_at'])
        self.assertIn('retrying a full fetch in', outcome.error)
        self.db.clear_parse_failure_state.assert_not_called()

    def test_a_deferred_failure_does_not_stamp_the_parse_counter(self):
        """refresh_all_feeds passes record_failure=False and rules on the batch
        itself; stamping here put every feed into backoff during a CDN incident."""
        self._rows(parse_failure_count=1)
        self.rss_parser.fetch_feed_conditional.return_value = ('<garbage', 'e', None)
        self.rss_parser.parse_feed.return_value = MagicMock(
            feed={}, entries=[], bozo=True)

        outcome = refresh_rss_feed('example-podcast', 'https://example.com/rss',
                                   False, False)

        self.assertEqual(outcome.status, 'parse_failed')
        self.assertEqual([c for c in self.db.update_podcast.call_args_list
                          if 'parse_failure_count' in c.kwargs], [])
        self.assertNotIn('retrying a full fetch in', outcome.error)

    def test_a_body_that_parses_ends_the_backoff(self):
        self._rows(parse_failure_count=2,
                   last_parse_failure_at=_iso(datetime.now(timezone.utc)))
        self.rss_parser.fetch_feed_conditional.return_value = ('<rss/>', 'e', None)
        self.rss_parser.parse_feed.return_value = MagicMock(
            feed={'title': 'Example Show'}, entries=[], bozo=False)

        self._refresh()

        self.db.clear_parse_failure_state.assert_called_once_with('example-podcast')


class TestTruncatedBodyRefetch(RefreshCase):
    """A body that fails to parse gets one immediate refetch before backoff."""

    def test_truncated_then_good_body_succeeds_without_a_backoff_stamp(self):
        self._rows(parse_failure_count=0)
        self.rss_parser.fetch_feed_conditional.side_effect = [
            ('<rss><trunc', 'e1', None),
            ('<rss/>', 'e2', None),
        ]
        self.rss_parser.parse_feed.side_effect = [
            None,
            MagicMock(feed={'title': 'Example Show'}, entries=[], bozo=False),
        ]
        self.rss_parser.find_channel_element.return_value = None
        self.rss_parser.resolve_channel_fields.return_value = {
            'title': 'Example Show', 'description': '', 'link': '',
            'language': 'en', 'author': '', 'categories': []}
        self.rss_parser.extract_podcast_artwork_url.return_value = None
        self.rss_parser.extract_podping_declaration.return_value = {
            'uses_podping': None, 'hive_accounts': []}
        self.rss_parser.extract_episodes.return_value = []
        self.db.bulk_upsert_discovered_episodes.return_value = 0
        self.db.get_episode_statuses_for_podcast.return_value = ({}, {})
        self.db.is_auto_process_enabled_for_podcast.return_value = False

        with patch.object(feeds, '_build_and_save_served_rss'):
            outcome = self._refresh()

        self.assertTrue(outcome.success)
        self.assertEqual(self.rss_parser.fetch_feed_conditional.call_count, 2)
        retry_kwargs = self.rss_parser.fetch_feed_conditional.call_args_list[1].kwargs
        self.assertIsNone(retry_kwargs['etag'])
        self.assertIsNone(retry_kwargs['last_modified'])
        self.assertEqual([c for c in self.db.update_podcast.call_args_list
                          if 'parse_failure_count' in c.kwargs], [])

    def test_truncated_twice_records_one_failure_and_backs_off(self):
        self._rows(parse_failure_count=0)
        self.rss_parser.fetch_feed_conditional.side_effect = [
            ('<rss><trunc', 'e1', None),
            ('<rss><trunc-again', 'e2', None),
        ]
        self.rss_parser.parse_feed.side_effect = [None, None]

        outcome = self._refresh()

        self.assertFalse(outcome.success)
        self.assertEqual(outcome.status, 'parse_failed')
        self.assertEqual(self.rss_parser.fetch_feed_conditional.call_count, 2)
        stamped = [c for c in self.db.update_podcast.call_args_list
                  if 'parse_failure_count' in c.kwargs]
        self.assertEqual(len(stamped), 1)
        self.assertEqual(stamped[0].kwargs['parse_failure_count'], 1)
        self.assertIn('retrying a full fetch in', outcome.error)


class TestBackoffHoldsTheFullFetch(RefreshCase):
    def test_stale_cache_refetch_waits_for_the_backoff(self):
        self._rows(parse_failure_count=1,
                   last_parse_failure_at=_iso(datetime.now(timezone.utc)))
        self.db.get_episodes.return_value = ([], 5)
        self.db.get_processed_episodes_for_feed.return_value = [
            {'episode_id': 'a1b2c3d4e5f6'}]
        self.storage.get_rss.return_value = '<rss/>'
        self.rss_parser.fetch_feed_conditional.return_value = (None, '"abc123"', None)

        outcome = self._refresh()

        self.assertFalse(outcome.success)
        self.assertEqual(outcome.status, 'parse_backoff')
        # The conditional GET still ran; only the full refetch was held.
        self.assertEqual(self.rss_parser.fetch_feed_conditional.call_count, 1)
        self.db.clear_refresh_failure_state.assert_not_called()

    def test_a_clean_304_is_still_served_during_the_backoff(self):
        """Nothing is re-downloaded, so the backoff must not block the 304 path."""
        self._rows(parse_failure_count=1,
                   last_parse_failure_at=_iso(datetime.now(timezone.utc)))
        self.db.get_episodes.return_value = ([], 5)
        self.db.get_processed_episodes_for_feed.return_value = []
        self.storage.get_rss.return_value = '<rss/>'
        self.rss_parser.fetch_feed_conditional.return_value = (None, '"abc123"', None)

        outcome = self._refresh()

        self.assertEqual(outcome.status, 'not_modified')
        # A 304 parses nothing, so it cannot end the backoff.
        self.db.clear_parse_failure_state.assert_not_called()

    def test_feed_without_validators_is_not_fetched_at_all(self):
        self._rows(etag=None, last_modified_header=None, parse_failure_count=2,
                   last_parse_failure_at=_iso(datetime.now(timezone.utc)))

        outcome = self._refresh()

        self.assertFalse(outcome.success)
        self.assertEqual(outcome.status, 'parse_backoff')
        self.rss_parser.fetch_feed_conditional.assert_not_called()
        # Attempt stamp up front, then last_checked_at on the deliberate skip.
        stamped = self.db.update_podcast.call_args_list
        self.assertEqual(len(stamped), 2)
        self.assertIn('last_refresh_attempt_at', stamped[0].kwargs)
        self.assertIn('last_checked_at', stamped[1].kwargs)
        self.assertNotIn('parse_failure_count', stamped[1].kwargs)

    def test_force_refresh_ignores_the_backoff(self):
        self._rows(etag=None, last_modified_header=None, parse_failure_count=2,
                   last_parse_failure_at=_iso(datetime.now(timezone.utc)))
        self.rss_parser.fetch_feed_conditional.return_value = ('<garbage', None, None)
        self.rss_parser.parse_feed.return_value = MagicMock(
            feed={}, entries=[], bozo=True)

        outcome = self._refresh(force=True)

        self.assertEqual(outcome.status, 'parse_failed')
        self.assertEqual(self.rss_parser.fetch_feed_conditional.call_count, 1)

    def test_stale_cache_refetch_resumes_once_the_backoff_elapses(self):
        stale = datetime.now(timezone.utc) - timedelta(
            seconds=feeds.PARSE_FAILURE_BACKOFF_BASE_SECONDS + 60)
        self._rows(parse_failure_count=1, last_parse_failure_at=_iso(stale))
        self.db.get_episodes.return_value = ([], 5)
        self.db.get_processed_episodes_for_feed.return_value = [
            {'episode_id': 'a1b2c3d4e5f6'}]
        self.storage.get_rss.return_value = '<rss/>'
        self.rss_parser.fetch_feed_conditional.side_effect = [
            (None, '"abc123"', None),
            ('<garbage', '"abc123"', None),
        ]
        self.rss_parser.parse_feed.return_value = MagicMock(
            feed={}, entries=[], bozo=True)

        outcome = self._refresh()

        self.assertEqual(outcome.status, 'parse_failed')
        self.assertEqual(self.rss_parser.fetch_feed_conditional.call_count, 2)


class TestClearOnSuccess:
    """On temp_db, not the session database: the feed this creates would
    otherwise outlive the module and land in another test's query."""

    def test_only_a_parse_clears_the_parse_state(self, temp_db):
        temp_db.create_podcast('clean-feed', 'https://example.com/rss',
                               'Example Show')
        temp_db.update_podcast('clean-feed', parse_failure_count=3,
                               last_parse_failure_at='2026-09-16T00:00:00Z',
                               refresh_failure_count=2)

        temp_db.clear_refresh_failure_state('clean-feed')
        row = temp_db.get_podcast_row('clean-feed')
        assert row['refresh_failure_count'] == 0
        assert row['parse_failure_count'] == 3

        temp_db.clear_parse_failure_state('clean-feed')
        row = temp_db.get_podcast_row('clean-feed')
        assert row['parse_failure_count'] == 0
        assert row['last_parse_failure_at'] is None


class TestASkippedFetchIsNotAFailedRefresh(SweepCase):
    """A held full fetch is the backoff doing its job. Counted as a failure it
    froze the sweep's completion timestamp and, on a small instance, looked
    like a shared outage."""

    def test_the_sweep_counts_it_as_neither_success_nor_failure(self):
        result = self._sweep({
            'a': feeds.RefreshOutcome(True, 'updated'),
            'b': feeds.RefreshOutcome(False, 'parse_backoff'),
        })

        assert result['failed'] == 0
        assert result['succeeded'] == 1
        assert result['success'] is True

    def test_a_backoff_only_sweep_completes_without_a_stamp(self):
        # parse_backoff is a skip, not a failure, so the sweep completes. The
        # dashboard freshness time is computed on read from MIN(last_checked_at)
        # now, so the sweep writes no completion stamp.
        result = self._sweep({'b': feeds.RefreshOutcome(False, 'parse_backoff')})

        assert result['failed'] == 0
        assert result['success'] is True
        stamped = [c for c in self.db.set_setting.call_args_list
                   if c.args and c.args[0] == 'feeds_last_refresh_completed_at']
        assert stamped == []

    def test_a_real_failure_is_still_counted(self):
        result = self._sweep({
            'a': feeds.RefreshOutcome(False, 'fetch_failed', error='boom'),
            'b': feeds.RefreshOutcome(False, 'parse_backoff'),
        })

        assert result['failed'] == 1
        assert result['success'] is False

    def test_a_single_feed_refresh_reports_it_as_handled(self):
        with patch.object(feeds, 'refresh_rss_feed',
                          return_value=feeds.RefreshOutcome(False, 'parse_backoff')):
            assert feeds.refresh_single_feed('example-podcast') is True

    def test_a_single_feed_refresh_still_reports_a_real_failure(self):
        with patch.object(feeds, 'refresh_rss_feed',
                          return_value=feeds.RefreshOutcome(False, 'fetch_failed')):
            assert feeds.refresh_single_feed('example-podcast') is False


class TestOutageIsJudgedOverAttemptedFeeds(SweepCase):
    """Feeds held in parse backoff were never attempted, so they are evidence
    neither for a shared outage nor against one."""

    def _outcomes(self, skipped, failed):
        outcomes = {f'skip-{i}': feeds.RefreshOutcome(False, 'parse_backoff')
                    for i in range(skipped)}
        outcomes.update({f'fail-{i}': feeds.RefreshOutcome(
            False, 'fetch_failed', error='boom') for i in range(failed)})
        return outcomes

    def test_every_attempted_feed_failing_is_an_outage(self):
        result = self._sweep(self._outcomes(skipped=6, failed=4))

        self.assertTrue(result['outage']['detected'])

    def test_too_few_attempted_feeds_to_judge_is_not_an_outage(self):
        result = self._sweep(self._outcomes(skipped=8, failed=2))

        self.assertFalse(result['outage']['detected'])

    def test_an_outage_does_not_put_feeds_into_parse_backoff(self):
        outcomes = {f'fail-{i}': feeds.RefreshOutcome(
            False, 'parse_failed', error='unparseable') for i in range(4)}
        with patch.object(feeds, '_record_parse_failure') as record:
            self._sweep(outcomes)

        record.assert_not_called()

    def test_a_lone_parse_failure_still_enters_backoff(self):
        outcomes = {'a': feeds.RefreshOutcome(True, 'updated'),
                    'b': feeds.RefreshOutcome(True, 'updated'),
                    'c': feeds.RefreshOutcome(True, 'updated'),
                    'd': feeds.RefreshOutcome(False, 'parse_failed',
                                              error='unparseable')}
        with patch.object(feeds, '_record_parse_failure') as record:
            self._sweep(outcomes)

        self.assertEqual(record.call_args.args[0], 'd')


if __name__ == '__main__':
    unittest.main()
