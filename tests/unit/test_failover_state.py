"""Failover state service (#806)."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from tests.app_bootstrap import bootstrap
bootstrap('failover_state_test_')

import failover
from database import Database


def _configure_llm(db):
    db.set_setting('failover_llm_enabled', 'true', is_default=False)
    db.set_setting('failover_llm_provider', 'openai-compatible', is_default=False)
    db.set_setting('failover_llm_detection_model', 'qwen3:8b', is_default=False)


def _reset(db):
    for key in ('failover_llm_enabled', 'failover_llm_provider',
                'failover_llm_detection_model', 'failover_llm_review_model',
                'failover_whisper_enabled', 'failover_whisper_backend',
                'failover_whisper_api_base_url'):
        db.clear_setting(key)
    for target in failover.TARGETS:
        db.clear_setting(f'failover_state:{target}')
    db.get_connection().execute('DELETE FROM failover_events')
    failover.invalidate_cache()


def test_trigger_requires_configuration():
    db = Database(); _reset(db)
    assert failover.is_configured('llm:primary') is False
    assert failover.trigger('llm:primary', 'boom') is False
    assert failover.is_active('llm:primary') is False


def test_trigger_cancel_round_trip_and_events():
    db = Database(); _reset(db); _configure_llm(db)
    with patch.object(failover.webhook_service, 'fire_failover_event') as fire:
        assert failover.trigger('llm:primary', 'HTTP 503') is True
        assert failover.trigger('llm:primary', 'again') is False  # idempotent
        st = failover.state('llm:primary')
        assert st['active'] is True and st['source'] == 'auto' and st['reason'] == 'HTTP 503'
        assert failover.cancel('llm:primary', source='auto') is True
        assert failover.is_active('llm:primary') is False
    actions = [(e['action'], e['source']) for e in failover.recent_events()]
    assert actions[:2] == [('cancel', 'auto'), ('trigger', 'auto')]
    assert fire.call_count == 2


def test_manual_trigger_sticks_through_auto_cancel():
    db = Database(); _reset(db); _configure_llm(db)
    failover.trigger('llm:primary', 'operator', source='manual')
    assert failover.cancel('llm:primary', source='auto') is False
    assert failover.is_active('llm:primary') is True
    assert failover.cancel('llm:primary', source='manual') is True


def test_manual_upgrades_auto():
    db = Database(); _reset(db); _configure_llm(db)
    failover.trigger('llm:primary', 'HTTP 503')
    assert failover.trigger('llm:primary', 'operator', source='manual') is True
    assert failover.state('llm:primary')['source'] == 'manual'


def test_auto_trigger_blocked_by_existing_manual():
    db = Database(); _reset(db); _configure_llm(db)
    failover.trigger('llm:primary', 'operator', source='manual')
    assert failover.trigger('llm:primary', 'HTTP 503') is False
    assert failover.state('llm:primary')['source'] == 'manual'


def test_auto_cancel_racing_manual_trigger_keeps_manual_state_and_event():
    db = Database(); _reset(db); _configure_llm(db)
    assert failover.trigger('llm:primary', 'automatic') is True
    db.get_connection().execute('DELETE FROM failover_events')
    db.get_connection().commit()
    first_state_read = threading.Event()
    release_auto_cancel = threading.Event()
    manual_begin_attempted = threading.Event()
    manual_begin_acquired = threading.Event()
    manual_state_read = threading.Event()
    original_state_read = failover._state_in_transaction
    original_begin = Database._TransactionContext.__enter__

    def pause_auto_cancel(conn, target):
        state = original_state_read(conn, target)
        if threading.current_thread().name.startswith('auto-cancel'):
            assert conn.in_transaction
            first_state_read.set()
            if not release_auto_cancel.wait(timeout=5):
                raise TimeoutError('manual trigger did not start')
        elif threading.current_thread().name.startswith('manual-trigger'):
            manual_state_read.set()
        return state

    def observe_manual_transaction(context):
        is_manual = threading.current_thread().name.startswith('manual-trigger')
        if is_manual:
            manual_begin_attempted.set()
        conn = original_begin(context)
        if is_manual:
            manual_begin_acquired.set()
        return conn

    def cancel_auto():
        return failover.cancel('llm:primary', source='auto')

    def trigger_manual():
        return failover.trigger('llm:primary', 'operator', source='manual')

    with patch.object(failover, '_state_in_transaction', side_effect=pause_auto_cancel), \
            patch.object(Database._TransactionContext, '__enter__', observe_manual_transaction), \
            ThreadPoolExecutor(max_workers=1, thread_name_prefix='auto-cancel') as auto_executor, \
            ThreadPoolExecutor(max_workers=1, thread_name_prefix='manual-trigger') as manual_executor:
        cancel_future = auto_executor.submit(cancel_auto)
        assert first_state_read.wait(timeout=5)
        trigger_future = manual_executor.submit(trigger_manual)
        assert manual_begin_attempted.wait(timeout=5)
        assert manual_begin_acquired.wait(timeout=0.05) is False
        assert manual_state_read.is_set() is False
        release_auto_cancel.set()
        cancel_result = cancel_future.result(timeout=10)
        trigger_result = trigger_future.result(timeout=10)

    assert failover.state('llm:primary')['source'] == 'manual'
    assert trigger_result is True
    events = list(reversed(failover.recent_events()))
    assert cancel_result is True
    assert [(event['action'], event['source']) for event in events] == [
        ('cancel', 'auto'), ('trigger', 'manual'),
    ]


def test_auto_trigger_racing_manual_trigger_keeps_manual_state_and_event():
    db = Database(); _reset(db); _configure_llm(db)
    barrier = threading.Barrier(2)

    def trigger(source, reason):
        barrier.wait(timeout=5)
        return failover.trigger('llm:primary', reason, source=source)

    with ThreadPoolExecutor(max_workers=2) as executor:
        auto_future = executor.submit(trigger, 'auto', 'outage')
        manual_future = executor.submit(trigger, 'manual', 'operator')
        auto_result = auto_future.result(timeout=10)
        manual_result = manual_future.result(timeout=10)

    assert auto_result in (False, True)
    assert manual_result is True
    assert failover.state('llm:primary')['source'] == 'manual'
    events = list(reversed(failover.recent_events()))
    assert [(event['action'], event['source']) for event in events] in (
        [('trigger', 'manual')],
        [('trigger', 'auto'), ('trigger', 'manual')],
    )


def test_whisper_configured_rules():
    db = Database(); _reset(db)
    db.set_setting('failover_whisper_enabled', 'true', is_default=False)
    db.set_setting('failover_whisper_backend', 'openai-api', is_default=False)
    failover.invalidate_cache()
    assert failover.is_configured('whisper') is False
    db.set_setting('failover_whisper_api_base_url', 'http://127.0.0.1:8001/v1', is_default=False)
    failover.invalidate_cache()
    assert failover.is_configured('whisper') is True


def test_whisper_local_backend_requires_local_stack():
    import transcriber
    db = Database(); _reset(db)
    db.set_setting('failover_whisper_enabled', 'true', is_default=False)
    db.set_setting('failover_whisper_backend', 'local', is_default=False)
    failover.invalidate_cache()
    with patch.object(transcriber, 'local_transcription_available', return_value=False):
        assert failover.is_configured('whisper') is False
    with patch.object(transcriber, 'local_transcription_available', return_value=True):
        assert failover.is_configured('whisper') is True


def test_llm_model_inheritance():
    db = Database(); _reset(db); _configure_llm(db)
    db.set_setting('failover_llm_review_model', 'qwen3:4b', is_default=False)
    failover.invalidate_cache()
    assert failover.failover_llm_model('review') == 'qwen3:4b'
    assert failover.failover_llm_model('chapters') == 'qwen3:8b'


def test_state_survives_json_garbage():
    db = Database(); _reset(db)
    db.set_setting('failover_state:whisper', 'not json', is_default=False)
    failover.invalidate_cache()
    assert failover.is_active('whisper') is False


def test_auto_trigger_rereads_state_another_worker_wrote():
    import json
    db = Database(); _reset(db); _configure_llm(db)
    assert failover.is_active('llm:primary') is False  # primes the cache
    db.set_setting('failover_state:llm:primary', json.dumps(
        {'active': True, 'source': 'manual', 'since': '2026-01-01T00:00:00Z', 'reason': 'drill'}),
        is_default=False)
    with patch.object(failover.webhook_service, 'fire_failover_event'):
        assert failover.trigger('llm:primary', 'window outage') is False
    assert failover.state('llm:primary')['source'] == 'manual'
    _reset(db)


def test_state_reads_manual_changes_without_waiting_for_settings_cache_ttl():
    db = Database(); _reset(db); _configure_llm(db)
    key = 'failover_state:llm:primary'
    failover.llm_client._provider_cache.set(key, failover.llm_client._CACHED_NONE)
    assert failover.llm_client._get_cached_setting(key) is None
    active = {'active': True, 'source': 'manual', 'since': '2026-01-01T00:00:00Z',
              'reason': 'drill'}
    db.set_setting(key, json.dumps(active), is_default=False)
    assert failover.state('llm:primary') == active

    failover.llm_client._provider_cache.set(key, json.dumps(active))
    db.clear_setting(key)
    assert failover.state('llm:primary') == failover._INACTIVE
    _reset(db)


def test_trigger_and_cancel_swallow_write_failures():
    db = Database(); _reset(db); _configure_llm(db)
    with patch.object(failover, '_apply_transition', side_effect=RuntimeError('db locked')):
        assert failover.trigger('llm:primary', 'HTTP 503') is False
    assert failover.is_active('llm:primary') is False
    with patch.object(failover.webhook_service, 'fire_failover_event'):
        failover.trigger('llm:primary', 'HTTP 503')
    with patch.object(failover, '_apply_transition', side_effect=RuntimeError('db locked')):
        assert failover.cancel('llm:primary') is False
    assert failover.is_active('llm:primary') is True
    _reset(db)


def test_transition_error_can_be_requested_for_api_callers():
    db = Database(); _reset(db); _configure_llm(db)
    with patch.object(failover, '_apply_transition', side_effect=RuntimeError('db locked')):
        with pytest.raises(failover.FailoverTransitionError):
            failover.trigger('llm:primary', 'HTTP 503', raise_on_error=True)
    assert failover.is_active('llm:primary') is False
    _reset(db)


def test_trigger_rolls_back_state_after_post_write_failure():
    db = Database(); _reset(db); _configure_llm(db)
    original = Database._upsert_setting

    def fail_after_state_write(conn, key, value, is_default):
        original(conn, key, value, is_default)
        if key == 'failover_state:llm:primary':
            raise RuntimeError('event transaction failed')

    with patch.object(Database, '_upsert_setting', side_effect=fail_after_state_write):
        assert failover.trigger('llm:primary', 'HTTP 503') is False
    assert failover.is_active('llm:primary') is False
    assert failover.recent_events() == []
    _reset(db)


def test_trigger_resets_healthy_streak_atomically():
    db = Database(); _reset(db); _configure_llm(db)
    db.set_setting('failover_probe:llm:primary', json.dumps({
        'reachable': True, 'healthy_streak': 3, 'failed_streak': 0,
    }), is_default=False)
    assert failover.trigger('llm:primary', 'HTTP 503') is True
    assert failover.probe_state('llm:primary')['healthy_streak'] == 0
    _reset(db)


def test_webhook_failure_keeps_the_state_change():
    db = Database(); _reset(db); _configure_llm(db)
    with patch.object(failover.webhook_service, 'fire_failover_event', side_effect=RuntimeError('smtp down')):
        assert failover.trigger('llm:primary', 'HTTP 503') is True
        assert failover.is_active('llm:primary') is True
        assert failover.cancel('llm:primary') is True
    assert failover.is_active('llm:primary') is False
    _reset(db)
