"""An episode row orphaned in 'processing' must heal without a restart.

The drainer's waiter polled only the row's status, so a row left behind by a
killed worker kept it there for the whole hard timeout (2h by default) before
requeuing, and the status itself was only reset at startup.
"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('orphan_row_test_')

from main_app import background


def test_periodic_block_resets_stuck_processing_rows():
    """Startup-only reconciliation left a row unprocessable until a restart."""
    ticks = {'n': 0}

    def fake_wait(timeout=None):
        ticks['n'] += 1
        return False

    mock_db = MagicMock()
    mock_db.claim_next_queued_episode.return_value = None
    mock_db.reset_orphaned_queue_items.return_value = (0, 0)
    mock_db.reset_failed_queue_items.return_value = 0

    with patch.object(background, 'db', mock_db), \
         patch.object(background, 'shutdown_event') as ev, \
         patch.object(background, 'reset_stuck_processing_episodes') as reset, \
         patch('offline_queue.offline_queue_tick'):
        # The periodic block runs every 10 iterations; give it 12.
        ev.is_set.side_effect = lambda: ticks['n'] >= 12
        ev.wait.side_effect = fake_wait
        background.background_queue_processor()

    reset.assert_called()


class TestWaiterOrphanDetection:
    """The waiter has to stop when nothing holds the processing lock."""

    def _drain_once(self, queue_says_processing, episode_status='processing'):
        """Run one drain iteration and return the refresh log calls."""
        episode = {'episode_id': 'a1b2c3d4e5f6', 'status': episode_status}
        queue_row = {
            'id': 7, 'podcast_slug': 'example-podcast',
            'episode_id': 'a1b2c3d4e5f6', 'original_url': 'https://e.test/a.mp3',
            'title': 'Episode One', 'podcast_title': 'Example Podcast',
            'published_at': None, 'description': None,
        }
        mock_queue = MagicMock()
        mock_queue.is_processing.return_value = queue_says_processing
        mock_db = MagicMock()
        mock_db.claim_next_queued_episode.side_effect = [queue_row, None]
        mock_db.is_auto_process_enabled_for_podcast.return_value = True
        mock_db.get_episode.return_value = episode

        stop_after = {'n': 0}

        def fake_wait(timeout=None):
            # Let the waiter poll a few times, then end the outer loop.
            stop_after['n'] += 1
            return stop_after['n'] > 6

        with patch.object(background, 'db', mock_db), \
             patch.object(background, 'shutdown_event') as ev, \
             patch('processing_queue.ProcessingQueue', return_value=mock_queue), \
             patch('main_app.processing.start_background_processing',
                   return_value=(True, 'started')), \
             patch('processing_timeouts.get_hard_timeout', return_value=7200), \
             patch.object(background, 'refresh_logger') as log, \
             patch('offline_queue.offline_queue_tick'):
            ev.is_set.side_effect = lambda: stop_after['n'] > 6
            ev.wait.side_effect = fake_wait
            background.background_queue_processor()

        return log, mock_db

    def test_orphan_breaks_out_instead_of_waiting_the_hard_timeout(self):
        log, mock_db = self._drain_once(queue_says_processing=False)

        warnings = ' '.join(str(c) for c in log.warning.call_args_list)
        assert 'orphaned' in warnings.lower()
        # Requeued, not failed: the work still needs doing.
        assert any(call.args[1] == 'pending'
                   for call in mock_db.update_queue_status.call_args_list)

    def test_a_live_job_is_left_alone(self):
        log, mock_db = self._drain_once(queue_says_processing=True)

        warnings = ' '.join(str(c) for c in log.warning.call_args_list)
        assert 'orphaned' not in warnings.lower()
