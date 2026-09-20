"""Admission must consult the resolved processing mode: a run that makes no
LLM call cannot be refused by a provider hold, and the dispatcher's blocked-slug
scan resolves the slug-independent route snapshot once per pass.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR',
                      tempfile.mkdtemp(prefix='admission-mode-test-'))

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('admission_mode_test_')
from main_app import background, db
from main_app.processing import _required_providers_for_admission

SLUG = 'admission-mode-feed'
EP = 'aa11bb22cc33'


def _snapshot(**routes):
    return {phase: {'provider_key': provider, 'configured_model': 'model-x',
                    'credential_slot': 'primary'}
            for phase, provider in routes.items()}


FULL_SNAPSHOT = _snapshot(detection='provider-a', review='provider-a',
                          verification='provider-a', chapters='provider-b')


@pytest.fixture
def feed():
    db.create_podcast(SLUG, 'https://example.com/feed.xml', title='Admission Mode')
    db.upsert_episode(SLUG, EP, title='Episode', status='discovered',
                      original_url='https://example.com/e.mp3')
    yield
    db.delete_podcast(SLUG)
    db.get_connection().execute("DELETE FROM auto_process_queue")
    db.get_connection().commit()


def _set_mode(mode):
    from config import PROCESSING_MODE_COLUMN_UPDATES
    db.update_podcast(SLUG, **PROCESSING_MODE_COLUMN_UPDATES[mode])


class TestRequiredProvidersHonorsMode:
    def test_standard_feed_still_requires_every_enabled_phase(self, feed):
        required = _required_providers_for_admission(SLUG, EP, snapshot=FULL_SNAPSHOT)
        assert ('provider-a', 'primary') in required

    def test_passthrough_feed_requires_nothing(self, feed):
        _set_mode('passthrough')
        assert _required_providers_for_admission(
            SLUG, EP, snapshot=FULL_SNAPSHOT) == []

    def test_per_episode_passthrough_override_requires_nothing(self, feed):
        db.set_episodes_passthrough(SLUG, [EP], True)
        assert _required_providers_for_admission(
            SLUG, EP, snapshot=FULL_SNAPSHOT) == []

    def test_override_does_not_leak_to_other_episodes(self, feed):
        other = 'bb22cc33dd44'
        db.upsert_episode(SLUG, other, title='Other', status='discovered',
                          original_url='https://example.com/o.mp3')
        db.set_episodes_passthrough(SLUG, [EP], True)
        assert ('provider-a', 'primary') in _required_providers_for_admission(
            SLUG, other, snapshot=FULL_SNAPSHOT)

    def test_skip_detection_requires_only_the_chapters_provider(self, feed):
        _set_mode('skip_detection')
        assert _required_providers_for_admission(
            SLUG, EP, snapshot=FULL_SNAPSHOT) == [('provider-b', 'primary')]

    def test_cue_only_requires_only_the_chapters_provider(self, feed):
        _set_mode('cue_only')
        assert _required_providers_for_admission(
            SLUG, EP, snapshot=FULL_SNAPSHOT) == [('provider-b', 'primary')]

    def test_chapters_off_leaves_a_cue_only_run_needing_nothing(self, feed):
        _set_mode('cue_only')
        db.set_setting('chapters_enabled', 'false')
        try:
            assert _required_providers_for_admission(
                SLUG, EP, snapshot=FULL_SNAPSHOT) == []
        finally:
            db.set_setting('chapters_enabled', 'true')

    def test_global_skip_verification_removes_verification_provider(self, feed):
        db.set_setting('skip_second_pass', 'true')
        try:
            required = _required_providers_for_admission(
                SLUG, EP, snapshot=FULL_SNAPSHOT)
            assert ('provider-a', 'primary') in required
            assert ('provider-b', 'primary') in required
            assert len(required) == 2
        finally:
            db.set_setting('skip_second_pass', 'false')

    def test_feed_can_run_verification_when_global_default_skips(self, feed):
        snapshot = _snapshot(detection='provider-a', review='provider-a',
                             verification='provider-c', chapters='provider-b')
        db.set_setting('skip_second_pass', 'true')
        db.update_podcast(SLUG, skip_second_pass=0)
        try:
            required = _required_providers_for_admission(
                SLUG, EP, snapshot=snapshot)
            assert ('provider-c', 'primary') in required
        finally:
            db.set_setting('skip_second_pass', 'false')

    def test_global_chapter_mode_off_removes_chapters_provider(self, feed):
        db.set_setting('chapters_mode', 'off')
        try:
            required = _required_providers_for_admission(
                SLUG, EP, snapshot=FULL_SNAPSHOT)
            assert required == [('provider-a', 'primary')]
        finally:
            db.set_setting('chapters_mode', 'auto')

    def test_feed_can_generate_chapters_when_global_default_is_off(self, feed):
        db.set_setting('chapters_mode', 'off')
        db.update_podcast(SLUG, chapters_mode='generate')
        try:
            required = _required_providers_for_admission(
                SLUG, EP, snapshot=FULL_SNAPSHOT)
            assert ('provider-b', 'primary') in required
        finally:
            db.set_setting('chapters_mode', 'auto')


class TestBlockedQueueEntries:
    def _queue(self, episode_id):
        db.upsert_episode_for_processing(SLUG, episode_id,
                                         'https://example.com/e.mp3', title='E')

    def test_passthrough_feed_is_not_blocked_by_a_held_provider(self, feed, monkeypatch):
        _set_mode('passthrough')
        self._queue(EP)
        monkeypatch.setattr('main_app.processing._resolve_route_snapshot',
                            lambda: FULL_SNAPSHOT)
        assert background._blocked_queue_entries(db, {('provider-a', 'primary')}) == set()

    def test_standard_feed_is_blocked_by_a_held_provider(self, feed, monkeypatch):
        self._queue(EP)
        monkeypatch.setattr('main_app.processing._resolve_route_snapshot',
                            lambda: FULL_SNAPSHOT)
        assert background._blocked_queue_entries(
            db, {('provider-a', 'primary')}) == {(SLUG, EP)}

    def test_only_the_blocked_episode_of_a_feed_is_listed(self, feed, monkeypatch):
        other = 'bb22cc33dd44'
        db.upsert_episode(SLUG, other, title='Other', status='discovered',
                          original_url='https://example.com/o.mp3')
        db.set_episodes_passthrough(SLUG, [other], True)
        self._queue(EP)
        self._queue(other)
        monkeypatch.setattr('main_app.processing._resolve_route_snapshot',
                            lambda: FULL_SNAPSHOT)
        assert background._blocked_queue_entries(
            db, {('provider-a', 'primary')}) == {(SLUG, EP)}

    def test_route_snapshot_is_resolved_once_per_pass(self, feed, monkeypatch):
        extra = [f'{SLUG}-{i}' for i in range(4)]
        for i, slug in enumerate(extra):
            db.create_podcast(slug, f'https://example.com/{i}.xml', title=f'F{i}')
            db.upsert_episode(slug, EP, title='E', status='discovered',
                              original_url='https://example.com/e.mp3')
            db.upsert_episode_for_processing(slug, EP, 'https://example.com/e.mp3',
                                             title='E')
        self._queue(EP)
        calls = {'n': 0}

        def _counting():
            calls['n'] += 1
            return FULL_SNAPSHOT

        monkeypatch.setattr('main_app.processing._resolve_route_snapshot', _counting)
        try:
            background._blocked_queue_entries(db, {('provider-a', 'primary')})
        finally:
            for slug in extra:
                db.delete_podcast(slug)
        assert calls['n'] == 1


class TestDispatcherClaimsAroundBlockedEpisodes:
    """A held provider must not stall a feed's admissible episodes, and the
    dispatcher must reach them without bouncing a blocked claim first.
    """

    def test_admissible_episode_is_claimed_without_a_bounce(self, feed, monkeypatch):
        from datetime import datetime, timedelta, timezone
        from unittest.mock import MagicMock, patch
        from rate_limit_hold import clear_hold, record_hold_until

        blocked_ep, open_ep = EP, 'bb22cc33dd44'
        db.upsert_episode(SLUG, open_ep, title='Open', status='discovered',
                          original_url='https://example.com/o.mp3')
        db.set_episodes_passthrough(SLUG, [open_ep], True)
        # Blocked first in dequeue order, so a slug-wide skip would stall both
        # and a naive claim would bounce on it before reaching the open one.
        db.upsert_episode_for_processing(SLUG, blocked_ep,
                                         'https://example.com/e.mp3', title='B')
        db.upsert_episode_for_processing(SLUG, open_ep,
                                         'https://example.com/o.mp3', title='O')
        future = (datetime.now(timezone.utc)
                  + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        record_hold_until(db, 'provider-a', future)

        monkeypatch.setattr('main_app.processing._resolve_route_snapshot',
                            lambda: FULL_SNAPSHOT)
        # Silences the hold tick and the probe, which would otherwise reap or
        # clear the hold under test.
        monkeypatch.setattr(background, '_run_tick', lambda fn, name: None)
        monkeypatch.setattr(background, 'IDLE_WAIT_SECONDS', 0.01)
        monkeypatch.setattr(background, 'HELD_IDLE_WAIT_SECONDS', 0.02)
        monkeypatch.setattr(background, '_wait_for_claimed_episode',
                            lambda *a, **kw: None)

        started, waits = [], []
        calls = {'n': 0}

        def _fake_start(slug, episode_id, *a, **kw):
            started.append(episode_id)
            return True, 'started'

        def _fake_wait(timeout=None):
            waits.append(timeout)
            calls['n'] += 1
            return calls['n'] >= 3

        stop = MagicMock()
        stop.is_set.side_effect = lambda: calls['n'] >= 3
        stop.wait.side_effect = _fake_wait
        pool = MagicMock()
        pool.active = True
        pool.max_episodes = 2

        try:
            with patch.object(background, 'shutdown_event', stop), \
                    patch.object(background, 'get_pool', lambda: pool), \
                    patch('main_app.processing.start_background_processing',
                          _fake_start):
                background.background_queue_processor()
        finally:
            clear_hold(db, 'provider-a:primary')

        assert started == [open_ep]
        assert 30 not in waits

    def test_both_sides_of_a_blocked_row_are_claimed(self, feed, monkeypatch):
        """The dispatcher claims up to max_episodes rows per pass, so a held
        row between two eligible ones must not bounce the second claim."""
        from datetime import datetime, timedelta, timezone
        from unittest.mock import MagicMock, patch
        from rate_limit_hold import clear_hold, record_hold_until

        blocked_ep, first_ep, second_ep = EP, 'bb22cc33dd44', 'cc33dd44ee55'
        for episode_id in (first_ep, second_ep):
            db.upsert_episode(SLUG, episode_id, title='Open', status='discovered',
                              original_url='https://example.com/o.mp3')
        db.set_episodes_passthrough(SLUG, [first_ep, second_ep], True)
        db.upsert_episode_for_processing(SLUG, first_ep, 'https://example.com/o.mp3',
                                         title='O1', priority=10)
        db.upsert_episode_for_processing(SLUG, blocked_ep, 'https://example.com/e.mp3',
                                         title='B', priority=5)
        db.upsert_episode_for_processing(SLUG, second_ep, 'https://example.com/o.mp3',
                                         title='O2', priority=1)
        future = (datetime.now(timezone.utc)
                  + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        record_hold_until(db, 'provider-a', future)

        monkeypatch.setattr('main_app.processing._resolve_route_snapshot',
                            lambda: FULL_SNAPSHOT)
        monkeypatch.setattr(background, '_run_tick', lambda fn, name: None)
        monkeypatch.setattr(background, 'IDLE_WAIT_SECONDS', 0.01)
        monkeypatch.setattr(background, 'HELD_IDLE_WAIT_SECONDS', 0.02)
        monkeypatch.setattr(background, '_wait_for_claimed_episode',
                            lambda *a, **kw: None)

        started, waits = [], []
        calls = {'n': 0}

        def _fake_start(slug, episode_id, *a, **kw):
            started.append(episode_id)
            return True, 'started'

        def _fake_wait(timeout=None):
            waits.append(timeout)
            calls['n'] += 1
            return calls['n'] >= 2

        stop = MagicMock()
        stop.is_set.side_effect = lambda: calls['n'] >= 2
        stop.wait.side_effect = _fake_wait
        pool = MagicMock()
        pool.active = True
        pool.max_episodes = 3

        try:
            with patch.object(background, 'shutdown_event', stop), \
                    patch.object(background, 'get_pool', lambda: pool), \
                    patch('main_app.processing.start_background_processing',
                          _fake_start):
                background.background_queue_processor()
        finally:
            clear_hold(db, 'provider-a:primary')

        assert started == [first_ep, second_ep]
        assert 30 not in waits

    def test_a_fully_blocked_scan_waits_before_rescanning(self, feed, monkeypatch):
        """Rescanning every pending row on every idle tick is wasted work while
        a hold blocks all of them."""
        from datetime import datetime, timedelta, timezone
        from unittest.mock import MagicMock, patch
        from rate_limit_hold import clear_hold, record_hold_until

        db.upsert_episode_for_processing(SLUG, EP, 'https://example.com/e.mp3',
                                         title='B')
        future = (datetime.now(timezone.utc)
                  + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        record_hold_until(db, 'provider-a', future)

        monkeypatch.setattr('main_app.processing._resolve_route_snapshot',
                            lambda: FULL_SNAPSHOT)
        monkeypatch.setattr(background, '_run_tick', lambda fn, name: None)
        monkeypatch.setattr(background, 'IDLE_WAIT_SECONDS', 0.01)
        monkeypatch.setattr(background, 'HELD_IDLE_WAIT_SECONDS', 0.02)

        waits = []
        calls = {'n': 0}

        def _fake_wait(timeout=None):
            waits.append(timeout)
            calls['n'] += 1
            return calls['n'] >= 2

        stop = MagicMock()
        stop.is_set.side_effect = lambda: calls['n'] >= 2
        stop.wait.side_effect = _fake_wait
        pool = MagicMock()
        pool.active = True
        pool.max_episodes = 2

        try:
            with patch.object(background, 'shutdown_event', stop), \
                    patch.object(background, 'get_pool', lambda: pool):
                background.background_queue_processor()
        finally:
            clear_hold(db, 'provider-a:primary')

        assert waits == [0.02, 0.02]


class TestBlockedScanPaging:
    """Scoring only the first page would leave the row just past it unscored,
    and the claim would hand that blocked row back and bounce."""

    def test_scan_pages_past_a_fully_blocked_window(self, feed, monkeypatch):
        episode_ids = [f'cc33dd44ee{i}{i}' for i in range(4)]
        for episode_id in episode_ids:
            db.upsert_episode(SLUG, episode_id, title='E', status='discovered',
                              original_url='https://example.com/e.mp3')
            db.upsert_episode_for_processing(SLUG, episode_id,
                                             'https://example.com/e.mp3', title='E')
        monkeypatch.setattr(background, 'BLOCKED_SCAN_PAGE', 2)
        monkeypatch.setattr('main_app.processing._resolve_route_snapshot',
                            lambda: FULL_SNAPSHOT)
        blocked = background._blocked_queue_entries(db, {('provider-a', 'primary')})
        assert blocked == {(SLUG, episode_id) for episode_id in episode_ids}

    def test_scan_scores_the_rows_behind_an_eligible_one(self, feed, monkeypatch):
        """One pass can claim several rows, so a blocked row behind an eligible
        one still has to be listed or that claim bounces."""
        open_ep, later_ep = 'ee55ff66aa11', 'ff66aa77bb22'
        for episode_id in (open_ep, later_ep):
            db.upsert_episode(SLUG, episode_id, title='E', status='discovered',
                              original_url='https://example.com/e.mp3')
        db.set_episodes_passthrough(SLUG, [open_ep], True)
        db.upsert_episode_for_processing(SLUG, open_ep, 'https://example.com/e.mp3',
                                         title='O', priority=10)
        db.upsert_episode_for_processing(SLUG, later_ep, 'https://example.com/e.mp3',
                                         title='L', priority=5)
        monkeypatch.setattr('main_app.processing._resolve_route_snapshot',
                            lambda: FULL_SNAPSHOT)
        assert background._blocked_queue_entries(
            db, {('provider-a', 'primary')}) == {(SLUG, later_ep)}

    def test_scan_issues_no_per_row_episode_query(self, feed, monkeypatch):
        """Mode columns ride on the pending row, so scoring reads no extra row."""
        from database import Database
        for i in range(3):
            episode_id = f'aa77bb88cc{i}{i}'
            db.upsert_episode(SLUG, episode_id, title='E', status='discovered',
                              original_url='https://example.com/e.mp3')
            db.upsert_episode_for_processing(SLUG, episode_id,
                                             'https://example.com/e.mp3', title='E')
        seen = []
        for name in ('get_podcast_by_slug', 'get_podcast_row', 'get_episode_state'):
            monkeypatch.setattr(
                Database, name,
                lambda self, *a, _name=name, **kw: seen.append(_name))
        monkeypatch.setattr('main_app.processing._resolve_route_snapshot',
                            lambda: FULL_SNAPSHOT)

        assert len(background._blocked_queue_entries(
            db, {('provider-a', 'primary')})) == 3
        assert seen == []

    def test_scan_stops_at_the_row_cap(self, feed, monkeypatch):
        for i in range(4):
            episode_id = f'dd44ee55ff{i}{i}'
            db.upsert_episode(SLUG, episode_id, title='E', status='discovered',
                              original_url='https://example.com/e.mp3')
            db.upsert_episode_for_processing(SLUG, episode_id,
                                             'https://example.com/e.mp3', title='E')
        monkeypatch.setattr(background, 'BLOCKED_SCAN_PAGE', 2)
        monkeypatch.setattr(background, 'BLOCKED_SCAN_MAX_ROWS', 2)
        monkeypatch.setattr('main_app.processing._resolve_route_snapshot',
                            lambda: FULL_SNAPSHOT)
        assert len(background._blocked_queue_entries(
            db, {('provider-a', 'primary')})) == 2


class TestLegacyUnscopedHold:
    """The pre-scoping marker blocks every account, but work that needs no
    account at all must still dispatch instead of stalling the whole queue.
    """

    def test_passthrough_episode_is_not_blocked_by_the_legacy_marker(
            self, feed, monkeypatch):
        db.set_episodes_passthrough(SLUG, [EP], True)
        db.upsert_episode_for_processing(SLUG, EP, 'https://example.com/e.mp3',
                                         title='E')
        monkeypatch.setattr('main_app.processing._resolve_route_snapshot',
                            lambda: FULL_SNAPSHOT)
        assert background._blocked_queue_entries(
            db, set(), legacy_hold=True) == set()

    def test_standard_episode_is_blocked_by_the_legacy_marker(self, feed, monkeypatch):
        db.upsert_episode_for_processing(SLUG, EP, 'https://example.com/e.mp3',
                                         title='E')
        monkeypatch.setattr('main_app.processing._resolve_route_snapshot',
                            lambda: FULL_SNAPSHOT)
        assert background._blocked_queue_entries(
            db, set(), legacy_hold=True) == {(SLUG, EP)}

    def test_dispatcher_keeps_passthrough_work_moving_under_a_legacy_hold(
            self, feed, monkeypatch):
        from datetime import datetime, timedelta, timezone
        from unittest.mock import MagicMock, patch

        blocked_ep, open_ep = EP, 'bb22cc33dd44'
        db.upsert_episode(SLUG, open_ep, title='Open', status='discovered',
                          original_url='https://example.com/o.mp3')
        db.set_episodes_passthrough(SLUG, [open_ep], True)
        db.upsert_episode_for_processing(SLUG, blocked_ep,
                                         'https://example.com/e.mp3', title='B')
        db.upsert_episode_for_processing(SLUG, open_ep,
                                         'https://example.com/o.mp3', title='O')
        future = (datetime.now(timezone.utc)
                  + timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        db.set_setting('rate_limit_hold_until', future)

        monkeypatch.setattr('main_app.processing._resolve_route_snapshot',
                            lambda: FULL_SNAPSHOT)
        monkeypatch.setattr(background, '_run_tick', lambda fn, name: None)
        monkeypatch.setattr(background, 'IDLE_WAIT_SECONDS', 0.01)
        monkeypatch.setattr(background, '_wait_for_claimed_episode',
                            lambda *a, **kw: None)

        started = []
        calls = {'n': 0}

        def _fake_start(slug, episode_id, *a, **kw):
            started.append(episode_id)
            return True, 'started'

        def _fake_wait(timeout=None):
            calls['n'] += 1
            return calls['n'] >= 3

        stop = MagicMock()
        stop.is_set.side_effect = lambda: calls['n'] >= 3
        stop.wait.side_effect = _fake_wait
        pool = MagicMock()
        pool.active = True
        pool.max_episodes = 2

        try:
            with patch.object(background, 'shutdown_event', stop), \
                    patch.object(background, 'get_pool', lambda: pool), \
                    patch('main_app.processing.start_background_processing',
                          _fake_start):
                background.background_queue_processor()
        finally:
            db.set_setting('rate_limit_hold_until', '')

        assert started == [open_ep]
