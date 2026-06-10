"""Regression test for the history ad-count bug.

Before this fix, `_record_history_and_event` recorded
``ads_detected=len(ads_to_remove)`` but ignored the ``verification_count``
it received as a parameter. The episodes table got the total
(``pass1_cut_count + verification_count``) via
``_persist_episode_state``, but the history table and the downstream
``EVENT_EPISODE_PROCESSED`` webhook got the pass-1-only count.

User-visible symptom: the Settings -> History page (and the corresponding
``/api/v1/history`` endpoint) showed pass-1 cuts only. Episodes where
verification re-cut found additional ads were undercounted by exactly the
verification re-cut count.

Also covers the matching log-line bug in ``_log_completion_summary``
where the "Complete: N ads removed" message used the same wrong value.
"""
import atexit
import logging
import os
import shutil
import sys
import tempfile
from unittest.mock import MagicMock, patch

# Boot pattern from test_feed_304_refresh: create a temp DATA_DIR and
# bind it to Database/Storage defaults before importing main_app, which
# otherwise tries to mkdir /app/data at module-load.
_test_data_dir = tempfile.mkdtemp(prefix='history_ad_count_test_')
os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ['DATA_DIR'] = _test_data_dir

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import database
import storage as storage_mod

# Mutating __defaults__ on shared classes leaks across the pytest session,
# but every other test file that uses this pattern leaks the same way and
# none restore -- per-file snapshot-and-restore would only paper over the
# symptom. Suite-wide cleanup belongs in conftest.py if/when it matters.
database.Database._instance = None
database.Database.__init__.__defaults__ = (_test_data_dir,)
database.Database.__new__.__defaults__ = (_test_data_dir,)
storage_mod.Storage.__init__.__defaults__ = (_test_data_dir,)

atexit.register(shutil.rmtree, _test_data_dir, ignore_errors=True)

from main_app.processing import _record_history_and_event, _log_completion_summary


def _make_db():
    db = MagicMock()
    db.get_podcast_by_slug.return_value = {'id': 42, 'title': 'Daily Show'}
    return db


def _make_token_totals():
    return {'input_tokens': 100, 'output_tokens': 50, 'cost': 0.01}


class TestHistoryAdCountIncludesVerification:
    """history.ads_detected = pass1_cut_count + verification_count.

    This is the field rendered by the Settings -> History page and the
    /api/v1/history endpoint. It is also the field the EVENT_EPISODE_PROCESSED
    webhook reads via ``webhook_service`` when populating
    ``episode.ads_removed`` in the payload.
    """

    def test_records_total_not_just_pass_1(self):
        db = _make_db()
        verification_count = 3

        # _record_history_and_event also fires a webhook; stub it so the
        # test isolates the DB write contract.
        with patch('main_app.processing.fire_event'):
            _record_history_and_event(
                slug='daily-show', episode_id='ep1',
                episode_title='Episode One',
                podcast_name='Daily Show',
                pass1_cut_count=2,
                verification_count=verification_count,
                original_duration=3600.0, new_duration=3300.0,
                processing_time=120.0,
                token_totals=_make_token_totals(),
                db=db,
            )

        db.record_processing_history.assert_called_once()
        kwargs = db.record_processing_history.call_args.kwargs
        assert kwargs['ads_detected'] == 5, (
            f"history.ads_detected should be pass1_cut_count+verification_count "
            f"(2+3=5), got {kwargs['ads_detected']}. Pre-2.5.28 this was 2."
        )

    def test_zero_verification_records_pass_1_count(self):
        """No verification re-cut: total equals pass-1 count (regression
        guard so the fix doesn't break the simple case)."""
        db = _make_db()

        with patch('main_app.processing.fire_event'):
            _record_history_and_event(
                slug='s', episode_id='e', episode_title='t', podcast_name='p',
                pass1_cut_count=1, verification_count=0,
                original_duration=3600.0, new_duration=3570.0,
                processing_time=60.0, token_totals=_make_token_totals(), db=db,
            )

        kwargs = db.record_processing_history.call_args.kwargs
        assert kwargs['ads_detected'] == 1

    def test_zero_pass_1_records_only_verification(self):
        """Pass-1 reviewer rejected everything but verification found ads:
        this is exactly the ``a40d43aec65b`` scenario that originally
        prompted the audit. history.ads_detected must reflect the
        verification cuts, not the pre-reviewer 0."""
        db = _make_db()

        with patch('main_app.processing.fire_event'):
            _record_history_and_event(
                slug='s', episode_id='e', episode_title='t', podcast_name='p',
                pass1_cut_count=0, verification_count=1,
                original_duration=3600.0, new_duration=3540.0,
                processing_time=60.0, token_totals=_make_token_totals(), db=db,
            )

        kwargs = db.record_processing_history.call_args.kwargs
        assert kwargs['ads_detected'] == 1


class TestCompletionLogLineIncludesVerification:
    """The "Complete: N ads removed, T.s" log line must report total cuts,
    not pass-1 only. This is the log the user reads to understand episode
    outcome and the source of the misleading ``0 ads removed`` line we saw
    on episodes where pass 1 rejected everything but verification re-cut
    found 1."""

    def test_log_includes_verification_count(self, caplog):
        db = MagicMock()
        # Mock get_episode_token_totals so we don't read/reset the
        # module-level _episode_accumulator that other tests share.
        token_stub = {'input_tokens': 0, 'output_tokens': 0, 'cost': 0.0}
        with caplog.at_level(logging.INFO, logger='podcast.audio'), \
                patch('main_app.processing.get_episode_token_totals',
                      return_value=token_stub):
            _log_completion_summary(
                slug='s', episode_id='e',
                pass1_cut_count=1,
                verification_count=2,
                original_duration=3600.0, new_duration=3300.0,
                processing_time=120.0, db=db,
            )

        messages = [r.message for r in caplog.records]
        complete_line = next((m for m in messages if 'Complete:' in m), None)
        assert complete_line is not None, f"no Complete: line in {messages}"
        assert '3 ads removed' in complete_line, (
            f"expected '3 ads removed' (1 pass-1 + 2 verification) in: {complete_line}"
        )

    def test_log_zero_total_when_no_cuts_anywhere(self, caplog):
        db = MagicMock()
        token_stub = {'input_tokens': 0, 'output_tokens': 0, 'cost': 0.0}
        with caplog.at_level(logging.INFO, logger='podcast.audio'), \
                patch('main_app.processing.get_episode_token_totals',
                      return_value=token_stub):
            _log_completion_summary(
                slug='s', episode_id='e',
                pass1_cut_count=0, verification_count=0,
                original_duration=3600.0, new_duration=3600.0,
                processing_time=60.0, db=db,
            )

        complete_line = next(
            (r.message for r in caplog.records if 'Complete:' in r.message), None
        )
        assert complete_line is not None
        assert '0 ads removed' in complete_line
