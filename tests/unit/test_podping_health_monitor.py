"""Leader-owned Podping node health checks."""
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('podping_health_monitor_test_')

from podping_listener import (
    ACTIVE_CONNECTION_SETTING,
    DEGRADED_SETTING,
    MONITOR_HEARTBEAT_SETTING,
    NODE_CHECK_SETTING,
    NODE_HEALTH_SETTING,
    PODPING_NODES,
    SELECTED_NODE_SETTING,
    PodpingListener,
)


class FakeDb:
    def __init__(self, settings=None):
        self.settings = dict(settings or {})
        self.fail_health_write = False

    def get_setting(self, key):
        return self.settings.get(key)

    def set_setting(self, key, value, is_default=False):
        if key == NODE_HEALTH_SETTING and self.fail_health_write:
            raise RuntimeError('db down')
        self.settings[key] = value

    def replace_setting_if_equal(self, key, expected, value):
        if self.settings.get(key) == expected:
            self.settings[key] = value
        return self.settings.get(key, '')

    def clear_setting_if_equal(self, key, expected):
        if self.settings.get(key) == expected:
            del self.settings[key]
            return True
        return False


def _wait_for_futures(listener):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if listener._probe_futures and all(
                future.done() for future in listener._probe_futures.values()):
            return
        time.sleep(0.005)
    raise AssertionError('Podping probe futures did not finish')


def test_automatic_checks_require_enabled_listener_and_do_not_overlap():
    release = threading.Event()
    calls = []

    def probe(node):
        calls.append(node)
        release.wait(timeout=2)
        return 200, 'healthy', None

    db = FakeDb()
    listener = PodpingListener(db=db, node_probe=probe)
    try:
        listener.update_node_probes(False)
        assert calls == []

        listener.update_node_probes(True)
        deadline = time.monotonic() + 2
        while len(calls) < len(PODPING_NODES) and time.monotonic() < deadline:
            time.sleep(0.005)
        assert set(calls) == set(PODPING_NODES)

        listener.update_node_probes(True)
        assert len(calls) == len(PODPING_NODES)

        release.set()
        _wait_for_futures(listener)
        listener.update_node_probes(True)
        health = json.loads(db.settings[NODE_HEALTH_SETTING])
        assert all(health[node]['last_outcome'] == 'healthy' for node in PODPING_NODES)
    finally:
        release.set()
        listener.close()


def test_manual_check_runs_while_disabled_and_preserves_failover_state():
    requested = {
        'checkId': 'check-1',
        'status': 'pending',
        'requestedAt': '2026-09-20T12:00:00Z',
        'startedAt': None,
        'completedAt': None,
    }
    db = FakeDb({
        NODE_CHECK_SETTING: json.dumps(requested),
        SELECTED_NODE_SETTING: PODPING_NODES[2],
        DEGRADED_SETTING: '1',
    })
    listener = PodpingListener(
        db=db, node_probe=lambda node: (200, 'healthy', None))
    listener.node_index = 2
    listener._last_rpc_status_code = 429
    listener._failed_nodes = {PODPING_NODES[3]}
    listener._backoff_step = 4
    try:
        listener.update_node_probes(False)
        running = json.loads(db.settings[NODE_CHECK_SETTING])
        assert running['status'] == 'running'
        assert running['startedAt'] is not None

        _wait_for_futures(listener)
        listener.update_node_probes(False)

        completed = json.loads(db.settings[NODE_CHECK_SETTING])
        assert completed['status'] == 'completed'
        assert completed['completedAt'] is not None
        assert listener.node_index == 2
        assert listener._last_rpc_status_code == 429
        assert listener._failed_nodes == {PODPING_NODES[3]}
        assert listener._backoff_step == 4
        assert db.settings[SELECTED_NODE_SETTING] == PODPING_NODES[2]
        assert db.settings[DEGRADED_SETTING] == '1'
    finally:
        listener.close()


def test_newer_live_result_wins_over_older_probe_result():
    release = threading.Event()

    def probe(node):
        release.wait(timeout=2)
        return None, 'unreachable', 'probe failed'

    db = FakeDb()
    listener = PodpingListener(db=db, node_probe=probe)
    try:
        listener.update_node_probes(True)
        listener._last_rpc_status_code = 200
        listener._record_node_success(PODPING_NODES[0])
        release.set()
        _wait_for_futures(listener)
        listener.update_node_probes(True)

        health = json.loads(db.settings[NODE_HEALTH_SETTING])
        assert health[PODPING_NODES[0]]['last_outcome'] == 'healthy'
        assert listener.node_index == 0
        assert json.loads(db.settings[ACTIVE_CONNECTION_SETTING])['node'] == PODPING_NODES[0]
    finally:
        release.set()
        listener.close()


def test_manual_count_includes_probe_result_guarded_by_newer_live_state():
    release = threading.Event()
    requested = {
        'checkId': 'check-count',
        'status': 'pending',
        'requestedAt': '2026-09-20T12:00:00Z',
    }
    db = FakeDb({NODE_CHECK_SETTING: json.dumps(requested)})

    def probe(node):
        release.wait(timeout=2)
        return (200, 'healthy', None) if node == PODPING_NODES[0] else (
            None, 'unreachable', 'offline')

    listener = PodpingListener(db=db, node_probe=probe)
    try:
        listener.update_node_probes(False)
        listener._last_rpc_status_code = 200
        listener._record_node_success(PODPING_NODES[0])
        release.set()
        _wait_for_futures(listener)
        listener.update_node_probes(False)

        check = json.loads(db.settings[NODE_CHECK_SETTING])
        health = json.loads(db.settings[NODE_HEALTH_SETTING])
        assert check['healthyNodes'] == 1
        assert check['totalNodes'] == len(PODPING_NODES)
        assert health[PODPING_NODES[0]]['last_outcome'] == 'healthy'
    finally:
        release.set()
        listener.close()


def test_expired_manual_claim_is_recovered_with_new_owner():
    expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    db = FakeDb({NODE_CHECK_SETTING: json.dumps({
        'checkId': 'check-2',
        'status': 'running',
        'requestedAt': '2026-09-20T12:00:00Z',
        'startedAt': '2026-09-20T12:00:01Z',
        'completedAt': None,
        'claimId': 'dead-leader',
        'leaseUntil': expired,
    })})
    listener = PodpingListener(
        db=db, node_probe=lambda node: (200, 'healthy', None))
    try:
        listener.update_node_probes(False)
        claimed = json.loads(db.settings[NODE_CHECK_SETTING])
        assert claimed['status'] == 'running'
        assert claimed['claimId'] != 'dead-leader'
    finally:
        listener.close()


def test_manual_check_reports_internal_persistence_failure():
    db = FakeDb({NODE_CHECK_SETTING: json.dumps({
        'checkId': 'check-4',
        'status': 'pending',
        'requestedAt': '2026-09-20T12:00:00Z',
        'startedAt': None,
        'completedAt': None,
    })})
    db.fail_health_write = True
    listener = PodpingListener(
        db=db, node_probe=lambda node: (200, 'healthy', None))
    try:
        listener.update_node_probes(False)
        _wait_for_futures(listener)
        listener.update_node_probes(False)

        check = json.loads(db.settings[NODE_CHECK_SETTING])
        assert check['status'] == 'error'
        assert check['message'] == 'Node health results could not be saved.'
    finally:
        listener.close()


def test_completed_probe_does_not_overwrite_a_newer_request():
    db = FakeDb({NODE_CHECK_SETTING: json.dumps({
        'checkId': 'old-check',
        'status': 'pending',
        'requestedAt': '2026-09-20T12:00:00Z',
    })})
    listener = PodpingListener(
        db=db, node_probe=lambda node: (200, 'healthy', None))
    try:
        listener.update_node_probes(False)
        _wait_for_futures(listener)
        db.set_setting(NODE_CHECK_SETTING, json.dumps({
            'checkId': 'new-check',
            'status': 'pending',
            'requestedAt': '2026-09-20T12:01:00Z',
        }))

        listener.update_node_probes(False)

        check = json.loads(db.settings[NODE_CHECK_SETTING])
        assert check['checkId'] == 'new-check'
        assert check['status'] == 'running'
    finally:
        listener.close()


def test_owned_manual_lease_is_renewed_while_probes_run():
    now = [datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)]
    release = threading.Event()
    db = FakeDb({NODE_CHECK_SETTING: json.dumps({
        'checkId': 'check-5',
        'status': 'pending',
        'requestedAt': now[0].isoformat(),
    })})
    listener = PodpingListener(
        db=db,
        now=lambda: now[0],
        node_probe=lambda node: (release.wait(timeout=2), (200, 'healthy', None))[1],
    )
    try:
        listener.update_node_probes(False)
        first = json.loads(db.settings[NODE_CHECK_SETTING])
        now[0] += timedelta(seconds=40)

        listener.update_node_probes(False)

        renewed = json.loads(db.settings[NODE_CHECK_SETTING])
        assert renewed['claimId'] == first['claimId']
        assert renewed['leaseUntil'] > first['leaseUntil']
    finally:
        release.set()
        listener.close()


def test_old_listener_shutdown_does_not_clear_successor_state():
    db = FakeDb()
    old = PodpingListener(db=db)
    new = PodpingListener(db=db)
    try:
        old.persist_monitor_heartbeat()
        old._persist_active_connection(PODPING_NODES[0])
        new.persist_monitor_heartbeat()
        new._persist_active_connection(PODPING_NODES[1])

        old.close()

        heartbeat = json.loads(db.settings['podping_monitor_heartbeat'])
        active = json.loads(db.settings[ACTIVE_CONNECTION_SETTING])
        assert heartbeat['owner'] == new._owner_id
        assert active['owner'] == new._owner_id
        assert active['node'] == PODPING_NODES[1]
    finally:
        new.close()


def test_backoff_services_heartbeat_and_pending_manual_check(monkeypatch):
    import main_app.background as background_module

    release = threading.Event()
    db = FakeDb({NODE_CHECK_SETTING: json.dumps({
        'checkId': 'check-6',
        'status': 'pending',
        'requestedAt': '2026-09-20T12:00:00Z',
    })})
    listener = PodpingListener(
        db=db,
        node_probe=lambda node: (release.wait(timeout=2), (200, 'healthy', None))[1],
    )
    listener.sleep = lambda seconds: None
    monkeypatch.setattr(background_module.shutdown_event, 'is_set', lambda: False)
    try:
        listener.wait_with_monitor(3)

        assert MONITOR_HEARTBEAT_SETTING in db.settings
        assert json.loads(db.settings[NODE_CHECK_SETTING])['status'] == 'running'
        assert listener._probe_futures
    finally:
        release.set()
        listener.close()


def test_catchup_services_monitor_between_blocks():
    db = FakeDb()

    def rpc(method, params):
        if method == 'condenser_api.get_dynamic_global_properties':
            return {'head_block_number': 3}
        return {'transactions': []}

    listener = PodpingListener(db=db, rpc=rpc, sleep=lambda seconds: None)
    listener.current_block = 0
    listener._maybe_refresh_feed_map = lambda: None
    listener.host_flushed_at = time.time()
    heartbeat_calls = []
    probe_calls = []
    listener.persist_monitor_heartbeat = lambda: heartbeat_calls.append(True)
    listener.update_node_probes = lambda enabled: probe_calls.append(enabled)
    try:
        listener.tick()

        assert len(heartbeat_calls) == 3
        assert len(probe_calls) == 3
    finally:
        listener.close()


def test_probe_validates_head_and_block_rpc_shapes():
    head = MagicMock(status_code=200)
    head.json.return_value = {'result': {'head_block_number': 101}}
    block = MagicMock(status_code=200)
    block.json.return_value = {'result': {'transactions': []}}
    listener = PodpingListener(db=FakeDb())
    try:
        with patch('podping_listener.requests.post', side_effect=[head, block]) as post:
            assert listener._default_node_probe(PODPING_NODES[0]) == (
                200, 'healthy', None)
        assert post.call_args_list[1].kwargs['json']['params'] == [100]
    finally:
        listener.close()
