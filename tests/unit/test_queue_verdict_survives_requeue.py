"""A requeue written while a run is in flight must survive the drainer's
verdict on that run.

auto_process_queue is unique per (podcast, episode), so a hook that re-queues
an episode mid-run reuses the very row the drainer claimed. An unconditional
verdict write then closed or failed the fresh requeue, and nothing re-opened
it: the rerun never ran.
"""
from unittest.mock import MagicMock, patch

from tests.app_bootstrap import bootstrap

_data_dir = bootstrap('queue_verdict_test_')

import main_app.background as background  # noqa: E402
import main_app.processing as processing  # noqa: E402
from database import Database  # noqa: E402

URL = 'https://example.com/ep.mp3'


def _claimed_row(db, slug, episode_id):
    """Seed a feed plus an episode and hand back the drainer's claimed row."""
    db.create_podcast(slug, 'https://example.com/feed.xml', 'A Podcast')
    db.upsert_episode(slug, episode_id, original_url=URL, status='processing')
    db.upsert_episode_for_processing(slug, episode_id, URL, 'Episode One')
    row = db.claim_next_queued_episode()
    assert row['status'] == 'processing'
    return row


def _row_status(db, queue_id):
    return db.get_connection().execute(
        'SELECT status FROM auto_process_queue WHERE id = ?', (queue_id,)
    ).fetchone()['status']


class TestVerdictIsConditional:
    def test_a_mid_run_requeue_survives_a_completed_verdict(self):
        db = Database()
        slug = 'verdict-completed-feed'
        row = _claimed_row(db, slug, 'ep1')

        db.upsert_episode_for_processing(slug, 'ep1', URL, 'Episode One')
        wrote = db._update_queue_status(row['id'], 'completed',
                                       expect_status='processing')

        assert wrote is False
        assert _row_status(db, row['id']) == 'pending'
        assert db.claim_next_queued_episode()['id'] == row['id']
        db.delete_podcast(slug)

    def test_a_mid_run_requeue_survives_a_failed_verdict(self):
        db = Database()
        slug = 'verdict-failed-feed'
        row = _claimed_row(db, slug, 'ep1')

        db.upsert_episode_for_processing(slug, 'ep1', URL, 'Episode One')
        wrote = db._update_queue_status(row['id'], 'failed',
                                       'Processing ended with status: pending',
                                       expect_status='processing')

        assert wrote is False
        assert _row_status(db, row['id']) == 'pending'
        assert db.claim_next_queued_episode()['id'] == row['id']
        db.delete_podcast(slug)

    def test_an_untouched_row_still_takes_the_verdict(self):
        db = Database()
        slug = 'verdict-normal-feed'
        row = _claimed_row(db, slug, 'ep1')

        assert db._update_queue_status(row['id'], 'completed',
                                      expect_status='processing') is True
        assert _row_status(db, row['id']) == 'completed'
        assert db.claim_next_queued_episode() is None
        db.delete_podcast(slug)

    def test_an_untouched_row_still_takes_a_failure(self):
        db = Database()
        slug = 'verdict-normal-failure-feed'
        row = _claimed_row(db, slug, 'ep1')

        assert db._update_queue_status(row['id'], 'failed', 'boom',
                                      expect_status='processing') is True
        assert _row_status(db, row['id']) == 'failed'
        db.delete_podcast(slug)

    def test_without_the_guard_the_write_is_unconditional(self):
        # Callers that are not reporting on a claim they hold keep the old
        # behavior.
        db = Database()
        slug = 'verdict-unguarded-feed'
        row = _claimed_row(db, slug, 'ep1')

        db.upsert_episode_for_processing(slug, 'ep1', URL, 'Episode One')
        assert db._update_queue_status(row['id'], 'completed') is True
        assert _row_status(db, row['id']) == 'completed'
        db.delete_podcast(slug)


class TestDegradedRedetectSurvives:
    """The degraded re-detect re-queues while the episode ends 'processed',
    so the drainer used to write 'completed' straight over it."""

    def test_the_queued_redetect_is_claimable_after_the_verdict(self):
        db = Database()
        slug = 'verdict-degraded-feed'
        row = _claimed_row(db, slug, 'ep1')
        db.upsert_episode(slug, 'ep1', status='processed')

        with patch.object(processing, 'db', db):
            processing._maybe_enqueue_degraded_redetect(
                slug, 'ep1', URL, 'Episode One', 'A Podcast', None, None,
                {'detection_degraded': None}, {'detection_degraded': 'boom'})

        assert db._update_queue_status(row['id'], 'completed',
                                      expect_status='processing') is False
        claimed = db.claim_next_queued_episode()
        assert claimed['id'] == row['id']
        assert db.get_episode(slug, 'ep1')['reprocess_mode'] == 'llm'
        db.delete_podcast(slug)


class TestLowYieldRerunSurvives:
    """End to end over the real queue: the hook re-queues, the drainer files
    its verdict on the finished run, and the next claim gets the rerun."""

    def _seed_feed_history(self, db, slug):
        for i in range(3):
            db.upsert_episode(slug, f'baseline-{i}', original_url=URL,
                              status='processed', original_duration=3600.0,
                              new_duration=3000.0,
                              processed_at=f'2026-08-0{i + 1}T00:00:00Z')

    def test_hook_requeue_survives_and_is_claimed_next(self):
        db = Database()
        slug = 'verdict-low-yield-feed'
        row = _claimed_row(db, slug, 'flat-copy')
        self._seed_feed_history(db, slug)
        db.upsert_episode(slug, 'flat-copy', status='processed',
                          original_duration=3600.0, new_duration=3600.0,
                          processed_at='2026-08-04T00:00:00Z')
        db.set_setting('low_ad_yield_action', 'reprocess', is_default=False)

        with patch.object(processing, 'db', db), \
             patch.object(processing, 'status_service', MagicMock()):
            processing._maybe_fire_low_ad_yield_action(
                slug, 'flat-copy', URL, 'Episode One', 'A Podcast', None, None,
                {}, {'mode': 'auto'})

        episode = db.get_episode(slug, 'flat-copy')
        assert episode['low_yield_rerun_at']
        assert episode['reprocess_mode'] == 'reprocess'
        assert _row_status(db, row['id']) == 'pending'

        # The drainer wakes up to an episode row the hook set back to pending.
        assert db._update_queue_status(
            row['id'], 'failed', 'Processing ended with status: pending',
            expect_status='processing') is False
        assert db.claim_next_queued_episode()['id'] == row['id']

        db.clear_setting('low_ad_yield_action')
        db.delete_podcast(slug)


class TestDrainerErrorPathKeepsRequeue:
    """The drainer's own error handler closes the row too, and it used to do
    that unconditionally."""

    def test_an_exception_after_a_mid_run_requeue_leaves_the_row_claimable(self):
        db = Database()
        slug = 'verdict-error-path-feed'
        db.create_podcast(slug, 'https://example.com/feed.xml', 'A Podcast')
        db.upsert_episode(slug, 'ep1', original_url=URL, status='processing')
        db.upsert_episode_for_processing(slug, 'ep1', URL, 'Episode One')

        def _start(*args, **kwargs):
            # Stands in for a hook that re-queues the episode mid-run.
            db.upsert_episode_for_processing(slug, 'ep1', URL, 'Episode One')
            return True, 'started'

        iterations = {'n': 0}

        def _is_set():
            iterations['n'] += 1
            return iterations['n'] > 1

        with patch.object(background, 'db', db), \
             patch.object(background, 'shutdown_event') as ev, \
             patch('main_app.processing.start_background_processing', side_effect=_start), \
             patch('processing_timeouts.get_hard_timeout',
                   side_effect=RuntimeError('poll loop blew up')), \
             patch('offline_queue.offline_queue_tick'):
            ev.is_set.side_effect = _is_set
            ev.wait.return_value = False
            background.background_queue_processor()

        row = db.get_connection().execute(
            "SELECT id, status FROM auto_process_queue WHERE episode_id = 'ep1'"
        ).fetchone()
        assert row['status'] == 'pending'
        assert db.claim_next_queued_episode()['id'] == row['id']
        db.delete_podcast(slug)


def _drain_once(db, on_start, log):
    """Run one drainer iteration with a real DB and a stubbed run."""
    iterations = {'n': 0}

    def _is_set():
        iterations['n'] += 1
        return iterations['n'] > 1

    with patch.object(background, 'db', db), \
         patch.object(background, 'shutdown_event') as ev, \
         patch.object(background, 'refresh_logger', log), \
         patch('main_app.processing.start_background_processing', side_effect=on_start), \
         patch('processing_timeouts.get_hard_timeout', return_value=7200), \
         patch('offline_queue.offline_queue_tick'):
        ev.is_set.side_effect = _is_set
        ev.wait.return_value = False
        background.background_queue_processor()


class TestDrainerRerunAccounting:
    """finalize closes the claimed row on every success, so 'no row changed'
    on its own does not mean a rerun was queued."""

    def _seed(self, db, slug):
        db.create_podcast(slug, 'https://example.com/feed.xml', 'A Podcast')
        db.upsert_episode(slug, 'ep1', original_url=URL, status='pending')
        db.upsert_episode_for_processing(slug, 'ep1', URL, 'Episode One')

    def test_a_plain_success_logs_the_success_line(self):
        db = Database()
        slug = 'drainer-success-feed'
        self._seed(db, slug)
        log = MagicMock()

        def _run(*args, **kwargs):
            db.upsert_episode(slug, 'ep1', status='processed')
            db.close_queue_rows_for_episode(slug, 'ep1')
            return True, 'started'

        _drain_once(db, _run, log)

        messages = ' '.join(str(c) for c in log.info.call_args_list)
        assert 'completed successfully' in messages
        assert 'rerun was queued' not in messages
        db.delete_podcast(slug)

    def test_a_policy_requeue_logs_the_rerun_line_and_no_failure_narrative(self):
        db = Database()
        slug = 'drainer-rerun-feed'
        self._seed(db, slug)
        log = MagicMock()

        def _run(*args, **kwargs):
            db.upsert_episode(slug, 'ep1', status='processed')
            db.close_queue_rows_for_episode(slug, 'ep1')
            # The hook: requeue without touching the published episode row.
            db.upsert_episode(slug, 'ep1', reprocess_mode='llm',
                              reprocess_requested_at='2026-08-20T00:00:00Z',
                              reprocess_source='policy')
            db.upsert_episode_for_processing(slug, 'ep1', URL, 'Episode One',
                                             priority=-10)
            return True, 'started'

        _drain_once(db, _run, log)

        messages = ' '.join(str(c) for c in log.info.call_args_list)
        warnings = ' '.join(str(c) for c in log.warning.call_args_list)
        assert 'rerun was queued' in messages
        assert 'completed successfully' not in messages
        assert 'Processing ended with status' not in messages
        assert 'orphaned' not in warnings.lower()
        assert db.claim_next_queued_episode()['episode_id'] == 'ep1'
        db.delete_podcast(slug)


class TestPolicyRerunKeepsTheEpisodePublished:
    """A processed-only served feed builds from episodes.status, so a rerun
    that flipped the row to pending pulled the episode out of the feed."""

    def test_status_and_stored_detail_survive_the_fire(self):
        db = Database()
        slug = 'published-through-rerun-feed'
        db.create_podcast(slug, 'https://example.com/feed.xml', 'A Podcast')
        for i in range(3):
            db.upsert_episode(slug, f'baseline-{i}', original_url=URL,
                              status='processed', original_duration=3600.0,
                              new_duration=3000.0,
                              processed_at=f'2026-08-0{i + 1}T00:00:00Z')
        db.upsert_episode(slug, 'flat-copy', original_url=URL, status='processed',
                          original_duration=3600.0, new_duration=3600.0,
                          processed_at='2026-08-04T00:00:00Z')
        db.save_episode_details(slug, 'flat-copy', transcript_text='the stored transcript')
        db.set_setting('low_ad_yield_action', 'reprocess', is_default=False)

        with patch.object(processing, 'db', db), \
             patch.object(processing, 'status_service', MagicMock()):
            processing._maybe_fire_low_ad_yield_action(
                slug, 'flat-copy', URL, 'Episode One', 'A Podcast', None, None,
                {}, {'mode': 'auto'})

        episode = db.get_episode(slug, 'flat-copy')
        assert episode['status'] == 'processed'
        assert episode['low_yield_rerun_at']
        # The set the processed-only feed build filters on.
        statuses, _ = db.get_episode_statuses_for_podcast(slug)
        assert statuses['flat-copy'] == 'processed'
        assert db.has_transcript(slug, 'flat-copy')

        db.clear_setting('low_ad_yield_action')
        db.delete_podcast(slug)
