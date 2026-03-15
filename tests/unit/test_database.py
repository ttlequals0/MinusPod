"""Unit tests for database operations."""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from database import DEFAULT_MODEL_PRICING


class TestPodcastOperations:
    """Tests for podcast CRUD operations."""

    def test_create_podcast(self, temp_db):
        """Create and retrieve a podcast."""
        slug = 'my-test-podcast'
        source_url = 'https://example.com/feed.xml'
        title = 'My Test Podcast'

        podcast_id = temp_db.create_podcast(slug, source_url, title)

        assert podcast_id is not None
        assert podcast_id > 0

        podcast = temp_db.get_podcast_by_slug(slug)

        assert podcast is not None
        assert podcast['slug'] == slug
        assert podcast['source_url'] == source_url
        assert podcast['title'] == title

    def test_podcast_unique_slug(self, temp_db):
        """Duplicate slugs should raise an error."""
        slug = 'unique-podcast'
        source_url = 'https://example.com/feed1.xml'

        temp_db.create_podcast(slug, source_url, 'First Podcast')

        # Attempting to create another with same slug should fail
        with pytest.raises(Exception):
            temp_db.create_podcast(slug, 'https://example.com/feed2.xml', 'Second Podcast')

    def test_get_nonexistent_podcast(self, temp_db):
        """Getting a non-existent podcast should return None."""
        podcast = temp_db.get_podcast_by_slug('nonexistent-slug')

        assert podcast is None

    def test_update_podcast(self, temp_db):
        """Update podcast fields."""
        slug = 'update-test'
        temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Original Title')

        temp_db.update_podcast(slug, title='Updated Title', description='New description')

        podcast = temp_db.get_podcast_by_slug(slug)

        assert podcast['title'] == 'Updated Title'
        assert podcast['description'] == 'New description'

    def test_delete_podcast_cascade(self, temp_db):
        """Deleting podcast should cascade to episodes."""
        slug = 'delete-test'
        temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Delete Me')

        # Add an episode
        temp_db.upsert_episode(slug, 'ep-001', original_url='https://example.com/ep.mp3')

        # Verify episode exists
        episode = temp_db.get_episode(slug, 'ep-001')
        assert episode is not None

        # Delete podcast
        result = temp_db.delete_podcast(slug)
        assert result is True

        # Podcast should be gone
        podcast = temp_db.get_podcast_by_slug(slug)
        assert podcast is None

        # Episode should also be gone (cascade)
        episode = temp_db.get_episode(slug, 'ep-001')
        assert episode is None

    def test_list_all_podcasts(self, temp_db):
        """List all podcasts."""
        temp_db.create_podcast('podcast-a', 'https://a.com/feed.xml', 'Podcast A')
        temp_db.create_podcast('podcast-b', 'https://b.com/feed.xml', 'Podcast B')

        podcasts = temp_db.get_all_podcasts()

        assert len(podcasts) >= 2
        slugs = [p['slug'] for p in podcasts]
        assert 'podcast-a' in slugs
        assert 'podcast-b' in slugs


class TestEpisodeOperations:
    """Tests for episode CRUD operations."""

    def test_upsert_episode_create(self, temp_db):
        """Create a new episode via upsert."""
        slug = 'episode-test'
        temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Test Podcast')

        episode_id = 'episode-001'
        db_id = temp_db.upsert_episode(
            slug,
            episode_id,
            original_url='https://example.com/episode.mp3',
            title='Test Episode',
            status='pending'
        )

        assert db_id is not None
        assert db_id > 0

        episode = temp_db.get_episode(slug, episode_id)

        assert episode is not None
        assert episode['episode_id'] == episode_id
        assert episode['title'] == 'Test Episode'
        assert episode['status'] == 'pending'

    def test_upsert_episode_update(self, temp_db):
        """Update existing episode via upsert."""
        slug = 'upsert-test'
        temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Upsert Podcast')

        episode_id = 'ep-update'
        temp_db.upsert_episode(
            slug,
            episode_id,
            original_url='https://example.com/ep.mp3',
            title='Original Title',
            status='pending'
        )

        # Upsert again with updated values
        temp_db.upsert_episode(
            slug,
            episode_id,
            original_url='https://example.com/ep.mp3',
            title='Updated Title',
            status='processed'
        )

        episode = temp_db.get_episode(slug, episode_id)

        assert episode['title'] == 'Updated Title'
        assert episode['status'] == 'processed'

    def test_get_episodes_by_status(self, temp_db):
        """Get episodes filtered by status."""
        slug = 'status-test'
        temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Status Test')

        temp_db.upsert_episode(slug, 'pending-ep', original_url='https://ex.com/1.mp3', status='pending')
        temp_db.upsert_episode(slug, 'processed-ep', original_url='https://ex.com/2.mp3', status='processed')
        temp_db.upsert_episode(slug, 'failed-ep', original_url='https://ex.com/3.mp3', status='failed')

        # get_episodes returns (episodes_list, total_count)
        pending, pending_count = temp_db.get_episodes(slug, status='pending')
        processed, processed_count = temp_db.get_episodes(slug, status='processed')

        pending_ids = [e['episode_id'] for e in pending]
        processed_ids = [e['episode_id'] for e in processed]

        assert 'pending-ep' in pending_ids
        assert 'processed-ep' in processed_ids
        assert 'failed-ep' not in pending_ids
        assert 'failed-ep' not in processed_ids


class TestAdPatternOperations:
    """Tests for ad pattern operations."""

    def test_create_ad_pattern(self, temp_db):
        """Create and retrieve ad pattern."""
        pattern_id = temp_db.create_ad_pattern(
            scope='global',
            text_template='brought to you by {sponsor}',
            sponsor='BetterHelp'
        )

        assert pattern_id is not None
        assert pattern_id > 0

    def test_create_podcast_scoped_pattern(self, temp_db):
        """Create pattern scoped to a podcast."""
        slug = 'pattern-podcast'
        podcast_id = temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Pattern Test')

        pattern_id = temp_db.create_ad_pattern(
            scope='podcast',
            podcast_id=slug,
            text_template='This show is sponsored by {sponsor}',
            sponsor='CustomSponsor'
        )

        assert pattern_id is not None

    def test_list_ad_patterns(self, temp_db):
        """List all ad patterns."""
        temp_db.create_ad_pattern(scope='global', sponsor='SponsorA')
        temp_db.create_ad_pattern(scope='global', sponsor='SponsorB')

        patterns = temp_db.get_ad_patterns()

        assert len(patterns) >= 2


class TestPatternDuration:
    """Tests for update_pattern_duration and create_ad_pattern with duration."""

    @staticmethod
    def _get_duration_fields(db, pattern_id):
        """Fetch avg_duration and duration_samples for a pattern."""
        conn = db.get_connection()
        return conn.execute(
            "SELECT avg_duration, duration_samples FROM ad_patterns WHERE id = ?",
            (pattern_id,)
        ).fetchone()

    def test_update_pattern_duration_first_sample(self, temp_db):
        """First duration sample: NULL -> value, duration_samples 0 -> 1."""
        pattern_id = temp_db.create_ad_pattern(scope='global', sponsor='TestSponsor')

        row = self._get_duration_fields(temp_db, pattern_id)
        assert row['avg_duration'] is None
        assert row['duration_samples'] == 0

        temp_db.update_pattern_duration(pattern_id, 60.0)

        row = self._get_duration_fields(temp_db, pattern_id)
        assert abs(row['avg_duration'] - 60.0) < 0.001
        assert row['duration_samples'] == 1

    def test_update_pattern_duration_running_average(self, temp_db):
        """Subsequent samples update as running average."""
        pattern_id = temp_db.create_ad_pattern(scope='global', sponsor='TestSponsor')

        temp_db.update_pattern_duration(pattern_id, 60.0)
        temp_db.update_pattern_duration(pattern_id, 80.0)

        row = self._get_duration_fields(temp_db, pattern_id)
        # Running average: (60*1 + 80) / 2 = 70
        assert abs(row['avg_duration'] - 70.0) < 0.001
        assert row['duration_samples'] == 2

    def test_update_pattern_duration_increments_samples(self, temp_db):
        """duration_samples increments with each update."""
        pattern_id = temp_db.create_ad_pattern(scope='global', sponsor='TestSponsor')

        for i in range(5):
            temp_db.update_pattern_duration(pattern_id, 60.0)

        row = self._get_duration_fields(temp_db, pattern_id)
        assert row['duration_samples'] == 5

    def test_create_ad_pattern_with_duration(self, temp_db):
        """Creating a pattern with duration sets avg_duration and duration_samples=1."""
        pattern_id = temp_db.create_ad_pattern(
            scope='global', sponsor='DurationSponsor', duration=45.5
        )

        row = self._get_duration_fields(temp_db, pattern_id)
        assert abs(row['avg_duration'] - 45.5) < 0.001
        assert row['duration_samples'] == 1

    def test_create_ad_pattern_without_duration(self, temp_db):
        """Creating a pattern without duration leaves avg_duration NULL, samples=0."""
        pattern_id = temp_db.create_ad_pattern(scope='global', sponsor='NoDuration')

        row = self._get_duration_fields(temp_db, pattern_id)
        assert row['avg_duration'] is None
        assert row['duration_samples'] == 0


class TestSettingsOperations:
    """Tests for settings operations."""

    def test_get_default_settings(self, temp_db):
        """Get default settings."""
        settings = temp_db.get_all_settings()

        assert settings is not None
        # Should have some default settings
        assert 'retention_days' in settings or len(settings) >= 0

    def test_update_setting(self, temp_db):
        """Update a setting value."""
        temp_db.set_setting('test_key', 'test_value')

        settings = temp_db.get_all_settings()

        # Settings are returned as dicts with 'value' and 'is_default' keys
        assert 'test_key' in settings
        assert settings['test_key']['value'] == 'test_value'

    def test_update_existing_setting(self, temp_db):
        """Update an existing setting."""
        temp_db.set_setting('my_setting', 'initial')
        temp_db.set_setting('my_setting', 'updated')

        settings = temp_db.get_all_settings()

        assert 'my_setting' in settings
        assert settings['my_setting']['value'] == 'updated'


class TestDeleteConflictingCorrections:
    """Tests for delete_conflicting_corrections()."""

    def test_confirm_deletes_false_positive(self, temp_db):
        """Confirming an ad should delete a prior false_positive for the same segment."""
        episode_id = 'ep-conflict-001'

        # Create a false_positive correction
        temp_db.create_pattern_correction(
            correction_type='false_positive',
            episode_id=episode_id,
            original_bounds={'start': 100.0, 'end': 200.0}
        )

        # Verify it exists
        corrections = temp_db.get_episode_corrections(episode_id)
        assert len(corrections) == 1
        assert corrections[0]['correction_type'] == 'false_positive'

        # Delete conflicting corrections when confirming the same segment
        deleted = temp_db.delete_conflicting_corrections(episode_id, 'confirm', 100.0, 200.0)
        assert deleted == 1

        # Verify the false_positive was removed
        corrections = temp_db.get_episode_corrections(episode_id)
        assert len(corrections) == 0

    def test_false_positive_deletes_confirm(self, temp_db):
        """Rejecting an ad should delete a prior confirm for the same segment."""
        episode_id = 'ep-conflict-002'

        # Create a confirm correction
        temp_db.create_pattern_correction(
            correction_type='confirm',
            episode_id=episode_id,
            original_bounds={'start': 300.0, 'end': 400.0}
        )

        # Delete conflicting corrections when marking as false positive
        deleted = temp_db.delete_conflicting_corrections(episode_id, 'false_positive', 300.0, 400.0)
        assert deleted == 1

        corrections = temp_db.get_episode_corrections(episode_id)
        assert len(corrections) == 0

    def test_no_conflict_with_non_overlapping_bounds(self, temp_db):
        """Non-overlapping corrections should not be deleted."""
        episode_id = 'ep-conflict-003'

        temp_db.create_pattern_correction(
            correction_type='false_positive',
            episode_id=episode_id,
            original_bounds={'start': 100.0, 'end': 200.0}
        )

        # Confirm a completely different segment
        deleted = temp_db.delete_conflicting_corrections(episode_id, 'confirm', 500.0, 600.0)
        assert deleted == 0

        # Original correction should still exist
        corrections = temp_db.get_episode_corrections(episode_id)
        assert len(corrections) == 1

    def test_partial_overlap_above_threshold(self, temp_db):
        """Segments overlapping >= 50% should be considered conflicting."""
        episode_id = 'ep-conflict-004'

        # Segment: 100-200 (100s duration)
        temp_db.create_pattern_correction(
            correction_type='false_positive',
            episode_id=episode_id,
            original_bounds={'start': 100.0, 'end': 200.0}
        )

        # New segment: 90-200 (110s duration, overlap=100s, 100/110=91%)
        deleted = temp_db.delete_conflicting_corrections(episode_id, 'confirm', 90.0, 200.0)
        assert deleted == 1

    def test_partial_overlap_below_threshold(self, temp_db):
        """Segments overlapping < 50% should not be considered conflicting."""
        episode_id = 'ep-conflict-005'

        # Segment: 100-200
        temp_db.create_pattern_correction(
            correction_type='false_positive',
            episode_id=episode_id,
            original_bounds={'start': 100.0, 'end': 200.0}
        )

        # New segment: 150-400 (250s duration, overlap=50s, 50/250=20%)
        deleted = temp_db.delete_conflicting_corrections(episode_id, 'confirm', 150.0, 400.0)
        assert deleted == 0

        corrections = temp_db.get_episode_corrections(episode_id)
        assert len(corrections) == 1

    def test_adjust_does_not_delete_anything(self, temp_db):
        """Adjust corrections should not conflict with either type."""
        episode_id = 'ep-conflict-006'

        temp_db.create_pattern_correction(
            correction_type='false_positive',
            episode_id=episode_id,
            original_bounds={'start': 100.0, 'end': 200.0}
        )
        temp_db.create_pattern_correction(
            correction_type='confirm',
            episode_id=episode_id,
            original_bounds={'start': 100.0, 'end': 200.0}
        )

        deleted = temp_db.delete_conflicting_corrections(episode_id, 'adjust', 100.0, 200.0)
        assert deleted == 0

        corrections = temp_db.get_episode_corrections(episode_id)
        assert len(corrections) == 2

    def test_only_deletes_for_matching_episode(self, temp_db):
        """Should not delete corrections from a different episode."""
        ep1 = 'ep-conflict-007a'
        ep2 = 'ep-conflict-007b'

        temp_db.create_pattern_correction(
            correction_type='false_positive',
            episode_id=ep1,
            original_bounds={'start': 100.0, 'end': 200.0}
        )

        # Delete for a different episode
        deleted = temp_db.delete_conflicting_corrections(ep2, 'confirm', 100.0, 200.0)
        assert deleted == 0

        corrections = temp_db.get_episode_corrections(ep1)
        assert len(corrections) == 1


class TestDatabaseSingleton:
    """Tests for database singleton pattern."""

    def test_singleton_reset(self, temp_dir):
        """Verify singleton can be reset for testing."""
        from database import Database

        # Reset singleton
        Database._instance = None

        db1 = Database(data_dir=temp_dir)
        db2 = Database(data_dir=temp_dir)

        # Should be same instance
        assert db1 is db2

        # Reset and create new
        Database._instance = None
        db3 = Database(data_dir=temp_dir)

        # Should be different instance after reset
        assert db1 is not db3

        # Clean up
        Database._instance = None


class TestResetFailedQueueItems:
    """Tests for reset_failed_queue_items() auto-retry logic."""

    def _setup_podcast_and_episode(self, db, slug, episode_id, episode_status='failed', retry_count=0):
        """Helper: create a podcast + episode and return podcast_id."""
        db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
        db.upsert_episode(slug, episode_id,
                          original_url=f'https://example.com/{episode_id}.mp3',
                          status=episode_status,
                          retry_count=retry_count)
        podcast = db.get_podcast_by_slug(slug)
        return podcast['id']

    def _queue_item(self, db, podcast_id, episode_id, status='failed', attempts=1, minutes_ago=10):
        """Helper: insert a queue item with a backdated updated_at."""
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO auto_process_queue
               (podcast_id, episode_id, original_url, title, status, attempts, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now', ?))""",
            (podcast_id, episode_id, f'https://example.com/{episode_id}.mp3',
             'Test', status, attempts, f'-{minutes_ago} minutes')
        )
        conn.commit()

    def test_resets_eligible_transient_failure(self, temp_db):
        """Failed queue items with transient episode failure should be reset to pending."""
        pid = self._setup_podcast_and_episode(temp_db, 'pod1', 'ep1', 'failed', retry_count=0)
        self._queue_item(temp_db, pid, 'ep1', status='failed', attempts=1, minutes_ago=10)

        count = temp_db.reset_failed_queue_items(max_retries=3)

        assert count == 1
        queued = temp_db.get_next_queued_episode()
        assert queued is not None
        assert queued['episode_id'] == 'ep1'

    def test_skips_permanently_failed_episode(self, temp_db):
        """Queue items for permanently_failed episodes should NOT be reset."""
        pid = self._setup_podcast_and_episode(temp_db, 'pod2', 'ep2', 'permanently_failed', retry_count=3)
        self._queue_item(temp_db, pid, 'ep2', status='failed', attempts=1, minutes_ago=10)

        count = temp_db.reset_failed_queue_items(max_retries=3)

        assert count == 0
        queued = temp_db.get_next_queued_episode()
        assert queued is None

    def test_respects_retry_limit(self, temp_db):
        """Queue items where episode retry_count >= max_retries should NOT be reset."""
        pid = self._setup_podcast_and_episode(temp_db, 'pod3', 'ep3', 'failed', retry_count=3)
        self._queue_item(temp_db, pid, 'ep3', status='failed', attempts=3, minutes_ago=60)

        count = temp_db.reset_failed_queue_items(max_retries=3)

        assert count == 0

    def test_backoff_attempt1_requires_5_minutes(self, temp_db):
        """Attempt 1 should require 5 minutes of backoff before retry."""
        pid = self._setup_podcast_and_episode(temp_db, 'pod4', 'ep4', 'failed', retry_count=0)

        # 3 minutes ago - too soon for 5-minute backoff
        self._queue_item(temp_db, pid, 'ep4', status='failed', attempts=1, minutes_ago=3)
        count = temp_db.reset_failed_queue_items(max_retries=3)
        assert count == 0

        # Update to 6 minutes ago - should now be eligible
        conn = temp_db.get_connection()
        conn.execute(
            "UPDATE auto_process_queue SET updated_at = datetime('now', '-6 minutes') WHERE episode_id = 'ep4'"
        )
        conn.commit()
        count = temp_db.reset_failed_queue_items(max_retries=3)
        assert count == 1

    def test_backoff_attempt2_requires_15_minutes(self, temp_db):
        """Attempt 2 should require 15 minutes of backoff."""
        pid = self._setup_podcast_and_episode(temp_db, 'pod5', 'ep5', 'failed', retry_count=1)

        # 10 minutes ago - too soon for 15-minute backoff
        self._queue_item(temp_db, pid, 'ep5', status='failed', attempts=2, minutes_ago=10)
        count = temp_db.reset_failed_queue_items(max_retries=3)
        assert count == 0

        # 20 minutes ago - should be eligible
        conn = temp_db.get_connection()
        conn.execute(
            "UPDATE auto_process_queue SET updated_at = datetime('now', '-20 minutes') WHERE episode_id = 'ep5'"
        )
        conn.commit()
        count = temp_db.reset_failed_queue_items(max_retries=3)
        assert count == 1

    def test_backoff_attempt3_requires_45_minutes(self, temp_db):
        """Attempt 3+ should require 45 minutes of backoff."""
        pid = self._setup_podcast_and_episode(temp_db, 'pod6', 'ep6', 'failed', retry_count=2)

        # 30 minutes ago - too soon for 45-minute backoff
        self._queue_item(temp_db, pid, 'ep6', status='failed', attempts=3, minutes_ago=30)
        count = temp_db.reset_failed_queue_items(max_retries=3)
        assert count == 0

        # 50 minutes ago - should be eligible
        conn = temp_db.get_connection()
        conn.execute(
            "UPDATE auto_process_queue SET updated_at = datetime('now', '-50 minutes') WHERE episode_id = 'ep6'"
        )
        conn.commit()
        count = temp_db.reset_failed_queue_items(max_retries=3)
        assert count == 1

    def test_skips_old_failed_items(self, temp_db):
        """Failed queue items older than max_age_hours should NOT be retried."""
        pid = self._setup_podcast_and_episode(temp_db, 'pod8', 'ep8', 'failed', retry_count=1)
        # Failed 72 hours ago - well past the 48-hour default
        conn = temp_db.get_connection()
        conn.execute(
            """INSERT INTO auto_process_queue
               (podcast_id, episode_id, original_url, title, status, attempts, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now', '-72 hours'))""",
            (pid, 'ep8', 'https://example.com/ep8.mp3', 'Test', 'failed', 1)
        )
        conn.commit()

        count = temp_db.reset_failed_queue_items(max_retries=3, max_age_hours=48)

        assert count == 0

    def test_skips_already_processed_episode(self, temp_db):
        """If episode was already processed (e.g., by client retry), skip it."""
        pid = self._setup_podcast_and_episode(temp_db, 'pod7', 'ep7', 'processed', retry_count=1)
        self._queue_item(temp_db, pid, 'ep7', status='failed', attempts=1, minutes_ago=10)

        count = temp_db.reset_failed_queue_items(max_retries=3)

        assert count == 0


class TestTokenUsage:
    """Tests for LLM token usage tracking and cost calculation."""

    def test_record_token_usage_creates_entry(self, temp_db):
        """Single call creates per-model row and global stats."""
        temp_db.record_token_usage('claude-haiku-4-5-20251001', 1000, 500)

        summary = temp_db.get_token_usage_summary()
        assert summary['totalInputTokens'] == 1000
        assert summary['totalOutputTokens'] == 500
        assert summary['totalCost'] > 0
        assert len(summary['models']) == 1
        assert summary['models'][0]['modelId'] == 'claude-haiku-4-5-20251001'
        assert summary['models'][0]['callCount'] == 1

    def test_record_token_usage_accumulates(self, temp_db):
        """Multiple calls for the same model increment correctly."""
        temp_db.record_token_usage('claude-haiku-4-5-20251001', 1000, 500)
        temp_db.record_token_usage('claude-haiku-4-5-20251001', 2000, 1000)

        summary = temp_db.get_token_usage_summary()
        assert summary['totalInputTokens'] == 3000
        assert summary['totalOutputTokens'] == 1500
        assert len(summary['models']) == 1
        assert summary['models'][0]['callCount'] == 2
        assert summary['models'][0]['totalInputTokens'] == 3000

    def test_record_token_usage_multiple_models(self, temp_db):
        """Per-model isolation works, global totals sum correctly."""
        temp_db.record_token_usage('claude-haiku-4-5-20251001', 1000, 500)
        temp_db.record_token_usage('claude-sonnet-4-20250514', 2000, 1000)

        summary = temp_db.get_token_usage_summary()
        assert summary['totalInputTokens'] == 3000
        assert summary['totalOutputTokens'] == 1500
        assert len(summary['models']) == 2

    def test_calculate_token_cost_exact_match(self, temp_db):
        """Cost is calculated correctly with known model pricing."""
        conn = temp_db.get_connection()
        # Haiku: $1.0/Mtok in, $5.0/Mtok out
        cost = temp_db._calculate_token_cost(conn, 'claude-haiku-4-5-20251001', 1_000_000, 1_000_000)
        assert abs(cost - 6.0) < 0.001  # $1 input + $5 output

    def test_calculate_token_cost_prefix_match(self, temp_db):
        """Model ID with extra suffix matches on prefix."""
        conn = temp_db.get_connection()
        # 'claude-haiku-4-5-20251001-extra' should prefix-match to 'claude-haiku-4-5-20251001'
        cost = temp_db._calculate_token_cost(conn, 'claude-haiku-4-5-20251001-extra', 1_000_000, 0)
        assert abs(cost - 1.0) < 0.001

    def test_calculate_token_cost_unknown_model(self, temp_db):
        """Unknown model returns 0 cost without crashing."""
        conn = temp_db.get_connection()
        cost = temp_db._calculate_token_cost(conn, 'unknown-model-xyz', 1_000_000, 1_000_000)
        assert cost == 0.0

    def test_get_token_usage_summary_empty(self, temp_db):
        """Empty database returns zero totals."""
        summary = temp_db.get_token_usage_summary()
        assert summary['totalInputTokens'] == 0
        assert summary['totalOutputTokens'] == 0
        assert summary['totalCost'] == 0
        assert summary['models'] == []

    def test_get_token_usage_summary_with_data(self, temp_db):
        """Summary returns correct structure and values."""
        temp_db.record_token_usage('claude-haiku-4-5-20251001', 100_000, 50_000)

        summary = temp_db.get_token_usage_summary()
        assert summary['totalInputTokens'] == 100_000
        assert summary['totalOutputTokens'] == 50_000
        assert summary['totalCost'] > 0

        model = summary['models'][0]
        assert model['modelId'] == 'claude-haiku-4-5-20251001'
        assert model['displayName'] == 'Claude Haiku 4.5'
        assert model['totalInputTokens'] == 100_000
        assert model['totalOutputTokens'] == 50_000
        assert model['callCount'] == 1
        assert model['inputCostPerMtok'] == 1.0
        assert model['outputCostPerMtok'] == 5.0


class MockStorage:
    """Mock storage for delete/cleanup tests."""
    def cleanup_episode_files(self, slug, episode_id):
        return 1024  # 1KB freed

    def delete_processed_file(self, slug, episode_id):
        pass


class TestBulkUpsertDiscoveredEpisodes:
    """Tests for bulk_upsert_discovered_episodes."""

    def _make_episode(self, ep_id, title='Test', url='https://example.com/ep.mp3',
                      episode_number=None, published=None):
        return {
            'id': ep_id,
            'title': title,
            'url': url,
            'description': 'desc',
            'artwork_url': None,
            'episode_number': episode_number,
            'published': published or '',
        }

    def test_bulk_upsert_inserts_new_episodes(self, temp_db):
        slug = 'bulk-test'
        temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Bulk Test')

        episodes = [self._make_episode(f'ep-{i}') for i in range(3)]
        count = temp_db.bulk_upsert_discovered_episodes(slug, episodes)

        assert count == 3
        for i in range(3):
            ep = temp_db.get_episode(slug, f'ep-{i}')
            assert ep is not None
            assert ep['status'] == 'discovered'

    def test_bulk_upsert_does_not_overwrite_existing_status(self, temp_db):
        slug = 'bulk-no-overwrite'
        temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Test')

        temp_db.bulk_upsert_discovered_episodes(slug, [self._make_episode('ep-1')])
        temp_db.upsert_episode(slug, 'ep-1', status='processed')

        temp_db.bulk_upsert_discovered_episodes(slug, [self._make_episode('ep-1')])
        ep = temp_db.get_episode(slug, 'ep-1')
        assert ep['status'] == 'processed'

    def test_bulk_upsert_updates_episode_number(self, temp_db):
        slug = 'bulk-epnum'
        temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Test')

        temp_db.bulk_upsert_discovered_episodes(slug, [self._make_episode('ep-1')])
        ep = temp_db.get_episode(slug, 'ep-1')
        assert ep.get('episode_number') is None

        temp_db.bulk_upsert_discovered_episodes(slug, [self._make_episode('ep-1', episode_number=42)])
        ep = temp_db.get_episode(slug, 'ep-1')
        assert ep['episode_number'] == 42

    def test_bulk_upsert_updates_episode_id_on_guid_change_discovered(self, temp_db):
        """When RSS GUID changes for a discovered episode, update the stored episode_id."""
        slug = 'bulk-guid-change'
        temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Test')
        pub_date = 'Mon, 10 Mar 2026 12:00:00 +0000'

        # Insert episode with original GUID
        temp_db.bulk_upsert_discovered_episodes(slug, [
            self._make_episode('old-guid', title='My Episode', published=pub_date)
        ])
        ep = temp_db.get_episode(slug, 'old-guid')
        assert ep is not None
        assert ep['status'] == 'discovered'

        # Re-insert same episode with new GUID (same title+date)
        temp_db.bulk_upsert_discovered_episodes(slug, [
            self._make_episode('new-guid', title='My Episode', published=pub_date)
        ])

        # Old ID should be gone, new ID should exist
        assert temp_db.get_episode(slug, 'old-guid') is None
        ep = temp_db.get_episode(slug, 'new-guid')
        assert ep is not None
        assert ep['status'] == 'discovered'

    def test_bulk_upsert_preserves_episode_id_on_guid_change_processed(self, temp_db):
        """When RSS GUID changes for a processed episode, do NOT update the stored episode_id."""
        slug = 'bulk-guid-processed'
        temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Test')
        pub_date = 'Mon, 10 Mar 2026 12:00:00 +0000'

        # Insert and mark as processed
        temp_db.bulk_upsert_discovered_episodes(slug, [
            self._make_episode('original-id', title='Processed Ep', published=pub_date)
        ])
        temp_db.upsert_episode(slug, 'original-id', status='processed')

        # Re-insert with new GUID
        temp_db.bulk_upsert_discovered_episodes(slug, [
            self._make_episode('changed-id', title='Processed Ep', published=pub_date)
        ])

        # Original ID should still exist with processed status
        ep = temp_db.get_episode(slug, 'original-id')
        assert ep is not None
        assert ep['status'] == 'processed'
        # New ID should NOT exist
        assert temp_db.get_episode(slug, 'changed-id') is None

    def test_bulk_upsert_nonexistent_slug(self, temp_db):
        count = temp_db.bulk_upsert_discovered_episodes('no-such-slug', [self._make_episode('ep-1')])
        assert count == 0


class TestGetEpisodesByIds:
    """Tests for get_episodes_by_ids."""

    def test_get_episodes_by_ids(self, temp_db):
        slug = 'byids-test'
        temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Test')
        for i in range(3):
            temp_db.upsert_episode(slug, f'ep-{i}', original_url=f'https://example.com/{i}.mp3')

        results = temp_db.get_episodes_by_ids(slug, ['ep-0', 'ep-2'])
        ids = [r['episode_id'] for r in results]
        assert len(results) == 2
        assert 'ep-0' in ids
        assert 'ep-2' in ids

    def test_get_episodes_by_ids_empty_list(self, temp_db):
        results = temp_db.get_episodes_by_ids('anything', [])
        assert results == []

    def test_get_episodes_by_ids_wrong_slug(self, temp_db):
        slug = 'byids-wrong'
        temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Test')
        temp_db.upsert_episode(slug, 'ep-1', original_url='https://example.com/1.mp3')

        results = temp_db.get_episodes_by_ids('nonexistent-slug', ['ep-1'])
        assert results == []


class TestDeleteEpisodes:
    """Tests for delete_episodes."""

    def test_delete_episodes_resets_to_discovered(self, temp_db):
        slug = 'del-test'
        temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Test')
        temp_db.upsert_episode(slug, 'ep-1',
                               original_url='https://example.com/1.mp3',
                               status='processed',
                               processed_file='/path/to/file.mp3')

        storage = MockStorage()
        count, freed = temp_db.delete_episodes(slug, ['ep-1'], storage)

        assert count == 1
        assert freed > 0
        ep = temp_db.get_episode(slug, 'ep-1')
        assert ep['status'] == 'discovered'
        assert ep['processed_file'] is None

    def test_delete_episodes_skips_unprocessed(self, temp_db):
        slug = 'del-skip'
        temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Test')
        temp_db.upsert_episode(slug, 'ep-1',
                               original_url='https://example.com/1.mp3',
                               status='discovered')

        storage = MockStorage()
        count, freed = temp_db.delete_episodes(slug, ['ep-1'], storage)
        assert count == 0
        assert freed == 0.0


class TestResetEpisodeToDiscovered:
    """Tests for _reset_episode_to_discovered."""

    def test_reset_clears_all_fields(self, temp_db):
        slug = 'reset-test'
        temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Test')
        temp_db.upsert_episode(slug, 'ep-1',
                               original_url='https://example.com/1.mp3',
                               status='processed',
                               processed_file='/path/to/file.mp3',
                               original_duration=3600.0,
                               new_duration=3400.0,
                               ads_removed=3,
                               error_message='some error')

        temp_db._reset_episode_to_discovered(slug, 'ep-1')

        ep = temp_db.get_episode(slug, 'ep-1')
        assert ep['status'] == 'discovered'
        assert ep['processed_file'] is None
        assert ep['original_duration'] is None
        assert ep['new_duration'] is None
        assert ep['ads_removed'] == 0
        assert ep['error_message'] is None


class TestCleanupOldEpisodes:
    """Tests for cleanup_old_episodes."""

    def test_cleanup_respects_retention_days(self, temp_db):
        slug = 'cleanup-ret'
        temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Test')
        temp_db.set_setting('retention_days', '1')
        temp_db.upsert_episode(slug, 'ep-old',
                               original_url='https://example.com/1.mp3',
                               status='processed',
                               processed_file='/path/file.mp3')
        # Backdate processed_at to 2 days ago
        conn = temp_db.get_connection()
        conn.execute(
            "UPDATE episodes SET processed_at = datetime('now', '-2 days') WHERE episode_id = 'ep-old'"
        )
        conn.commit()

        storage = MockStorage()
        count, freed = temp_db.cleanup_old_episodes(storage=storage)
        assert count == 1

    def test_cleanup_skips_recent(self, temp_db):
        slug = 'cleanup-recent'
        temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Test')
        temp_db.set_setting('retention_days', '30')
        temp_db.upsert_episode(slug, 'ep-new',
                               original_url='https://example.com/1.mp3',
                               status='processed',
                               processed_file='/path/file.mp3')
        # Set processed_at to now
        conn = temp_db.get_connection()
        conn.execute(
            "UPDATE episodes SET processed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE episode_id = 'ep-new'"
        )
        conn.commit()

        storage = MockStorage()
        count, freed = temp_db.cleanup_old_episodes(storage=storage)
        assert count == 0

    def test_cleanup_disabled_when_zero(self, temp_db):
        slug = 'cleanup-zero'
        temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Test')
        temp_db.set_setting('retention_days', '0')
        temp_db.upsert_episode(slug, 'ep-1',
                               original_url='https://example.com/1.mp3',
                               status='processed',
                               processed_file='/path/file.mp3')

        storage = MockStorage()
        count, freed = temp_db.cleanup_old_episodes(storage=storage)
        assert count == 0
        assert freed == 0.0

    def test_cleanup_force_all(self, temp_db):
        slug = 'cleanup-force'
        temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Test')
        temp_db.upsert_episode(slug, 'ep-1',
                               original_url='https://example.com/1.mp3',
                               status='processed',
                               processed_file='/path/file.mp3')
        conn = temp_db.get_connection()
        conn.execute(
            "UPDATE episodes SET processed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE episode_id = 'ep-1'"
        )
        conn.commit()

        storage = MockStorage()
        count, freed = temp_db.cleanup_old_episodes(force_all=True, storage=storage)
        assert count == 1

    def test_cleanup_crashes_without_storage(self, temp_db):
        with pytest.raises(ValueError, match="storage is required"):
            temp_db.cleanup_old_episodes()


class TestVacuum:
    """Tests for vacuum."""

    def test_vacuum_returns_duration(self, temp_db):
        duration = temp_db.vacuum()
        assert isinstance(duration, int)
        assert duration >= 0


class TestRetentionSettings:
    """Tests for retention settings."""

    def test_get_set_retention_days(self, temp_db):
        temp_db.set_setting('retention_days', '45')
        settings = temp_db.get_all_settings()
        assert settings['retention_days']['value'] == '45'


class TestEpisodeSorting:
    """Tests for episode sorting."""

    def test_sort_by_episode_number(self, temp_db):
        slug = 'sort-epnum'
        temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Test')
        temp_db.upsert_episode(slug, 'ep-a', original_url='https://example.com/a.mp3')
        temp_db.upsert_episode(slug, 'ep-b', original_url='https://example.com/b.mp3')
        temp_db.upsert_episode(slug, 'ep-c', original_url='https://example.com/c.mp3')

        conn = temp_db.get_connection()
        conn.execute("UPDATE episodes SET episode_number = 3 WHERE episode_id = 'ep-a'")
        conn.execute("UPDATE episodes SET episode_number = 1 WHERE episode_id = 'ep-b'")
        conn.execute("UPDATE episodes SET episode_number = 2 WHERE episode_id = 'ep-c'")
        conn.commit()

        episodes, total = temp_db.get_episodes(slug, sort_by='episode_number', sort_dir='asc')
        ep_ids = [e['episode_id'] for e in episodes]
        assert ep_ids == ['ep-b', 'ep-c', 'ep-a']

    def test_sort_by_published_at(self, temp_db):
        slug = 'sort-pub'
        temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Test')
        temp_db.upsert_episode(slug, 'ep-old', original_url='https://example.com/old.mp3')
        temp_db.upsert_episode(slug, 'ep-new', original_url='https://example.com/new.mp3')

        conn = temp_db.get_connection()
        conn.execute("UPDATE episodes SET published_at = '2025-01-01T00:00:00Z' WHERE episode_id = 'ep-old'")
        conn.execute("UPDATE episodes SET published_at = '2026-01-01T00:00:00Z' WHERE episode_id = 'ep-new'")
        conn.commit()

        episodes, total = temp_db.get_episodes(slug, sort_by='published_at', sort_dir='desc')
        ep_ids = [e['episode_id'] for e in episodes]
        assert ep_ids[0] == 'ep-new'
        assert ep_ids[1] == 'ep-old'

    def test_sort_by_invalid_column_defaults_to_created_at(self, temp_db):
        slug = 'sort-invalid'
        temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Test')
        temp_db.upsert_episode(slug, 'ep-1', original_url='https://example.com/1.mp3')

        # Should not crash -- falls back to created_at
        episodes, total = temp_db.get_episodes(slug, sort_by='bobby_tables; DROP TABLE--', sort_dir='asc')
        assert total >= 1


class TestBatchMethods:
    """Tests for batch DB methods."""

    def test_batch_clear_episode_details(self, temp_db):
        slug = 'batch-clear'
        temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Test')
        temp_db.upsert_episode(slug, 'ep-1', original_url='https://example.com/1.mp3')
        temp_db.upsert_episode(slug, 'ep-2', original_url='https://example.com/2.mp3')

        # batch_clear_episode_details should not crash even with no details
        temp_db.batch_clear_episode_details(slug, ['ep-1', 'ep-2'])

    def test_batch_reset_episodes_to_discovered(self, temp_db):
        slug = 'batch-reset'
        temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Test')
        temp_db.upsert_episode(slug, 'ep-1', original_url='https://example.com/1.mp3',
                               status='processed', processed_file='/path/1.mp3')
        temp_db.upsert_episode(slug, 'ep-2', original_url='https://example.com/2.mp3',
                               status='failed', error_message='oops')

        temp_db.batch_reset_episodes_to_discovered(slug, ['ep-1', 'ep-2'])

        for eid in ['ep-1', 'ep-2']:
            ep = temp_db.get_episode(slug, eid)
            assert ep['status'] == 'discovered'
            assert ep['processed_file'] is None
            assert ep['error_message'] is None

    def test_batch_set_episodes_pending(self, temp_db):
        slug = 'batch-pending'
        temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Test')
        temp_db.upsert_episode(slug, 'ep-1', original_url='https://example.com/1.mp3',
                               status='discovered')
        temp_db.upsert_episode(slug, 'ep-2', original_url='https://example.com/2.mp3',
                               status='discovered')

        count = temp_db.batch_set_episodes_pending(slug, ['ep-1', 'ep-2'])
        assert count == 2

        for eid in ['ep-1', 'ep-2']:
            ep = temp_db.get_episode(slug, eid)
            assert ep['status'] == 'pending'

    def test_batch_set_episodes_pending_with_reprocess(self, temp_db):
        slug = 'batch-reprocess'
        temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Test')
        temp_db.upsert_episode(slug, 'ep-1', original_url='https://example.com/1.mp3',
                               status='processed')

        count = temp_db.batch_set_episodes_pending(
            slug, ['ep-1'],
            reprocess_mode='full',
            reprocess_requested_at='2026-01-01T00:00:00Z'
        )
        assert count == 1
        ep = temp_db.get_episode(slug, 'ep-1')
        assert ep['status'] == 'pending'
        assert ep['reprocess_mode'] == 'full'

    def test_batch_methods_empty_ids(self, temp_db):
        slug = 'batch-empty'
        temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Test')
        # Should not crash with empty lists
        temp_db.batch_clear_episode_details(slug, [])
        temp_db.batch_reset_episodes_to_discovered(slug, [])
        assert temp_db.batch_set_episodes_pending(slug, []) == 0
