"""Tests for the per-feed -> global-default -> hard-fallback resolvers
introduced in 2.0.20 (issue #181 follow-up).

Resolvers under test (in src/database/podcasts.py):
- get_max_episodes_for_podcast(slug, podcast=None)
- is_only_expose_processed_for_podcast(slug, podcast=None)
"""

import pytest


@pytest.fixture
def feed_slug(temp_db):
    slug = 'precedence-test'
    temp_db.create_podcast(slug, 'https://example.com/feed.xml', title='Precedence Test')
    return slug


class TestMaxEpisodesResolution:
    def test_per_feed_set_wins_over_global(self, temp_db, feed_slug):
        temp_db.set_setting('max_feed_episodes', '200', is_default=False)
        temp_db.update_podcast(feed_slug, max_episodes=42)
        assert temp_db.get_max_episodes_for_podcast(feed_slug) == 42

    def test_per_feed_null_falls_back_to_global(self, temp_db, feed_slug):
        temp_db.set_setting('max_feed_episodes', '150', is_default=False)
        # Per-feed left at default NULL.
        assert temp_db.get_max_episodes_for_podcast(feed_slug) == 150

    def test_per_feed_null_and_no_global_falls_back_to_300(self, temp_db, feed_slug):
        # Wipe seeded global to simulate "neither set".
        conn = temp_db.get_connection()
        conn.execute("DELETE FROM settings WHERE key = 'max_feed_episodes'")
        conn.commit()
        assert temp_db.get_max_episodes_for_podcast(feed_slug) == 300

    def test_passing_in_podcast_dict_avoids_extra_lookup(self, temp_db, feed_slug):
        temp_db.update_podcast(feed_slug, max_episodes=77)
        podcast = temp_db.get_podcast_by_slug(feed_slug)
        # Pass dict explicitly; method must not need to re-fetch.
        assert temp_db.get_max_episodes_for_podcast(feed_slug, podcast=podcast) == 77


class TestOnlyExposeProcessedResolution:
    def test_per_feed_true_wins_when_global_false(self, temp_db, feed_slug):
        temp_db.set_setting('only_expose_processed_default', 'false', is_default=True)
        temp_db.update_podcast(feed_slug, only_expose_processed_episodes=1)
        assert temp_db.is_only_expose_processed_for_podcast(feed_slug) is True

    def test_per_feed_false_wins_when_global_true(self, temp_db, feed_slug):
        temp_db.set_setting('only_expose_processed_default', 'true', is_default=False)
        temp_db.update_podcast(feed_slug, only_expose_processed_episodes=0)
        assert temp_db.is_only_expose_processed_for_podcast(feed_slug) is False

    def test_per_feed_null_uses_global_true(self, temp_db, feed_slug):
        temp_db.set_setting('only_expose_processed_default', 'true', is_default=False)
        # Per-feed left NULL.
        assert temp_db.is_only_expose_processed_for_podcast(feed_slug) is True

    def test_per_feed_null_uses_global_false(self, temp_db, feed_slug):
        temp_db.set_setting('only_expose_processed_default', 'false', is_default=True)
        assert temp_db.is_only_expose_processed_for_podcast(feed_slug) is False

    def test_no_global_setting_defaults_to_false(self, temp_db, feed_slug):
        conn = temp_db.get_connection()
        conn.execute("DELETE FROM settings WHERE key = 'only_expose_processed_default'")
        conn.commit()
        assert temp_db.is_only_expose_processed_for_podcast(feed_slug) is False
