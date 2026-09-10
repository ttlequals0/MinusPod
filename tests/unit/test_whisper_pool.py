"""WhisperPool: bounded admission for remote Whisper requests."""
import threading
import time

import whisper_pool
from whisper_pool import WhisperPool


def _settings(enabled=True, backend='openai-api', max_requests=3, max_episodes=2):
    state = {'enabled': enabled, 'backend': backend,
             'max_requests': max_requests, 'max_episodes': max_episodes}
    return state, (lambda: dict(state))


def test_inactive_when_toggle_off():
    _, read = _settings(enabled=False)
    pool = WhisperPool(read)
    assert pool.active is False
    assert pool.inactive_reason == 'disabled'
    assert pool.capacity == 0
    assert pool.max_episodes == 1
    assert pool.chunk_workers(7) == 7
    with pool.slot():
        pass
    assert pool.snapshot()['inFlight'] == 0


def test_inactive_on_local_backend():
    _, read = _settings(backend='local')
    pool = WhisperPool(read)
    assert pool.active is False
    assert pool.inactive_reason == 'local_backend'
    assert pool.max_episodes == 1


def test_active_exposes_capacity_and_episodes():
    _, read = _settings(max_requests=5, max_episodes=3)
    pool = WhisperPool(read)
    assert pool.active is True
    assert pool.capacity == 5
    assert pool.max_episodes == 3


def test_chunk_workers_split_capacity_across_transcribing_episodes():
    _, read = _settings(max_requests=4)
    pool = WhisperPool(read)
    assert pool.chunk_workers(8) == 4
    with pool.transcribing():
        assert pool.chunk_workers(8) == 4
        with pool.transcribing():
            assert pool.chunk_workers(8) == 2
            with pool.transcribing():
                assert pool.chunk_workers(8) == 1
                with pool.transcribing():
                    with pool.transcribing():
                        assert pool.chunk_workers(8) == 1  # never below one
    assert pool.chunk_workers(3) == 3  # configured value caps it


def test_in_flight_never_exceeds_capacity():
    _, read = _settings(max_requests=2)
    pool = WhisperPool(read)
    peak = {'n': 0, 'cur': 0}
    lock = threading.Lock()

    def work():
        with pool.slot():
            with lock:
                peak['cur'] += 1
                peak['n'] = max(peak['n'], peak['cur'])
            time.sleep(0.02)
            with lock:
                peak['cur'] -= 1

    threads = [threading.Thread(target=work) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak['n'] == 2


def test_resize_up_and_down_under_held_permits():
    state, read = _settings(max_requests=2)
    pool = WhisperPool(read)
    a = pool.slot(); a.__enter__()
    b = pool.slot(); b.__enter__()
    state['max_requests'] = 1
    pool.refresh()
    assert pool.capacity == 1
    # Both held permits survive; the debt is paid on release.
    a.__exit__(None, None, None)
    assert pool.snapshot()['inFlight'] == 1
    acquired = threading.Event()

    def try_third():
        with pool.slot():
            acquired.set()
    t = threading.Thread(target=try_third); t.start()
    assert not acquired.wait(0.1)  # capacity 1 is still held by b
    b.__exit__(None, None, None)
    assert acquired.wait(1.0)
    t.join()
    state['max_requests'] = 3
    pool.refresh()
    assert pool.capacity == 3


def test_toggle_off_mid_run_keeps_held_permit_and_makes_new_slots_free():
    state, read = _settings(max_requests=1)
    pool = WhisperPool(read)
    a = pool.slot(); a.__enter__()
    state['enabled'] = False
    pool.refresh()
    assert pool.active is False
    with pool.slot():  # no-op now
        pass
    a.__exit__(None, None, None)
    assert pool.snapshot()['inFlight'] == 0


def test_snapshot_shape():
    _, read = _settings(max_requests=4, max_episodes=2)
    pool = WhisperPool(read)
    snap = pool.snapshot()
    # Leader is process-global state (set once at app startup, never reset in
    # tests), so assert against the live value rather than a fixed expectation.
    assert snap == {
        'enabled': True, 'backend': 'openai-api', 'active': True, 'inactiveReason': None,
        'capacity': 4, 'inFlight': 0, 'transcribingEpisodes': 0,
        'maxEpisodes': {'configured': 2, 'effective': 2},
        'leader': whisper_pool.is_background_leader(),
    }


class _StubDb:
    """Settings reader stand-in: only the keys a test seeds are stored."""

    def __init__(self, values=None):
        self.values = values or {}

    def get_setting(self, key):
        return self.values.get(key)

    def get_setting_int(self, key, default=0):
        try:
            return int(self.values[key])
        except (KeyError, TypeError, ValueError):
            return default


def _read_with(monkeypatch, values):
    import database
    monkeypatch.setattr(database, 'Database', lambda *a, **kw: _StubDb(values))
    return whisper_pool._db_settings_reader()()


def test_backend_falls_back_to_the_registry_default(monkeypatch):
    """whisper_backend is not a seeded row, so an install configured only by
    WHISPER_BACKEND has nothing stored for the reader to find."""
    monkeypatch.setenv('WHISPER_BACKEND', 'openai-api')
    value = _read_with(monkeypatch, {'whisper_pool_enabled': 'true'})
    assert value['backend'] == 'openai-api'
    assert WhisperPool(lambda: dict(value)).active is True


def test_bad_numeric_env_falls_back_without_disabling_the_pool(monkeypatch):
    monkeypatch.setenv('WHISPER_BACKEND', 'openai-api')
    monkeypatch.setenv('WHISPER_POOL_MAX_REQUESTS', 'abc')
    value = _read_with(monkeypatch, {'whisper_pool_enabled': 'true'})
    assert value['enabled'] is True
    assert value['max_requests'] == 4


def test_stored_numeric_is_clamped_to_its_range(monkeypatch):
    value = _read_with(monkeypatch, {'whisper_pool_max_requests': '999',
                                     'whisper_pool_max_episodes': '0'})
    assert value['max_requests'] == 64
    assert value['max_episodes'] == 1


def test_unreadable_settings_leave_the_pool_off(monkeypatch):
    import database

    def boom(*a, **kw):
        raise RuntimeError('no database')

    monkeypatch.setattr(database, 'Database', boom)
    assert whisper_pool._db_settings_reader()() == {
        'enabled': False, 'backend': 'local', 'max_requests': 4, 'max_episodes': 1}
