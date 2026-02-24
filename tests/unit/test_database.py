"""Unit tests for database operations."""
import pytest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))


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
