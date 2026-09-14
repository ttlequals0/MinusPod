"""Shared-outage-aware refresh_all_feeds (checkpoint 08, ops-hardening task 1).

When most feeds in one refresh batch fail together, that is a shared network
outage, not N publishers independently breaking. refresh_all_feeds must not
mark every healthy feed broken, must leave cached feed data and conditional
GET validators (etag/last-modified) untouched, and must schedule one bounded,
jittered retry instead of every feed retrying in lockstep at the next tick.
A batch where only a minority of feeds fail is unaffected: those still count
against each broken feed's refresh_failure_count as before.

get_feed_map() is patched per test to the feeds this module creates: the
suite runs many test modules against one shared app/db singleton (see
tests/app_bootstrap.py), and refresh_all_feeds iterates every configured
feed, so leaving it unpatched would pull in -- and mutate -- unrelated
feeds left behind by other test modules.
"""
import os
import tempfile
from datetime import datetime, timezone

import pytest

# main_app resolves its data dir at import time; set it before any src
# import in case this module is collected before another one that does
# (mirrors tests/integration/test_feed_source_url_patch.py).
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='refresh-outage-test-'))

VALID_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Outage Test Show</title>
    <link>https://example.com</link>
    <description>D</description>
    <item>
      <title>Ep One</title>
      <enclosure url="https://example.com/ep1.mp3" type="audio/mpeg"/>
      <guid>ep1</guid>
    </item>
  </channel>
</rss>"""

FAILED_FETCH = (None, None, None)


def _mock_fetch(monkeypatch, result_or_fn):
    if callable(result_or_fn):
        fn = result_or_fn
    else:
        def fn(url, etag=None, last_modified=None):
            return result_or_fn
    monkeypatch.setattr('main_app.feeds.rss_parser.fetch_feed_conditional', fn)


@pytest.fixture
def db(app_client):
    from api import get_database
    return get_database()


@pytest.fixture
def seed_feeds(db, monkeypatch):
    """Create N unique podcasts under a caller-chosen slug prefix, scope
    get_feed_map() to exactly those slugs, and clean up afterwards."""
    created_slugs = []

    def _seed(n, prefix):
        import main_app.feeds as feeds_mod
        feed_map = {}
        for i in range(n):
            slug = f'{prefix}-{i}'
            db.create_podcast(slug, f'https://example.com/{slug}.xml', f'Show {i}')
            db.update_podcast(slug, etag=f'"etag-{i}"',
                              last_modified_header='Mon, 01 Jan 2026 00:00:00 GMT')
            feed_map[slug] = {'in': f'https://example.com/{slug}.xml', 'out': f'/{slug}'}
        created_slugs.extend(feed_map.keys())
        monkeypatch.setattr(feeds_mod, 'get_feed_map', lambda: feed_map)
        return list(feed_map.keys())

    yield _seed
    for slug in created_slugs:
        try:
            db.delete_podcast(slug)
        except Exception:
            pass


@pytest.fixture(autouse=True)
def _reset_coalesce():
    import main_app.feeds as feeds_mod
    feeds_mod._refresh_coalesce.invalidate()
    yield
    feeds_mod._refresh_coalesce.invalidate()


class TestSharedOutageDetected:
    def test_all_feeds_failing_together_skips_per_feed_counting(self, db, seed_feeds, monkeypatch):
        slugs = seed_feeds(4, 'outage-all')
        _mock_fetch(monkeypatch, FAILED_FETCH)

        from main_app.feeds import refresh_all_feeds
        result = refresh_all_feeds()

        assert result['failed'] == 4
        assert result['outage']['detected'] is True

        for i, slug in enumerate(slugs):
            p = db.get_podcast_by_slug(slug)
            # Not marked broken: no per-feed counter bump.
            assert p['refresh_failure_count'] == 0
            # Conditional-fetch validators and cached state untouched.
            assert p['etag'] == f'"etag-{i}"'

    def test_single_jittered_retry_timestamp_is_recorded(self, db, seed_feeds, monkeypatch):
        seed_feeds(3, 'outage-retry')
        _mock_fetch(monkeypatch, FAILED_FETCH)

        from main_app.feeds import refresh_all_feeds
        result = refresh_all_feeds()

        assert db.get_setting('feeds_refresh_outage_active') == '1'
        next_retry = result['outage']['nextRetryAt']
        assert next_retry
        parsed = datetime.fromisoformat(next_retry)
        assert parsed > datetime.now(timezone.utc)
        assert db.get_setting('feeds_next_refresh_retry_at') == next_retry
        assert int(db.get_setting('feeds_refresh_outage_affected_count')) == 3

    def test_recovery_after_outage_does_not_duplicate_episodes(self, db, seed_feeds, monkeypatch):
        import main_app.feeds as feeds_mod
        slugs = seed_feeds(3, 'outage-recover')
        _mock_fetch(monkeypatch, FAILED_FETCH)
        feeds_mod.refresh_all_feeds()
        for slug in slugs:
            assert db.get_podcast_by_slug(slug)['refresh_failure_count'] == 0

        # Upstream recovers.
        _mock_fetch(monkeypatch, (VALID_RSS, '"new-etag"', None))
        feeds_mod._refresh_coalesce.invalidate()
        result = feeds_mod.refresh_all_feeds()

        assert result['outage']['detected'] is False
        assert db.get_setting('feeds_refresh_outage_active') == '0'
        counts_after_first_success = {}
        for slug in slugs:
            _, count = db.get_episodes(slug)
            assert count == 1
            counts_after_first_success[slug] = count

        # A second successful refresh against the same upstream content must
        # not re-insert the same episode (guid-based upsert stays idempotent).
        feeds_mod._refresh_coalesce.invalidate()
        feeds_mod.refresh_all_feeds()
        for slug in slugs:
            _, count = db.get_episodes(slug)
            assert count == counts_after_first_success[slug]


class TestPartialFailureBelowThreshold:
    def test_minority_failure_still_counts_per_feed(self, db, seed_feeds, monkeypatch):
        slugs = seed_feeds(4, 'outage-partial')
        broken_slug = slugs[0]

        def fetch(url, etag=None, last_modified=None):
            if broken_slug in url:
                return FAILED_FETCH
            return (VALID_RSS, '"ok-etag"', None)

        _mock_fetch(monkeypatch, fetch)

        from main_app.feeds import refresh_all_feeds
        result = refresh_all_feeds()

        assert result['failed'] == 1
        assert result['outage']['detected'] is False
        assert db.get_setting('feeds_refresh_outage_active') != '1'

        broken = db.get_podcast_by_slug(broken_slug)
        assert broken['refresh_failure_count'] == 1
        for slug in slugs[1:]:
            assert db.get_podcast_by_slug(slug)['refresh_failure_count'] == 0
