"""Tests for durable per-node Podping health and the all-nodes-down signal."""
import json

import requests

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap(
    'podping_node_health_test_', passphrase='podping-node-health-test-passphrase')

from podping_listener import (
    PodpingListener,
    PODPING_NODES,
    DEGRADED_SETTING,
    NODE_HEALTH_SETTING,
    get_node_health_summary,
)


class FakeDb:
    def __init__(self, settings=None):
        self.settings = dict(settings or {})
        self.setting_writes = []

    def get_setting(self, key):
        return self.settings.get(key)

    def set_setting(self, key, value, is_default=False):
        self.settings[key] = value
        self.setting_writes.append((key, value))


def _health(fake_db, node):
    raw = fake_db.settings.get(NODE_HEALTH_SETTING)
    return json.loads(raw)[node] if raw else None


class TestOneNodeFailure:
    def test_failing_node_gets_a_consecutive_failure_and_next_retry(self):
        db = FakeDb()
        listener = PodpingListener(
            rpc=lambda *a, **k: (_ for _ in ()).throw(
                requests.RequestException('boom')),
            db=db, sleep=lambda s: None, rand=lambda lo, hi: 0)

        listener._call_rpc('some_method', [])

        entry = _health(db, PODPING_NODES[0])
        assert entry['consecutive_failures'] == 1
        assert entry['next_retry_at'] is not None
        assert entry['last_failure_reason']

    def test_one_node_down_does_not_set_degraded(self):
        db = FakeDb()
        listener = PodpingListener(
            rpc=lambda *a, **k: (_ for _ in ()).throw(
                requests.RequestException('boom')),
            db=db, sleep=lambda s: None, rand=lambda lo, hi: 0)

        listener._call_rpc('some_method', [])

        assert db.settings.get(DEGRADED_SETTING) != '1'


class TestTwoNodeFailure:
    def test_two_of_three_nodes_down_does_not_set_degraded(self):
        db = FakeDb()
        listener = PodpingListener(
            rpc=lambda *a, **k: (_ for _ in ()).throw(
                requests.RequestException('boom')),
            db=db, sleep=lambda s: None, rand=lambda lo, hi: 0)

        assert len(PODPING_NODES) == 3, 'test assumes the default 3-node list'
        listener._call_rpc('some_method', [])
        listener._call_rpc('some_method', [])

        assert db.settings.get(DEGRADED_SETTING) != '1'
        # Each failed node has its own recorded streak.
        assert _health(db, PODPING_NODES[0])['consecutive_failures'] == 1
        assert _health(db, PODPING_NODES[1])['consecutive_failures'] == 1


class TestAllNodesFailure:
    def test_all_nodes_down_sets_degraded_true(self):
        db = FakeDb()
        listener = PodpingListener(
            rpc=lambda *a, **k: (_ for _ in ()).throw(
                requests.RequestException('boom')),
            db=db, sleep=lambda s: None, rand=lambda lo, hi: 0)

        for _ in range(len(PODPING_NODES)):
            listener._call_rpc('some_method', [])

        assert db.settings.get(DEGRADED_SETTING) == '1'
        assert db.settings.get('podping_degraded_since')

    def test_degraded_indicator_only_true_in_all_node_case(self):
        db = FakeDb()
        listener = PodpingListener(
            rpc=lambda *a, **k: (_ for _ in ()).throw(
                requests.RequestException('boom')),
            db=db, sleep=lambda s: None, rand=lambda lo, hi: 0)

        for i in range(len(PODPING_NODES) - 1):
            listener._call_rpc('some_method', [])
            assert db.settings.get(DEGRADED_SETTING) != '1', \
                f'must not be degraded after {i + 1} of {len(PODPING_NODES)} nodes fail'

        listener._call_rpc('some_method', [])  # the last node fails
        assert db.settings.get(DEGRADED_SETTING) == '1'


class TestStreakResetsOnSuccess:
    def test_a_node_success_resets_its_own_streak(self):
        db = FakeDb()
        responses = {'fails': requests.RequestException('boom'), 'succeeds': {'ok': True}}

        def rpc(method, params):
            value = responses[method]
            if isinstance(value, Exception):
                raise value
            return value

        listener = PodpingListener(rpc=rpc, db=db, sleep=lambda s: None,
                                   rand=lambda lo, hi: 0)

        listener._call_rpc('fails', [])
        listener.node_index = 0  # walk back to the node that just failed
        listener._call_rpc('fails', [])
        assert _health(db, PODPING_NODES[0])['consecutive_failures'] == 2

        listener.node_index = 0
        listener._call_rpc('succeeds', [])
        assert _health(db, PODPING_NODES[0])['consecutive_failures'] == 0

    def test_recovery_clears_degraded_flag(self):
        db = FakeDb()
        state = {'up': False}

        def rpc(method, params):
            if not state['up']:
                raise requests.RequestException('boom')
            return {'ok': True}

        listener = PodpingListener(rpc=rpc, db=db, sleep=lambda s: None,
                                   rand=lambda lo, hi: 0)
        for _ in range(len(PODPING_NODES)):
            listener._call_rpc('some_method', [])
        assert db.settings.get(DEGRADED_SETTING) == '1'

        state['up'] = True
        listener._call_rpc('some_method', [])

        assert db.settings.get(DEGRADED_SETTING) == '0'


class TestBackoffGrowsWithJitter:
    def test_backoff_grows_monotonically_within_jitter_bounds(self):
        """Each step's max (base*1.2) must stay below the next step's min
        (base*0.8) so growth holds even at the extremes of the +/-20% jitter."""
        sleep_calls = []
        listener = PodpingListener(
            rpc=lambda *a, **k: (_ for _ in ()).throw(
                requests.RequestException('boom')),
            sleep=sleep_calls.append)  # real random.uniform jitter

        for _ in range(4):
            listener._call_rpc('some_method', [])

        assert len(sleep_calls) == 4
        for value in sleep_calls:
            assert value >= 0
        for prev, nxt in zip(sleep_calls, sleep_calls[1:], strict=False):
            assert nxt > prev, f'{sleep_calls} did not grow monotonically'

    def test_jitter_varies_the_exact_value(self):
        """With real jitter, repeated single-step backoffs are not all
        identical -- distinguishing this from the old fixed schedule."""
        values = set()
        for _ in range(20):
            listener = PodpingListener(
                rpc=lambda *a, **k: (_ for _ in ()).throw(
                    requests.RequestException('boom')),
                sleep=lambda s: values.add(round(s, 6)))
            listener._call_rpc('some_method', [])
        assert len(values) > 1, 'jitter should not produce a single fixed value'


class TestDurableStateSurvivesRestart:
    def test_reloading_from_the_same_settings_resumes_the_streak(self):
        db = FakeDb()
        listener1 = PodpingListener(
            rpc=lambda *a, **k: (_ for _ in ()).throw(
                requests.RequestException('boom')),
            db=db, sleep=lambda s: None, rand=lambda lo, hi: 0)
        listener1._call_rpc('some_method', [])
        listener1.node_index = 0  # walk back to the node that just failed
        listener1._call_rpc('some_method', [])

        # Simulate a restart: a fresh listener loads state from the same db.
        listener2 = PodpingListener(db=db, sleep=lambda s: None)

        entry = listener2._node_health[PODPING_NODES[0]]
        assert entry['consecutive_failures'] == 2
        assert entry['next_retry_at'] is not None

    def test_reloading_ignores_corrupt_persisted_json(self):
        db = FakeDb(settings={NODE_HEALTH_SETTING: 'not-json'})
        listener = PodpingListener(db=db, sleep=lambda s: None)
        assert listener._node_health == {}


class TestNodeHealthSummary:
    def test_summary_lists_every_configured_node(self):
        db = FakeDb()
        summary = get_node_health_summary(db)
        assert [row['node'] for row in summary] == PODPING_NODES
        assert all(row['consecutiveFailures'] == 0 for row in summary)

    def test_summary_reflects_a_failing_node(self):
        db = FakeDb()
        listener = PodpingListener(
            rpc=lambda *a, **k: (_ for _ in ()).throw(
                requests.RequestException('boom')),
            db=db, sleep=lambda s: None, rand=lambda lo, hi: 0)
        listener._call_rpc('some_method', [])

        summary = get_node_health_summary(db)
        failing = next(row for row in summary if row['node'] == PODPING_NODES[0])
        assert failing['consecutiveFailures'] == 1
        assert failing['lastFailureReason']

    def test_failure_reason_has_no_query_string(self):
        db = FakeDb()
        listener = PodpingListener(
            rpc=lambda *a, **k: (_ for _ in ()).throw(
                requests.RequestException(
                    'HTTP 401 from https://api.hive.blog/?token=SECRET123')),
            db=db, sleep=lambda s: None, rand=lambda lo, hi: 0)
        listener._call_rpc('some_method', [])

        summary = get_node_health_summary(db)
        reason = summary[0]['lastFailureReason']
        assert 'SECRET123' not in reason
        assert '<redacted>' in reason
