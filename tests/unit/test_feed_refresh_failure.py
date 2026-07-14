"""Tests for the Feed Refresh Failed notification event (#516).

A feed whose origin RSS keeps failing to refresh should raise an operator
alert (webhook + email) once per outage. The alert is deduped per feed so
one broken feed does not suppress alerts for another.
"""
from unittest.mock import patch

import pytest

import email_service
import webhook_service
from webhook_service import (
    EVENT_FEED_REFRESH_FAILED,
    VALID_EVENTS,
    fire_feed_refresh_failed_event,
)
from tests.unit.thread_fakes import SyncThread


@pytest.fixture(autouse=True)
def _reset_alert_dedup():
    webhook_service._last_alert_time.clear()
    yield
    webhook_service._last_alert_time.clear()


class TestEventRegistration:
    def test_event_in_valid_events(self):
        assert EVENT_FEED_REFRESH_FAILED in VALID_EVENTS

    def test_email_formatter_registered(self):
        assert EVENT_FEED_REFRESH_FAILED in email_service.FORMATTERS

    def test_email_default_events_include_it(self):
        assert EVENT_FEED_REFRESH_FAILED in email_service.DEFAULT_EVENTS


class TestFireFeedRefreshFailedEvent:
    @staticmethod
    def _fire(slug='show-a'):
        fire_feed_refresh_failed_event(
            slug=slug,
            podcast_name='Show A',
            feed_url='https://example.com/feed.xml',
            error_message='connection refused',
            failure_count=3,
        )

    def test_dispatches_to_matching_webhook_and_email(self):
        webhook = {'enabled': True, 'events': [EVENT_FEED_REFRESH_FAILED],
                   'url': 'https://hooks.example.com/x'}
        with patch.object(webhook_service.threading, 'Thread', SyncThread), \
             patch.object(webhook_service, 'load_webhooks', return_value=[webhook]), \
             patch.object(webhook_service, '_prepare_and_dispatch') as dispatch, \
             patch.object(webhook_service.email_service, 'send_event_email') as email:
            self._fire()

        assert dispatch.call_count == 1
        ctx = dispatch.call_args[0][1]
        assert ctx['event'] == EVENT_FEED_REFRESH_FAILED
        assert ctx['slug'] == 'show-a'
        assert ctx['failure_count'] == 3
        assert ctx['error_message'] == 'connection refused'
        email.assert_called_once_with(EVENT_FEED_REFRESH_FAILED, ctx)

    def test_dedup_per_feed_with_burst_cap(self):
        """A second feed alerting moments later is held back by the burst
        cap (mass-outage flood protection); past the cap it fires, while
        the same feed stays suppressed by its own 5-minute dedup."""
        fake_now = [1_000_000.0]
        with patch.object(webhook_service.threading, 'Thread', SyncThread), \
             patch.object(webhook_service, 'load_webhooks', return_value=[]), \
             patch.object(webhook_service.time, 'time', lambda: fake_now[0]), \
             patch.object(webhook_service.email_service, 'send_event_email') as email:
            self._fire(slug='show-a')
            self._fire(slug='show-b')  # inside burst cap: suppressed
            assert email.call_count == 1

            fake_now[0] += webhook_service._ALERT_BURST_SECS + 1
            self._fire(slug='show-b')  # past burst cap: fires
            self._fire(slug='show-a')  # own 5-min dedup: suppressed

        assert email.call_count == 2


class TestEmailFormatter:
    def test_formatter_output(self):
        subject, rows, hint = email_service._fmt_feed_refresh_failed({
            'podcast_name': 'Show A',
            'slug': 'show-a',
            'feed_url': 'https://example.com/feed.xml',
            'failure_count': 3,
            'error_message': 'connection refused',
            'timestamp': '2026-07-14T00:00:00Z',
        })
        assert 'Show A' in subject
        labels = [label for label, _ in rows]
        assert 'Feed URL' in labels
        assert 'Consecutive failures' in labels
        assert 'Error' in labels
        assert hint


class TestFailureClassification:
    """Only origin-feed problems (fetch or parse failures) count toward the
    alert; internal faults must not blame the publisher's feed."""

    def _run_refresh(self, parse_result=None, parse_raises=None):
        import main_app.feeds as feeds_mod

        with patch.object(feeds_mod, 'db') as db, \
             patch.object(feeds_mod, 'rss_parser') as rss_parser, \
             patch.object(feeds_mod, 'storage'), \
             patch.object(feeds_mod, 'status_service'), \
             patch.object(feeds_mod, 'pattern_service'), \
             patch.object(feeds_mod, '_record_refresh_failure') as rec_fail, \
             patch.object(feeds_mod, '_record_refresh_success') as rec_ok, \
             patch.object(feeds_mod, '_build_and_save_served_rss'):
            db.get_podcast_by_slug.return_value = {
                'id': 1, 'etag': None, 'last_modified_header': None,
                'artwork_cached': True,
            }
            db.bulk_upsert_discovered_episodes.return_value = 0
            db.is_auto_process_enabled_for_podcast.return_value = False
            rss_parser.fetch_feed_conditional.return_value = (
                b'<html>error page</html>', None, None,
            )
            rss_parser.extract_episodes.return_value = []
            rss_parser.extract_podcast_artwork_url.return_value = None
            rss_parser.extract_podcast_categories.return_value = []
            if parse_raises is not None:
                rss_parser.parse_feed.side_effect = parse_raises
            else:
                rss_parser.parse_feed.return_value = parse_result
            result = feeds_mod.refresh_rss_feed(
                'pod-x', 'https://example.com/x.rss', force=True)
        return result, rec_fail, rec_ok

    @staticmethod
    def _parsed(feed, entries, bozo):
        from unittest.mock import MagicMock
        parsed = MagicMock()
        parsed.feed = feed
        parsed.entries = entries
        parsed.bozo = bozo
        return parsed

    def test_unparseable_body_records_failure(self):
        result, rec_fail, rec_ok = self._run_refresh(
            parse_result=self._parsed(feed={}, entries=[], bozo=True))

        assert result is False
        rec_fail.assert_called_once()
        rec_ok.assert_not_called()

    def test_clean_empty_placeholder_feed_is_success(self):
        """A feed that parses cleanly (bozo=False) but has no metadata or
        items yet must not be treated as an origin failure."""
        result, rec_fail, rec_ok = self._run_refresh(
            parse_result=self._parsed(feed={}, entries=[], bozo=False))

        assert result is True
        rec_fail.assert_not_called()
        rec_ok.assert_called_once()

    def test_internal_exception_not_recorded_as_feed_failure(self):
        result, rec_fail, rec_ok = self._run_refresh(
            parse_raises=RuntimeError('database is locked'))

        assert result is False
        rec_fail.assert_not_called()
        rec_ok.assert_not_called()


class TestFailureColumns:
    def test_update_and_read_failure_fields(self, temp_db):
        temp_db.create_podcast('show-db', 'https://example.com/feed.xml', 'Show DB')
        temp_db.update_podcast(
            'show-db',
            refresh_failure_count=2,
            last_refresh_error='fetch failed',
            last_refresh_error_at='2026-07-14T00:00:00Z',
        )
        p = temp_db.get_podcast_by_slug('show-db')
        assert p['refresh_failure_count'] == 2
        assert p['last_refresh_error'] == 'fetch failed'
        assert p['last_refresh_error_at'] == '2026-07-14T00:00:00Z'

        temp_db.update_podcast(
            'show-db',
            refresh_failure_count=0,
            last_refresh_error=None,
            last_refresh_error_at=None,
        )
        p = temp_db.get_podcast_by_slug('show-db')
        assert p['refresh_failure_count'] == 0
        assert p['last_refresh_error'] is None
        assert p['last_refresh_error_at'] is None
