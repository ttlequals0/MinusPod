"""Tests for PodpingListener and podping_listener_loop (Task 5)."""
import json
import time
from unittest.mock import Mock

import requests

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('podping_listener_test_', passphrase='podping-listener-test-passphrase')

from podping_listener import (
    PodpingListener,
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
    def __init__(self, podcasts=None):
        self.podcasts = podcasts or []
        self.stamped_slugs = []

    def get_all_podcasts(self):
        return self.podcasts

    def set_last_podping_at(self, slug):
        self.stamped_slugs.append(slug)


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


class TestRefreshAllowedAccounts:
    def test_parses_posting_auths_and_includes_podping(self):
        rpc = ScriptedRpc({
            'condenser_api.get_accounts': [{
                'name': 'podping',
                'posting': {'account_auths': [['delegate1', 1], ['delegate2', 1]]},
            }],
        })
        listener = PodpingListener(rpc=rpc, sleep=lambda s: None)

        listener.refresh_allowed_accounts()

        assert listener.allowed_accounts == {'podping', 'delegate1', 'delegate2'}
        assert listener.allowed_accounts_fetched_at > 0

    def test_fetch_failure_keeps_old_list(self):
        rpc = ScriptedRpc({
            'condenser_api.get_accounts': requests.RequestException('down'),
        })
        listener = PodpingListener(rpc=rpc, sleep=lambda s: None)
        listener.allowed_accounts = {'podping', 'delegate1'}
        listener.allowed_accounts_fetched_at = 123.0

        listener.refresh_allowed_accounts()

        assert listener.allowed_accounts == {'podping', 'delegate1'}


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
    def test_allowlist_never_fetched_processes_nothing(self):
        rpc = ScriptedRpc({
            'condenser_api.get_accounts': requests.RequestException('down'),
        })
        fake_db = FakeDb()
        refresh_mock = Mock()
        listener = PodpingListener(rpc=rpc, db=fake_db, refresh=refresh_mock, sleep=lambda s: None)

        listener.tick()

        assert listener.allowed_accounts == set()
        assert fake_db.stamped_slugs == []
        refresh_mock.assert_not_called()
        # Never got past the allow-list fetch -- no head/block calls made.
        assert [c[0] for c in rpc.calls] == ['condenser_api.get_accounts']

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
    def test_second_ping_within_cooldown_stamps_but_no_refresh(self):
        fake_db = FakeDb()
        refresh_mock = Mock()
        listener = PodpingListener(db=fake_db, refresh=refresh_mock, sleep=lambda s: None)

        listener._handle_match('my-show', 'update')
        listener._handle_match('my-show', 'update')

        assert fake_db.stamped_slugs == ['my-show', 'my-show']
        assert refresh_mock.call_count == 1


class _FakeShutdownEvent:
    """Stand-in for the real, process-wide shutdown_event -- see
    test_refresh_interval_setting.py's identically-shaped fake for why this
    is needed rather than monkeypatching the real singleton's methods.
    """

    def __init__(self):
        self._flag = False
        self.wait_calls = []

    def is_set(self):
        return self._flag

    def set(self):
        self._flag = True

    def wait(self, timeout=None):
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
