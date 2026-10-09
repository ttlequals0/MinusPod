"""Configured Podping RPC endpoints and runtime listener reconfiguration."""
import json
import threading
import time

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('podping_nodes_test_')

from podping_listener import (  # noqa: E402
    DEGRADED_SETTING,
    PODPING_NODE_SETTING,
    PODPING_NODES,
    NODE_HEALTH_SETTING,
    SELECTED_NODE_SETTING,
    PodpingListener,
    get_node_health_summary,
    get_podping_nodes,
    normalize_podping_nodes,
)


class FakeDb:
    def __init__(self, settings=None):
        self.settings = dict(settings or {})

    def get_setting(self, key):
        return self.settings.get(key)

    def set_setting(self, key, value, is_default=False):
        self.settings[key] = value

    def clear_setting(self, key):
        self.settings.pop(key, None)

    def clear_setting_if_equal(self, key, expected):
        if self.settings.get(key) == expected:
            self.settings.pop(key, None)
            return True
        return False


def test_normalizes_and_deduplicates_endpoint_urls():
    assert normalize_podping_nodes([
        ' HTTPS://Example.COM/ ', 'https://example.com', 'http://node.example/rpc/',
    ]) == ['https://example.com', 'http://node.example/rpc']


def test_accepts_local_and_private_rpc_nodes():
    assert normalize_podping_nodes([
        'http://localhost:8090', 'https://127.0.0.1:8091',
        'http://192.168.1.4:8092', 'https://[::1]:8093',
    ]) == [
        'http://localhost:8090', 'https://127.0.0.1:8091',
        'http://192.168.1.4:8092', 'https://[::1]:8093',
    ]


@pytest.mark.parametrize('nodes', [
    [],
    [''],
    ['ftp://node.example'],
    ['https://user:pass@node.example'],
    ['https://@node.example'],
    ['https://node.example?token=secret'],
    ['https://node.example#fragment'],
    ['https://node name.example'],
    ['https://node\n.example'],
    ['https://node\t.example'],
    ['https://node.example:99999'],
    ['https://node.example'] * 21,
])
def test_rejects_invalid_or_unbounded_endpoint_lists(nodes):
    with pytest.raises(ValueError):
        normalize_podping_nodes(nodes)


def test_effective_nodes_and_health_follow_configured_order():
    nodes = ['https://second.example', 'https://first.example']
    db = FakeDb({PODPING_NODE_SETTING: json.dumps(nodes)})

    assert get_podping_nodes(db) == nodes
    assert [row['node'] for row in get_node_health_summary(db)] == nodes
    assert get_podping_nodes(FakeDb()) == PODPING_NODES


def test_runtime_reconfiguration_keeps_block_and_feed_state_and_maps_rotation():
    db = FakeDb({SELECTED_NODE_SETTING: PODPING_NODES[1]})
    listener = PodpingListener(db=db, sleep=lambda _: None)
    try:
        listener.node_index = 1
        listener.current_block = 12345
        listener.feed_map = {'https://feed.example/rss': 'feed'}
        listener.feed_rules = {'feed': {'uses_podping': True}}
        db.set_setting(PODPING_NODE_SETTING, json.dumps([
            'https://new.example', PODPING_NODES[1],
        ]))

        assert listener.refresh_nodes() is True
        assert listener.nodes == ['https://new.example', PODPING_NODES[1]]
        assert listener.node_index == 1
        assert listener.current_block == 12345
        assert listener.feed_map == {'https://feed.example/rss': 'feed'}
        assert listener.feed_rules == {'feed': {'uses_podping': True}}
        assert db.get_setting(SELECTED_NODE_SETTING) == PODPING_NODES[1]
    finally:
        listener.close()


def test_removing_failed_node_resets_invalid_selection_and_degraded_state():
    removed = PODPING_NODES[1]
    db = FakeDb({
        PODPING_NODE_SETTING: json.dumps([PODPING_NODES[0], removed]),
        SELECTED_NODE_SETTING: removed,
        DEGRADED_SETTING: '1',
    })
    listener = PodpingListener(db=db, sleep=lambda _: None)
    try:
        listener._failed_nodes = {PODPING_NODES[0], removed}
        db.set_setting(PODPING_NODE_SETTING, json.dumps([PODPING_NODES[0]]))

        listener.refresh_nodes()

        assert listener._failed_nodes == {PODPING_NODES[0]}
        assert db.get_setting(SELECTED_NODE_SETTING) is None
        assert db.get_setting(DEGRADED_SETTING) == '1'
        listener._failed_nodes.clear()
        listener._set_degraded(False)
        assert db.get_setting(DEGRADED_SETTING) == '0'
    finally:
        listener.close()


def test_configuration_change_discards_inflight_probe_results():
    started = threading.Event()
    release = threading.Event()

    def probe(node):
        started.set()
        release.wait(timeout=2)
        return 200, 'healthy', None

    db = FakeDb()
    listener = PodpingListener(db=db, node_probe=probe, sleep=lambda _: None)
    try:
        listener._start_node_probes()
        assert started.wait(timeout=2)
        db.set_setting(PODPING_NODE_SETTING, json.dumps(['https://new.example']))
        listener.refresh_nodes()
        release.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not all(
                future.done() for future in listener._probe_futures.values()):
            time.sleep(0.005)
        listener._finish_node_probes()

        assert listener._node_health == {}
    finally:
        release.set()
        listener.close()


def test_backoff_refreshes_config_and_discards_completed_old_probes(monkeypatch):
    import main_app.background as background

    original = ['https://old.example', 'https://other.example']
    db = FakeDb({PODPING_NODE_SETTING: json.dumps(original)})
    listener = PodpingListener(
        db=db, node_probe=lambda _node: (200, 'healthy', None))
    sleeps = []

    class Running:
        @staticmethod
        def is_set():
            return False

    monkeypatch.setattr(background, 'shutdown_event', Running())
    listener._start_node_probes()
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and not all(
            future.done() for future in listener._probe_futures.values()):
        time.sleep(0.005)

    def change_config(seconds):
        sleeps.append(seconds)
        db.set_setting(PODPING_NODE_SETTING, json.dumps(['https://new.example']))

    listener.sleep = change_config
    try:
        listener.wait_with_monitor(360)

        assert sleeps == [3]
        assert listener.nodes == ['https://new.example']
        assert listener._node_health == {}
        assert db.get_setting(NODE_HEALTH_SETTING) is None
    finally:
        listener.close()


def test_catchup_refreshes_nodes_before_each_block_probe():
    old = ['https://old.example', 'https://other.example']
    db = FakeDb({PODPING_NODE_SETTING: json.dumps(old)})
    probed_lists = []
    listener = None

    def rpc(method, params):
        if method.endswith('get_dynamic_global_properties'):
            return {'head_block_number': 12}
        probed_lists.append(tuple(listener.nodes))
        if params == [11]:
            db.set_setting(PODPING_NODE_SETTING, json.dumps(['https://new.example']))
        return {'transactions': []}

    listener = PodpingListener(rpc=rpc, db=db, sleep=lambda _: None)
    listener.current_block = 10
    listener._maybe_refresh_feed_map = lambda: None
    try:
        listener.tick()

        assert probed_lists == [tuple(old), ('https://new.example',)]
        assert listener.current_block == 12
    finally:
        listener.close()
