"""A failed YouTube download is retried or given up by YouTube's verdict, not by its wording."""
import contextlib
import unittest
from unittest.mock import patch

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('youtube_download_failures_')
import main_app.processing as processing  # noqa: E402
from main_app.pipeline import common, failure, finalize  # noqa: E402
from main_app.pipeline import sponsorblock as sb_stage  # noqa: E402
from utils.constants import EpisodeStatus  # noqa: E402
from utils.errors import YouTubeAccessBlockedError, YouTubeVideoUnavailableError  # noqa: E402
from youtube import download  # noqa: E402

BOT = ("ERROR: [youtube] vid000000001: Sign in to confirm you’re not a bot. Use --cookies-from-browser or "
       "--cookies for the authentication.")
FORBIDDEN = 'ERROR: unable to download video data: HTTP Error 403: Forbidden'
MEMBERS = 'ERROR: [youtube] vid000000001: Join this channel to get access to members-only content like this video'
AGE = 'ERROR: [youtube] vid000000001: Sign in to confirm your age. Use --cookies for the authentication.'


def _download_error(stderr):
    """The DownloadError download.download raises for this yt-dlp stderr."""
    return download.DownloadError(stderr, needs_update=False, kind=download.classify_error(stderr))


def _acquire_error(exc):
    """What acquire_audio raises when the download fails with exc."""
    with patch.object(sb_stage.storage, 'get_original_path', return_value=None), \
         patch.object(sb_stage.download, 'download', side_effect=exc), \
         patch.object(sb_stage.ytdlp, 'update_after_failure') as update, \
         patch.object(common, '_publish_status'), \
         patch.object(common, '_measure_run_stage', side_effect=lambda *_: contextlib.nullcontext()):
        try:
            sb_stage.acquire_audio('yt-feed', 'ep1', 'vid000000001', {}, False, 'en')
        except Exception as raised:  # noqa: BLE001 - the test inspects whatever came out
            return raised, update
    raise AssertionError('acquire_audio did not raise')


class TestDownloadFailureType(unittest.TestCase):
    def test_bot_check_becomes_a_transient_block(self):
        raised, _ = _acquire_error(_download_error(BOT))
        self.assertIsInstance(raised, YouTubeAccessBlockedError)
        self.assertIn('not a bot', str(raised))
        self.assertIn('cookies or a proxy', str(raised))
        self.assertTrue(common.is_transient_error(raised))
        self.assertTrue(common.is_known_upstream_error(raised))

    def test_403_becomes_a_transient_block_and_updates_yt_dlp(self):
        exc = download.DownloadError(FORBIDDEN, needs_update=True, kind=download.classify_error(FORBIDDEN))
        raised, update = _acquire_error(exc)
        self.assertIsInstance(raised, YouTubeAccessBlockedError)
        self.assertTrue(common.is_transient_error(raised))
        update.assert_called_once()

    def test_members_only_is_permanent(self):
        raised, _ = _acquire_error(_download_error(MEMBERS))
        self.assertIsInstance(raised, YouTubeVideoUnavailableError)
        self.assertIn('members-only', str(raised))
        self.assertFalse(common.is_transient_error(raised))

    def test_unrecognized_failure_keeps_the_plain_message(self):
        raised, _ = _acquire_error(_download_error(AGE))
        self.assertIs(type(raised), Exception)
        self.assertEqual(str(raised), AGE)
        # The age gate needs cookies; retrying without them never helps, as before.
        self.assertFalse(common.is_transient_error(raised))


class TestRetryLadder(unittest.TestCase):
    def _fail(self, error, retry_count=0):
        episode_data = {'retry_count': retry_count}
        totals = {'input_tokens': 0, 'output_tokens': 0, 'cost': 0.0}
        with patch.object(processing, 'db') as db, \
             patch.object(finalize, '_record_history_row'), \
             patch.object(common, '_require_publication_owner'), \
             patch.object(common, '_publish_status'), \
             patch.object(finalize, 'get_episode_token_totals', return_value=totals), \
             patch.object(failure, 'get_episode_token_totals', return_value=totals), \
             patch.object(failure, 'is_offline_queue_enabled', return_value=False), \
             patch.object(finalize, 'fire_event'), \
             patch.object(failure, 'fire_event'):
            db.get_episode.return_value = episode_data
            failure._handle_processing_failure(
                'yt-feed', 'ep1', 'An Episode', 'A Channel', episode_data, error, start_time=0.0)
            return db.upsert_episode.call_args.kwargs

    def test_bot_check_is_retried(self):
        raised, _ = _acquire_error(_download_error(BOT))
        row = self._fail(raised)
        self.assertEqual(row['status'], EpisodeStatus.FAILED.value)
        self.assertEqual(row['retry_count'], 1)

    def test_bot_check_still_ends_after_the_last_retry(self):
        raised, _ = _acquire_error(_download_error(BOT))
        row = self._fail(raised, retry_count=failure.MAX_EPISODE_RETRIES - 1)
        self.assertEqual(row['status'], EpisodeStatus.PERMANENTLY_FAILED.value)

    def test_members_only_fails_at_once(self):
        raised, _ = _acquire_error(_download_error(MEMBERS))
        row = self._fail(raised)
        self.assertEqual(row['status'], EpisodeStatus.PERMANENTLY_FAILED.value)
        self.assertEqual(row['retry_count'], 0)


if __name__ == '__main__':
    unittest.main()
