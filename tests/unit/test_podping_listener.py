"""Tests for PodpingListener and podping_listener_loop."""
import json
import logging
import threading
import time
from unittest.mock import Mock

import requests

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('podping_listener_test_', passphrase='podping-listener-test-passphrase')

from podping_listener import (
    PodpingListener,
    extract_podping_events,
    podping_listener_loop,
    PODPING_NODES,
    ACTIONABLE_REASONS,
    MAX_CATCHUP_BLOCKS,
)


class ScriptedRpc:
    """Dict-dispatch fake rpc: method -> static value, Exception, or a
    callable(params) -> value for scenarios that vary by call."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, method, params):
        self.calls.append((method, params))
        value = self.responses.get(method)
        if isinstance(value, Exception):
            raise value
        if callable(value):
            return value(params)
        return value


class FakeDb:
    def __init__(self, podcasts=None, declarations=None, settings=None):
        self.podcasts = podcasts or []
        self.declarations = declarations or {}
        self.stamped_slugs = []
        self.recorded_hosts = []
        self.settings = dict(settings or {})
        self.setting_writes = []
        self.heavy_podcast_reads = 0
        self.record_fails = False

    def get_all_podcasts(self):
        self.heavy_podcast_reads += 1
        return self.podcasts

    def get_podcast_feed_urls(self):
        return [{'slug': p['slug'], 'source_url': p.get('source_url')}
                for p in self.podcasts]

    def get_all_podping_declarations(self):
        return self.declarations

    def set_last_podping_at(self, slug):
        self.stamped_slugs.append(slug)

    def record_podping_hosts(self, counts):
        if self.record_fails:
            raise RuntimeError('db down')
        self.recorded_hosts.append(dict(counts))

    def get_setting(self, key):
        return self.settings.get(key)

    def set_setting(self, key, value, is_default=False):
        self.settings[key] = value
        self.setting_writes.append((key, value))


def _podping_op(auths, payload):
    return {
        'operations': [
            ['custom_json', {
                'id': 'podping',
                'required_posting_auths': auths,
                'json': json.dumps(payload),
            }]
        ]
    }


class TestFeedAcceptance:
    """Per-feed authorization from the upstream <podcast:hiveAccount> tags."""

    def _listener(self, declarations):
        db = FakeDb(
            podcasts=[{'slug': 'my-show', 'source_url': 'https://feeds.example.com/show'}],
            declarations=declarations)
        listener = PodpingListener(db=db, refresh=Mock(), sleep=lambda s: None)
        listener._refresh_feed_map()
        return listener

    def test_undeclared_feed_accepts_any_sender(self):
        listener = self._listener({})
        assert listener._feed_accepts('my-show', {'podping.ddd'}) is True

    def test_declared_account_accepts_matching_sender(self):
        listener = self._listener({
            'my-show': {'uses_podping': True, 'hive_accounts': ['podping.aaa']}})
        assert listener._feed_accepts('my-show', {'podping.aaa'}) is True

    def test_declared_account_rejects_other_sender(self):
        listener = self._listener({
            'my-show': {'uses_podping': True, 'hive_accounts': ['podping.aaa']}})
        assert listener._feed_accepts('my-show', {'attacker'}) is False

    def test_any_declared_account_may_match(self):
        listener = self._listener({
            'my-show': {'uses_podping': True,
                        'hive_accounts': ['podping.aaa', 'podping.bbb']}})
        assert listener._feed_accepts('my-show', {'podping.bbb'}) is True

    def test_opt_out_rejects_even_a_declared_account(self):
        listener = self._listener({
            'my-show': {'uses_podping': False, 'hive_accounts': ['podping.aaa']}})
        assert listener._feed_accepts('my-show', {'podping.aaa'}) is False

    def test_declared_true_with_no_accounts_accepts_any_sender(self):
        listener = self._listener({
            'my-show': {'uses_podping': True, 'hive_accounts': []}})
        assert listener._feed_accepts('my-show', {'podping.ddd'}) is True

    def test_missing_auths_rejected_when_accounts_declared(self):
        listener = self._listener({
            'my-show': {'uses_podping': True, 'hive_accounts': ['podping.aaa']}})
        assert listener._feed_accepts('my-show', set()) is False


class TestNodeRotation:
    def test_increments_node_index_on_request_exception(self):
        rpc = ScriptedRpc({'some_method': requests.RequestException('boom')})
        sleep_calls = []
        listener = PodpingListener(rpc=rpc, sleep=sleep_calls.append)
        start_index = listener.node_index

        result = listener._call_rpc('some_method', [])

        assert result is None
        assert listener.node_index == (start_index + 1) % len(PODPING_NODES)
        assert sleep_calls == [5]

    def test_invalid_shape_also_rotates(self):
        rpc = ScriptedRpc({'some_method': None})
        sleep_calls = []
        listener = PodpingListener(rpc=rpc, sleep=sleep_calls.append)

        result = listener._call_rpc('some_method', [])

        assert result is None
        assert listener.node_index == 1
        assert sleep_calls == [5]

    def test_backoff_escalates_across_consecutive_failures(self):
        rpc = ScriptedRpc({'some_method': requests.RequestException('boom')})
        sleep_calls = []
        listener = PodpingListener(rpc=rpc, sleep=sleep_calls.append)

        for _ in range(4):
            listener._call_rpc('some_method', [])

        assert sleep_calls == [5, 15, 60, 60]

    def test_success_resets_backoff(self):
        rpc = ScriptedRpc({
            'fails': requests.RequestException('boom'),
            'succeeds': {'ok': True},
        })
        sleep_calls = []
        listener = PodpingListener(rpc=rpc, sleep=sleep_calls.append)

        listener._call_rpc('fails', [])
        listener._call_rpc('succeeds', [])
        listener._call_rpc('fails', [])

        assert sleep_calls == [5, 5]


class TestTick:
    def test_head_fetch_failure_processes_nothing(self):
        rpc = ScriptedRpc({
            'condenser_api.get_dynamic_global_properties':
                requests.RequestException('down'),
        })
        fake_db = FakeDb()
        refresh_mock = Mock()
        listener = PodpingListener(rpc=rpc, db=fake_db, refresh=refresh_mock, sleep=lambda s: None)

        listener.tick()

        assert fake_db.stamped_slugs == []
        refresh_mock.assert_not_called()
        # No allow-list round trip any more: the head poll is the first call.
        assert [c[0] for c in rpc.calls] == [
            'condenser_api.get_dynamic_global_properties']

    def test_match_triggers_refresh_and_stamp(self, caplog):
        block = _podping_op(['delegate1'], {
            'version': '1.0',
            'iris': ['https://feeds.example.com/show'],
            'reason': 'update',
        })
        rpc = ScriptedRpc({
            'condenser_api.get_accounts': [{
                'name': 'podping',
                'posting': {'account_auths': [['delegate1', 1]]},
            }],
            'condenser_api.get_dynamic_global_properties': {'head_block_number': 5},
            'condenser_api.get_block': {'transactions': [block]},
        })
        fake_db = FakeDb(podcasts=[
            {'slug': 'my-show', 'source_url': 'https://feeds.example.com/show'},
        ])
        refresh_mock = Mock()
        listener = PodpingListener(rpc=rpc, db=fake_db, refresh=refresh_mock, sleep=lambda s: None)

        with caplog.at_level('INFO', logger='podcast.podping'):
            listener.tick()

        assert fake_db.stamped_slugs == ['my-show']
        refresh_mock.assert_called_once_with('my-show')
        assert '[my-show] Podping received (reason=update), refreshing feed' in caplog.text

    def test_tick_skips_a_feed_whose_hiveaccount_list_excludes_the_sender(self):
        block = _podping_op(['podping.ddd'], {
            'version': '1.0',
            'iris': ['https://feeds.example.com/show'],
            'reason': 'update',
        })
        rpc = ScriptedRpc({
            'condenser_api.get_dynamic_global_properties': {'head_block_number': 5},
            'condenser_api.get_block': {'transactions': [block]},
        })
        fake_db = FakeDb(
            podcasts=[{'slug': 'my-show', 'source_url': 'https://feeds.example.com/show'}],
            declarations={'my-show': {'uses_podping': True,
                                      'hive_accounts': ['podping.aaa']}})
        refresh_mock = Mock()
        listener = PodpingListener(rpc=rpc, db=fake_db, refresh=refresh_mock,
                                   sleep=lambda s: None)

        listener.tick()

        assert fake_db.stamped_slugs == []
        refresh_mock.assert_not_called()
        # The host domain is still recorded: coverage is about what the chain
        # shows, independent of per-feed authorization.
        assert fake_db.recorded_hosts == [{'feeds.example.com': 1}]

    def test_tick_honors_a_feed_that_opted_out(self):
        block = _podping_op(['podping.aaa'], {
            'version': '1.0',
            'iris': ['https://feeds.example.com/show'],
            'reason': 'update',
        })
        rpc = ScriptedRpc({
            'condenser_api.get_dynamic_global_properties': {'head_block_number': 5},
            'condenser_api.get_block': {'transactions': [block]},
        })
        fake_db = FakeDb(
            podcasts=[{'slug': 'my-show', 'source_url': 'https://feeds.example.com/show'}],
            declarations={'my-show': {'uses_podping': False, 'hive_accounts': []}})
        refresh_mock = Mock()
        listener = PodpingListener(rpc=rpc, db=fake_db, refresh=refresh_mock,
                                   sleep=lambda s: None)

        listener.tick()

        assert fake_db.stamped_slugs == []
        refresh_mock.assert_not_called()

    def test_tick_accepts_the_load_balanced_senders(self):
        # Regression: the old posting-authority allow-list rejected every one
        # of these, so the listener never fired in production.
        block = _podping_op(['podping.eee'], {
            'version': '1.0',
            'iris': ['https://feeds.example.com/show'],
            'reason': 'update',
        })
        rpc = ScriptedRpc({
            'condenser_api.get_dynamic_global_properties': {'head_block_number': 5},
            'condenser_api.get_block': {'transactions': [block]},
        })
        fake_db = FakeDb(podcasts=[
            {'slug': 'my-show', 'source_url': 'https://feeds.example.com/show'},
        ])
        refresh_mock = Mock()
        listener = PodpingListener(rpc=rpc, db=fake_db, refresh=refresh_mock,
                                   sleep=lambda s: None)

        listener.tick()

        assert fake_db.stamped_slugs == ['my-show']
        refresh_mock.assert_called_once_with('my-show')

    def test_tick_records_hosts_of_feeds_it_does_not_have(self):
        block = _podping_op(['delegate1'], {
            'version': '1.0',
            'iris': ['https://feeds.example.com/show',
                     'https://anchor.fm/somebody-else'],
            'reason': 'update',
        })
        rpc = ScriptedRpc({
            'condenser_api.get_accounts': [{
                'name': 'podping',
                'posting': {'account_auths': [['delegate1', 1]]},
            }],
            'condenser_api.get_dynamic_global_properties': {'head_block_number': 5},
            'condenser_api.get_block': {'transactions': [block]},
        })
        fake_db = FakeDb(podcasts=[
            {'slug': 'my-show', 'source_url': 'https://feeds.example.com/show'},
        ])
        listener = PodpingListener(rpc=rpc, db=fake_db, refresh=Mock(),
                                   sleep=lambda s: None)

        listener.tick()

        assert fake_db.recorded_hosts == [
            {'feeds.example.com': 1, 'anchor.fm': 1}]

    def test_tick_does_not_flush_again_inside_the_interval(self):
        block = _podping_op(['delegate1'], {
            'version': '1.0',
            'iris': ['https://anchor.fm/somebody-else'],
            'reason': 'update',
        })
        rpc = ScriptedRpc({
            'condenser_api.get_accounts': [{
                'name': 'podping',
                'posting': {'account_auths': [['delegate1', 1]]},
            }],
            'condenser_api.get_dynamic_global_properties': {'head_block_number': 5},
            'condenser_api.get_block': {'transactions': [block]},
        })
        fake_db = FakeDb()
        listener = PodpingListener(rpc=rpc, db=fake_db, refresh=Mock(),
                                   sleep=lambda s: None)

        listener.tick()
        listener.current_block = 4  # replay the same block
        listener.tick()

        assert len(fake_db.recorded_hosts) == 1
        assert listener.host_buffer == {'anchor.fm': 1}

    def test_reason_none_is_actionable(self):
        block = _podping_op(['delegate1'], {
            'version': '1.0',
            'iris': ['https://feeds.example.com/show'],
        })
        rpc = ScriptedRpc({
            'condenser_api.get_accounts': [{
                'name': 'podping',
                'posting': {'account_auths': [['delegate1', 1]]},
            }],
            'condenser_api.get_dynamic_global_properties': {'head_block_number': 5},
            'condenser_api.get_block': {'transactions': [block]},
        })
        fake_db = FakeDb(podcasts=[
            {'slug': 'my-show', 'source_url': 'https://feeds.example.com/show'},
        ])
        refresh_mock = Mock()
        listener = PodpingListener(rpc=rpc, db=fake_db, refresh=refresh_mock, sleep=lambda s: None)

        listener.tick()

        refresh_mock.assert_called_once_with('my-show')

    def test_non_actionable_reason_is_ignored(self):
        assert 'delete' not in ACTIONABLE_REASONS
        block = _podping_op(['delegate1'], {
            'version': '1.0',
            'iris': ['https://feeds.example.com/show'],
            'reason': 'delete',
        })
        rpc = ScriptedRpc({
            'condenser_api.get_accounts': [{
                'name': 'podping',
                'posting': {'account_auths': [['delegate1', 1]]},
            }],
            'condenser_api.get_dynamic_global_properties': {'head_block_number': 5},
            'condenser_api.get_block': {'transactions': [block]},
        })
        fake_db = FakeDb(podcasts=[
            {'slug': 'my-show', 'source_url': 'https://feeds.example.com/show'},
        ])
        refresh_mock = Mock()
        listener = PodpingListener(rpc=rpc, db=fake_db, refresh=refresh_mock, sleep=lambda s: None)

        listener.tick()

        refresh_mock.assert_not_called()
        assert fake_db.stamped_slugs == []

    def test_a_non_actionable_reason_is_still_counted_as_traffic(self):
        """The host table is a coverage measure, so a reason we do not act on
        must not make its sender invisible."""
        block = _podping_op(['delegate1'], {
            'version': '1.0',
            'iris': ['https://feeds.somehost.com/show'],
            'reason': 'delete',
        })
        rpc = ScriptedRpc({
            'condenser_api.get_dynamic_global_properties': {'head_block_number': 5},
            'condenser_api.get_block': {'transactions': [block]},
        })
        fake_db = FakeDb()
        listener = PodpingListener(rpc=rpc, db=fake_db, refresh=Mock(),
                                   sleep=lambda s: None)
        listener.host_flushed_at = 0

        listener.tick()

        recorded = {d for call in fake_db.recorded_hosts for d in call}
        assert recorded == {'feeds.somehost.com'}
        assert fake_db.stamped_slugs == [], 'still no refresh for that reason'

    def test_block_fetch_failure_does_not_advance_cursor(self):
        def get_block_side_effect(params):
            if params[0] == 3:
                raise requests.RequestException('boom')
            return {'transactions': []}

        rpc = ScriptedRpc({
            'condenser_api.get_dynamic_global_properties': {'head_block_number': 5},
            'condenser_api.get_block': get_block_side_effect,
        })
        fake_db = FakeDb()
        listener = PodpingListener(rpc=rpc, db=fake_db, refresh=Mock(), sleep=lambda s: None)
        listener.allowed_accounts = {'podping'}
        listener.allowed_accounts_fetched_at = time.time()
        listener.feed_map = {}
        listener.feed_map_fetched_at = time.time()
        listener.current_block = 2  # next block to fetch is 3, which fails

        listener.tick()

        # Cursor must not advance past the block that failed to fetch.
        assert listener.current_block == 2

        # Next tick retries the same block instead of skipping it.
        listener.tick()

        block_call_params = [c[1] for c in rpc.calls if c[0] == 'condenser_api.get_block']
        assert block_call_params == [[3], [3]]
        assert listener.current_block == 2

    def test_catchup_skip_jumps_to_head_minus_one(self, caplog):
        head = 100 + MAX_CATCHUP_BLOCKS + 50
        rpc = ScriptedRpc({
            'condenser_api.get_dynamic_global_properties': {'head_block_number': head},
            'condenser_api.get_block': {'transactions': []},
        })
        fake_db = FakeDb()
        listener = PodpingListener(rpc=rpc, db=fake_db, refresh=Mock(), sleep=lambda s: None)
        listener.allowed_accounts = {'podping'}
        listener.allowed_accounts_fetched_at = time.time()
        listener.feed_map = {}
        listener.feed_map_fetched_at = time.time()
        listener.current_block = 100

        with caplog.at_level('INFO', logger='podcast.podping'):
            listener.tick()

        assert listener.current_block == head
        get_block_calls = [c for c in rpc.calls if c[0] == 'condenser_api.get_block']
        assert len(get_block_calls) == 1
        assert 'blocks behind' in caplog.text


class TestCooldown:
    def test_second_ping_within_cooldown_neither_stamps_nor_refreshes(self):
        """A bursty sender drove one UPDATE plus commit per notification."""
        fake_db = FakeDb()
        refresh_mock = Mock()
        listener = PodpingListener(db=fake_db, refresh=refresh_mock, sleep=lambda s: None)

        listener._handle_match('my-show', 'update')
        listener._handle_match('my-show', 'update')

        assert fake_db.stamped_slugs == ['my-show']
        assert refresh_mock.call_count == 1

    def test_second_ping_within_cooldown_logs_debug_skip(self, caplog):
        fake_db = FakeDb()
        listener = PodpingListener(db=fake_db, refresh=Mock(), sleep=lambda s: None)

        with caplog.at_level('DEBUG', logger='podcast.podping'):
            listener._handle_match('my-show', 'update')
            listener._handle_match('my-show', 'update')

        debug_records = [r for r in caplog.records if r.levelname == 'DEBUG']
        assert len(debug_records) == 1
        message = debug_records[0].getMessage()
        assert 'my-show' in message
        assert 'cooldown' in message.lower()


class _FakeShutdownEvent:
    """Stand-in for the real, process-wide shutdown_event -- see
    test_refresh_interval_setting.py's identically-shaped fake for why this
    is needed rather than monkeypatching the real singleton's methods.

    Only the thread that built it is observed. Background threads started at
    bootstrap read the same patched attribute, and their waits used to land in
    wait_calls, which made the assertions here depend on test order.
    """

    def __init__(self):
        self._flag = False
        self.wait_calls = []
        self._owner = threading.current_thread()

    def _is_owner(self):
        return threading.current_thread() is self._owner

    def is_set(self):
        return self._flag

    def set(self):
        self._flag = True

    def wait(self, timeout=None):
        if not self._is_owner():
            return True
        self.wait_calls.append(timeout)
        self._flag = True
        return True


class TestPodpingListenerLoop:
    def test_disabled_setting_waits_without_rpc(self, monkeypatch):
        import main_app.background as background_module

        fake_event = _FakeShutdownEvent()
        monkeypatch.setattr(background_module, 'shutdown_event', fake_event)

        podping_listener_loop()

        assert fake_event.wait_calls == [30]

    def test_enabled_loop_paces_ticks_with_3s_wait(self, monkeypatch):
        import main_app.background as background_module
        from unittest.mock import Mock

        class CountingShutdownEvent(_FakeShutdownEvent):
            """Countdown event that allows N iterations before stopping."""

            def __init__(self, max_iterations):
                super().__init__()
                self.iteration_count = 0
                self.max_iterations = max_iterations

            def is_set(self):
                return self.iteration_count >= self.max_iterations

            def wait(self, timeout=None):
                if not self._is_owner():
                    return True
                self.wait_calls.append(timeout)
                self.iteration_count += 1
                return True

        fake_event = CountingShutdownEvent(max_iterations=3)
        monkeypatch.setattr(background_module, 'shutdown_event', fake_event)

        # Mock db to always return enabled=True and provide stub methods.
        fake_db = Mock()
        fake_db.get_setting_bool.side_effect = lambda k, default=False: True if k == 'podping_enabled' else default
        fake_db.clear_leaked_transaction.return_value = None
        monkeypatch.setattr(background_module, 'db', fake_db)

        # Mock PodpingListener.tick to succeed without network calls.
        monkeypatch.setattr(PodpingListener, 'tick', Mock())

        # Mock refresh function to prevent any side effects.
        refresh_mock = Mock()
        monkeypatch.setattr('main_app.feeds.refresh_single_feed', refresh_mock)

        podping_listener_loop()

        # After each of 3 successful ticks, wait(timeout=3) should be called.
        assert fake_event.wait_calls == [3, 3, 3]

    def test_tick_exception_is_caught_and_loop_continues(self, monkeypatch, caplog):
        """A tick() that raises must not kill the loop: log via
        logger.exception, back off 60s, and proceed to the next iteration."""
        import main_app.background as background_module
        from unittest.mock import Mock

        class CountingShutdownEvent(_FakeShutdownEvent):
            def __init__(self, max_iterations):
                super().__init__()
                self.iteration_count = 0
                self.max_iterations = max_iterations

            def is_set(self):
                return self.iteration_count >= self.max_iterations

            def wait(self, timeout=None):
                if not self._is_owner():
                    return True
                self.wait_calls.append(timeout)
                self.iteration_count += 1
                return True

        fake_event = CountingShutdownEvent(max_iterations=2)
        monkeypatch.setattr(background_module, 'shutdown_event', fake_event)

        fake_db = Mock()
        fake_db.get_setting_bool.side_effect = (
            lambda k, default=False: True if k == 'podping_enabled' else default)
        fake_db.clear_leaked_transaction.return_value = None
        monkeypatch.setattr(background_module, 'db', fake_db)

        tick_mock = Mock(side_effect=RuntimeError('boom'))
        monkeypatch.setattr(PodpingListener, 'tick', tick_mock)
        monkeypatch.setattr('main_app.feeds.refresh_single_feed', Mock())

        with caplog.at_level('ERROR', logger='podcast.podping'):
            podping_listener_loop()  # must return normally, not raise

        # Both iterations hit the exception path and backed off 60s each --
        # the loop reached a second iteration rather than dying on the first.
        assert fake_event.wait_calls == [60, 60]
        assert tick_mock.call_count == 2
        assert 'Podping listener loop iteration failed' in caplog.text
        exc_records = [r for r in caplog.records if r.exc_info is not None]
        assert len(exc_records) == 2


class TestRestartResume:
    """A podping is never resent, so a restart must not jump to the chain head
    and skip whatever was sent while the container was down."""

    def _listener(self, head, stored=None):
        rpc = ScriptedRpc({
            'condenser_api.get_dynamic_global_properties': {'head_block_number': head},
            'condenser_api.get_block': {'transactions': []},
        })
        settings = {'podping_last_block': stored} if stored else {}
        fake_db = FakeDb(settings=settings)
        listener = PodpingListener(rpc=rpc, db=fake_db, refresh=Mock(),
                                   sleep=lambda s: None)
        listener.feed_map = {}
        listener.feed_map_fetched_at = time.time()
        return listener, fake_db, rpc

    def test_resumes_from_the_last_processed_block(self):
        head = 5000
        listener, _, rpc = self._listener(head, stored='4990')
        listener.tick()

        fetched = sorted(c[1][0] for c in rpc.calls
                         if c[0] == 'condenser_api.get_block')
        assert fetched[0] == 4991, 'must replay the blocks missed while down'
        assert listener.current_block == head

    def test_a_gap_wider_than_the_cap_still_skips(self):
        head = 5000
        listener, _, rpc = self._listener(head, stored=str(head - MAX_CATCHUP_BLOCKS - 500))
        listener.tick()

        calls = [c for c in rpc.calls if c[0] == 'condenser_api.get_block']
        assert len(calls) == 1, 'an unbounded catch-up would hammer the nodes'

    def test_first_ever_start_begins_at_the_head(self):
        listener, _, rpc = self._listener(5000)
        listener.tick()

        calls = [c for c in rpc.calls if c[0] == 'condenser_api.get_block']
        assert len(calls) == 1

    def test_progress_is_persisted_for_the_next_start(self):
        listener, fake_db, _ = self._listener(5000, stored='4998')
        listener.host_flushed_at = 0  # force the flush cadence
        listener.tick()

        assert fake_db.settings.get('podping_last_block') == '5000'

    def test_a_corrupt_stored_block_falls_back_to_the_head(self):
        listener, _, rpc = self._listener(5000, stored='not-a-number')
        listener.tick()

        calls = [c for c in rpc.calls if c[0] == 'condenser_api.get_block']
        assert len(calls) == 1


class TestListenerWritePatterns:
    """The listener runs a tick every 3 seconds, so anything unconditional in
    a tick is a write every 3 seconds."""

    def _listener(self, fake_db, head=5, block=None):
        rpc = ScriptedRpc({
            'condenser_api.get_dynamic_global_properties': {'head_block_number': head},
            'condenser_api.get_block': {'transactions': [block] if block else []},
        })
        listener = PodpingListener(rpc=rpc, db=fake_db, refresh=Mock(),
                                   sleep=lambda s: None)
        listener.feed_map = {}
        listener.feed_map_fetched_at = time.time()
        return listener, rpc

    def test_an_empty_buffer_tick_does_not_rewrite_the_block_setting(self):
        fake_db = FakeDb()
        listener, _ = self._listener(fake_db)
        listener.host_flushed_at = 0

        listener.tick()
        writes_after_first = len(fake_db.setting_writes)
        listener.tick()

        assert len(fake_db.setting_writes) == writes_after_first

    def test_a_failed_flush_still_advances_the_stored_block(self):
        """Block progress is delivery correctness; a podping is never resent,
        while a host count the buffer still holds is re-flushed next tick."""
        block = _podping_op(['delegate1'], {
            'version': '1.0', 'iris': ['https://anchor.fm/x'], 'reason': 'update'})
        fake_db = FakeDb()
        fake_db.record_fails = True
        listener, _ = self._listener(fake_db, block=block)
        listener.host_flushed_at = 0

        listener.tick()

        assert fake_db.settings.get('podping_last_block') == '5'
        assert listener.host_buffer == {'anchor.fm': 1}

    def test_the_last_ping_stamp_is_throttled_per_slug(self):
        block = _podping_op(['delegate1'], {
            'version': '1.0', 'iris': ['https://feeds.example.com/show'],
            'reason': 'update'})
        fake_db = FakeDb(podcasts=[
            {'slug': 'my-show', 'source_url': 'https://feeds.example.com/show'}])
        listener, _ = self._listener(fake_db, block=block)
        listener._refresh_feed_map()

        listener.tick()
        listener.current_block = 4  # replay the same block
        listener.tick()

        assert fake_db.stamped_slugs == ['my-show']

    def test_the_feed_map_reads_only_slug_and_url(self):
        fake_db = FakeDb(podcasts=[
            {'slug': 'my-show', 'source_url': 'https://feeds.example.com/show'}])
        listener, _ = self._listener(fake_db)

        listener._refresh_feed_map()

        assert fake_db.heavy_podcast_reads == 0

    def test_a_caught_up_restart_does_not_replay_the_head_block(self):
        fake_db = FakeDb(settings={'podping_last_block': '5000'})
        listener, rpc = self._listener(fake_db, head=5000)

        listener.tick()

        assert [c for c in rpc.calls if c[0] == 'condenser_api.get_block'] == []

    def test_shutdown_flushes_and_persists(self):
        block = _podping_op(['delegate1'], {
            'version': '1.0', 'iris': ['https://anchor.fm/x'], 'reason': 'update'})
        fake_db = FakeDb()
        listener, _ = self._listener(fake_db, block=block)
        listener.host_flushed_at = time.time()

        listener.tick()          # inside the flush interval: nothing written yet
        assert fake_db.recorded_hosts == []
        listener.final_flush()

        assert fake_db.recorded_hosts == [{'anchor.fm': 1}]
        assert fake_db.settings.get('podping_last_block') == '5'


class TestActiveAuthorityIsAccepted:
    def test_an_op_signed_with_active_authority_carries_its_auths(self):
        events = extract_podping_events({'transactions': [{
            'operations': [['custom_json', {
                'id': 'podping',
                'required_auths': ['podping.aaa'],
                'required_posting_auths': [],
                'json': json.dumps({'version': '1.0',
                                    'iris': ['https://feeds.example.com/show'],
                                    'reason': 'update'}),
            }]]}]})

        assert events[0]['auths'] == {'podping.aaa'}


class TestNodeFailureLogging:
    """One node failing while failover works is not actionable, and at the 60s
    backoff cap it would print once a minute forever. Only losing every node
    misses pings."""

    def _listener(self):
        return PodpingListener(rpc=lambda *a, **k: None, sleep=lambda s: None)

    def test_first_failure_for_a_node_warns(self, caplog):
        listener = self._listener()
        with caplog.at_level(logging.DEBUG, logger='podcast.podping'):
            listener._node_failure('connection refused')
        assert [r.levelname for r in caplog.records] == ['WARNING']

    def test_repeat_failure_for_same_node_is_debug(self, caplog):
        listener = self._listener()
        listener._node_failure('connection refused')
        # Walk back to the same node so the repeat is for an already-failed one.
        listener.node_index = 0
        # caplog.records spans the whole test, not just the with block.
        caplog.clear()
        with caplog.at_level(logging.DEBUG, logger='podcast.podping'):
            listener._node_failure('connection refused')
        assert [r.levelname for r in caplog.records] == ['DEBUG']

    def test_losing_every_node_logs_error(self, caplog):
        listener = self._listener()
        with caplog.at_level(logging.DEBUG, logger='podcast.podping'):
            for _ in range(len(PODPING_NODES)):
                listener._node_failure('connection refused')
        assert [r.levelname for r in caplog.records][-1] == 'ERROR'
        assert len([r for r in caplog.records if r.levelname == 'ERROR']) == 1

    def test_success_clears_the_failed_set(self, caplog):
        """After a success a node that fails again is news, so it warns."""
        listener = PodpingListener(rpc=lambda *a, **k: {}, sleep=lambda s: None)
        listener._node_failure('connection refused')
        listener.node_index = 0
        listener._call_rpc('some_method', {}, expected_type=dict)
        assert listener._failed_nodes == set()
        caplog.clear()
        with caplog.at_level(logging.DEBUG, logger='podcast.podping'):
            listener._node_failure('connection refused')
        assert [r.levelname for r in caplog.records] == ['WARNING']
