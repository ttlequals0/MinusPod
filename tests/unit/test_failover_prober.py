"""Failover prober (#806)."""
import itertools
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import transcriber

from tests.app_bootstrap import bootstrap
bootstrap('failover_prober_test_')

import failover
from database import Database


@pytest.fixture(autouse=True)
def _clock():
    """One second per timestamp, so probe and trigger times are strictly ordered."""
    ticks = itertools.count()
    start = datetime.now(timezone.utc)
    with patch.object(failover, 'utc_now_iso',
                      side_effect=lambda: (start + timedelta(seconds=next(ticks))).strftime('%Y-%m-%dT%H:%M:%SZ')):
        yield
    db = Database()
    _reset(db)
    for key in ('failover_whisper_backend', 'failover_recovery_probes', 'whisper_backend'):
        db.clear_setting(key)
    failover.invalidate_cache()


def _reset(db):
    for t in failover.PROBE_TARGETS:
        db.clear_setting(f'failover_probe:{t}')
        db.clear_setting(f'failover_probe_lease:{t}')
    for t in failover.TARGETS:
        db.clear_setting(f'failover_state:{t}')
        db.clear_setting(f'failover_generation:{t}')
    db.set_setting('failover_llm_enabled', 'true', is_default=False)
    db.set_setting('failover_llm_provider', 'openai-compatible', is_default=False)
    db.set_setting('failover_llm_detection_model', 'qwen3:8b', is_default=False)
    db.set_setting('secondary_provider_enabled', 'false', is_default=False)
    db.set_setting('whisper_backend', 'local', is_default=False)
    db.set_setting('failover_whisper_enabled', 'false', is_default=False)
    db.set_setting('failover_recovery_probes', '2', is_default=False)
    failover.invalidate_cache()


def _probe(results):
    def fake(t, request_config=None):
        ok = results.get(t, True)
        return {'reachable': ok, 'status': 200 if ok else 503,
                'detail': '' if ok else 'The server is reachable but rejected the test request (HTTP 503).'}
    return patch.object(failover, 'probe_target', side_effect=fake)


def test_enabled_targets_follow_configuration():
    db = Database(); _reset(db)
    assert failover.enabled_probe_targets() == ['llm:primary', 'llm:failover', 'whisper:active']
    db.set_setting('secondary_provider_enabled', 'true', is_default=False)
    db.set_setting('whisper_backend', 'openai-api', is_default=False)
    db.set_setting('failover_whisper_enabled', 'true', is_default=False)
    db.set_setting('failover_whisper_backend', 'local', is_default=False)
    failover.invalidate_cache()
    assert failover.enabled_probe_targets() == [
        'llm:primary', 'llm:secondary', 'llm:failover', 'whisper:active', 'whisper:failover']


def test_two_failures_trigger_and_recovery_cancels():
    db = Database(); _reset(db)
    down = {'llm:primary': False, 'llm:failover': True}
    up = {'llm:primary': True, 'llm:failover': True}
    with _probe(down), patch.object(failover.webhook_service, 'fire_failover_event'):
        failover.probe_tick(db)
        assert failover.is_active('llm:primary') is False
        assert failover.probe_state('llm:primary')['failed_streak'] == 1
        failover.probe_tick(db)
        assert failover.is_active('llm:primary') is True
        assert failover.state('llm:primary')['source'] == 'probe'
        # The recorded reason must carry the probe's own detail text (#806
        # ruling): a persistent 4xx still counts as unreachable, but the
        # webhook/events/card must show why, not just a bare streak count.
        trigger_event = next(e for e in failover.recent_events() if e['action'] == 'trigger')
        assert 'HTTP 503' in trigger_event['reason']
    with _probe(up), patch.object(failover.webhook_service, 'fire_failover_event'):
        failover.probe_tick(db)
        assert failover.is_active('llm:primary') is True   # streak 1 of 2
        failover.probe_tick(db)
        assert failover.is_active('llm:primary') is False
    assert failover.probe_state('llm:primary')['healthy_streak'] == 2


def test_manual_failover_not_cancelled_by_probes():
    db = Database(); _reset(db)
    failover.trigger('llm:primary', 'operator', source='manual')
    with _probe({'llm:primary': True, 'llm:failover': True}), patch.object(failover.webhook_service, 'fire_failover_event'):
        for _ in range(3):
            failover.probe_tick(db)
    assert failover.is_active('llm:primary') is True


def test_probe_failure_without_configured_failover_only_records():
    db = Database(); _reset(db)
    db.set_setting('failover_llm_enabled', 'false', is_default=False)
    failover.invalidate_cache()
    with _probe({'llm:primary': False}):
        failover.probe_tick(db); failover.probe_tick(db)
    assert failover.is_active('llm:primary') is False
    assert failover.probe_state('llm:primary')['reachable'] is False


def test_ensure_fresh_probes_skips_recent():
    db = Database(); _reset(db)
    with _probe({'llm:primary': True, 'llm:failover': True}) as p:
        failover.probe_tick(db)
        failover.ensure_fresh_probes(['llm:primary'])
        assert p.call_count == 3   # tick probed all three; ensure_fresh probed nothing
    stale = json.loads(db.get_setting('failover_probe:llm:primary'))
    stale['checked_at'] = '2000-01-01T00:00:00Z'
    db.set_setting('failover_probe:llm:primary', json.dumps(stale), is_default=False)
    failover.invalidate_cache()
    with _probe({'llm:primary': True}) as p:
        failover.ensure_fresh_probes(['llm:primary'])
        assert p.call_count == 1


def test_ensure_fresh_probes_rechecks_recent_result_after_configuration_change(
        preserve_setting):
    preserve_setting('provider_config_revision')
    db = Database(); _reset(db)
    db.set_setting('provider_config_revision', 'before', is_default=False)
    with _probe({'llm:primary': True}) as p:
        failover.probe_tick(db, ['llm:primary'])
        db.set_setting('provider_config_revision', 'after', is_default=False)
        failover.ensure_fresh_probes(['llm:primary'])
    assert p.call_count == 2


def test_current_probe_state_rejects_recent_result_after_configuration_change(
        preserve_setting):
    preserve_setting('provider_config_revision')
    db = Database(); _reset(db)
    db.set_setting('provider_config_revision', 'before', is_default=False)
    with _probe({'llm:primary': True}):
        failover.probe_tick(db, ['llm:primary'])
        before = failover._capture_probe_context(db, 'llm:primary')['config_identity']
        db.set_setting('provider_config_revision', 'after', is_default=False)
        after = failover._capture_probe_context(db, 'llm:primary')['config_identity']
        assert before != after
        assert failover.probe_state('llm:primary')['reachable'] is True
        assert failover.current_probe_state('llm:primary')['reachable'] is None


def test_current_probe_state_rejects_result_after_generation_change():
    db = Database(); _reset(db)
    with _probe({'llm:primary': True}):
        failover.probe_tick(db, ['llm:primary'])
        failover.trigger('llm:primary', 'test')
    assert failover.current_probe_state('llm:primary')['reachable'] is None


def test_current_probe_state_rejects_result_after_local_outcome_changes(preserve_setting):
    preserve_setting('transcribe_last_local_outcome')
    db = Database(); _reset(db)
    with _probe({'whisper:active': True}):
        failover.probe_tick(db, ['whisper:active'])
        db.set_setting('transcribe_last_local_outcome', '{"outcome":"failure"}',
                       is_default=False)
    assert failover.current_probe_state('whisper:active')['reachable'] is None


def test_probe_tick_probes_targets_concurrently():
    db = Database(); _reset(db)

    def slow(target, request_config=None):
        time.sleep(0.3)
        return {'reachable': True, 'status': 200, 'detail': ''}
    with patch.object(failover, 'probe_target', side_effect=slow) as p:
        started = time.monotonic()
        failover.probe_tick(db, ['llm:primary', 'llm:failover'])
        elapsed = time.monotonic() - started
    assert p.call_count == 2 and elapsed < 0.55
    assert failover.probe_state('llm:failover')['reachable'] is True


def test_fast_probe_result_is_recorded_while_another_target_is_still_running():
    db = Database(); _reset(db)
    primary_started = threading.Event()
    release_primary = threading.Event()
    failover_finished = threading.Event()
    failover_recorded = threading.Event()

    def probe(target, request_config=None):
        if target == 'llm:primary':
            primary_started.set()
            assert release_primary.wait(5)
        else:
            failover_finished.set()
        return {'reachable': True, 'status': 200, 'detail': ''}

    record_probe = failover._record_probe

    def record(db_arg, target, result, context):
        recorded_result = record_probe(db_arg, target, result, context)
        if target == 'llm:failover':
            failover_recorded.set()
        return recorded_result

    with patch.object(failover, 'probe_target', side_effect=probe), \
            patch.object(failover, '_record_probe', side_effect=record):
        worker = threading.Thread(
            target=failover.probe_tick, args=(db, ['llm:primary', 'llm:failover']))
        worker.start()
        assert primary_started.wait(5)
        assert failover_finished.wait(5)
        assert failover_recorded.wait(5)
        recorded = failover.probe_state('llm:failover')['checked_at'] is not None
        release_primary.set()
        worker.join(5)

    assert not worker.is_alive()
    assert recorded is True


def test_concurrent_ensure_fresh_probes_share_one_probe():
    db = Database(); _reset(db)
    entered = threading.Barrier(4)
    started = threading.Event()
    release = threading.Event()

    def slow(target, request_config=None):
        started.set()
        assert release.wait(5)
        return {'reachable': True, 'status': 200, 'detail': ''}

    def ensure():
        entered.wait(timeout=5)
        failover.ensure_fresh_probes(['llm:primary'])

    with patch.object(failover, 'probe_target', side_effect=slow) as p:
        workers = [threading.Thread(target=ensure) for _ in range(3)]
        for w in workers:
            w.start()
        entered.wait(timeout=5)
        assert started.wait(timeout=5)
        time.sleep(0.1)
        assert p.call_count == 1
        release.set()
        for w in workers:
            w.join()
    assert p.call_count == 1


def test_ensure_fresh_probes_waits_for_distinct_targets_concurrently():
    waiting = threading.Barrier(2)

    def wait_for_target(_db, target, _checked_at, wait_for_inflight):
        assert wait_for_inflight is True
        waiting.wait(timeout=5)
        return {'target': target}

    with patch.object(failover, 'probe_state', return_value={'checked_at': None}), \
            patch.object(failover, '_probe_requested_target', side_effect=wait_for_target) as run:
        failover.ensure_fresh_probes(['llm:primary', 'llm:secondary'])

    assert {call.args[1] for call in run.call_args_list} == {
        'llm:primary', 'llm:secondary',
    }


def test_probe_tick_empty_targets_does_not_probe_everything():
    db = Database(); _reset(db)
    with patch.object(failover, 'probe_target') as probe:
        assert failover.probe_tick(db, []) == {}
    probe.assert_not_called()


def test_probe_lease_prevents_duplicate_cross_thread_requests():
    db = Database(); _reset(db)
    started = threading.Event()
    release = threading.Event()

    def slow(target, request_config=None):
        started.set()
        assert release.wait(5)
        return {'reachable': True, 'status': 200, 'detail': ''}

    with patch.object(failover, 'probe_target', side_effect=slow) as probe:
        worker = threading.Thread(target=failover.probe_tick,
                                  args=(db, ['llm:primary']))
        worker.start()
        assert started.wait(5)
        result = failover.probe_tick(db, ['llm:primary'])
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert probe.call_count == 1
    assert result['llm:primary']['checked_at'] is None
    assert failover.probe_state('llm:primary')['reachable'] is True


def test_expired_probe_owner_cannot_overwrite_newer_result():
    db = Database(); _reset(db)
    first_started = threading.Event()
    release_first = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def probe(target, request_config=None):
        nonlocal calls
        with calls_lock:
            calls += 1
            first_call = calls == 1
        if first_call:
            first_started.set()
            assert release_first.wait(5)
            return {'reachable': False, 'status': 503, 'detail': 'old result'}
        return {'reachable': True, 'status': 200, 'detail': 'new result'}

    with patch.object(failover, 'probe_target', side_effect=probe):
        worker = threading.Thread(
            target=failover.probe_tick, args=(db, ['llm:primary']),
            name='slow-probe')
        worker.start()
        assert first_started.wait(5)
        db.set_setting(
            'failover_probe_lease:llm:primary',
            json.dumps({'token': 'expired-owner', 'expires_at': time.time() - 1}),
            is_default=False,
        )
        failover.probe_tick(db, ['llm:primary'])
        release_first.set()
        worker.join(5)
    assert not worker.is_alive()
    assert failover.probe_state('llm:primary')['reachable'] is True
    assert failover.probe_state('llm:primary')['detail'] == 'new result'


def test_probe_renews_lease_while_slow_request_is_running():
    db = Database(); _reset(db)
    started = threading.Event()
    renewed = threading.Event()
    release = threading.Event()
    original_renew = failover._renew_probe_lease

    def renew(lease_db, target, token):
        result = original_renew(lease_db, target, token)
        renewed.set()
        return result

    def slow_probe(_target, request_config=None):
        started.set()
        assert release.wait(5)
        return {'reachable': True, 'status': 200, 'detail': ''}

    with patch.object(failover, '_PROBE_LEASE_SECONDS', 0.15), \
            patch.object(failover, '_PROBE_LEASE_RENEW_SECONDS', 0.01), \
            patch.object(failover, '_renew_probe_lease', side_effect=renew), \
            patch.object(failover, 'probe_target', side_effect=slow_probe) as probe:
        worker = threading.Thread(target=failover.probe_tick, args=(db, ['llm:primary']))
        worker.start()
        assert started.wait(5)
        assert renewed.wait(5)
        failover.probe_tick(db, ['llm:primary'])
        assert probe.call_count == 1
        release.set()
        worker.join(5)

    assert not worker.is_alive()
    assert probe.call_count == 1


def test_probe_lease_stays_owned_until_result_publication_finishes():
    db = Database(); _reset(db)
    publishing = threading.Event()
    renewed_during_publish = threading.Event()
    release_publish = threading.Event()
    original_renew = failover._renew_probe_lease
    original_record = failover._record_probe

    def renew(lease_db, target, token):
        result = original_renew(lease_db, target, token)
        if publishing.is_set():
            renewed_during_publish.set()
        return result

    def record(db_arg, target, result, context):
        publishing.set()
        assert release_publish.wait(5)
        return original_record(db_arg, target, result, context)

    with patch.object(failover, '_PROBE_LEASE_SECONDS', 0.15), \
            patch.object(failover, '_PROBE_LEASE_RENEW_SECONDS', 0.01), \
            patch.object(failover, '_renew_probe_lease', side_effect=renew), \
            patch.object(failover, '_record_probe', side_effect=record), \
            patch.object(failover, 'probe_target', return_value={
                'reachable': True, 'status': 200, 'detail': '',
            }) as probe:
        worker = threading.Thread(target=failover.probe_tick, args=(db, ['llm:primary']))
        worker.start()
        assert publishing.wait(5)
        assert renewed_during_publish.wait(5)
        failover.probe_tick(db, ['llm:primary'])
        assert probe.call_count == 1
        release_publish.set()
        worker.join(5)

    assert not worker.is_alive()
    assert probe.call_count == 1


def test_free_target_publishes_before_another_target_waits_for_lease():
    db = Database(); _reset(db)
    db.set_setting('failover_probe_lease:llm:primary', json.dumps({
        'token': 'active-owner', 'expires_at': time.time() + 10,
    }), is_default=False)
    primary_waiting = threading.Event()
    release_primary_wait = threading.Event()
    failover_recorded = threading.Event()
    original_wait = failover._claim_or_wait
    original_record = failover._record_probe

    def wait_for_target(wait_db, target, checked_at, wait_for_inflight):
        if target == 'llm:primary':
            primary_waiting.set()
            assert release_primary_wait.wait(5)
            return None
        return original_wait(wait_db, target, checked_at, wait_for_inflight)

    def record(db_arg, target, result, context):
        value = original_record(db_arg, target, result, context)
        if target == 'llm:failover':
            failover_recorded.set()
        return value

    with patch.object(failover, '_claim_or_wait', side_effect=wait_for_target), \
            patch.object(failover, '_record_probe', side_effect=record), \
            patch.object(failover, 'probe_target', return_value={
                'reachable': True, 'status': 200, 'detail': '',
            }) as probe:
        worker = threading.Thread(
            target=failover.ensure_fresh_probes,
            args=(['llm:primary', 'llm:failover'],))
        worker.start()
        assert primary_waiting.wait(5)
        assert failover_recorded.wait(5)
        assert worker.is_alive()
        assert {call.args[0] for call in probe.call_args_list} == {'llm:failover'}
        release_primary_wait.set()
        worker.join(5)

    assert not worker.is_alive()


def test_run_probe_targets_skip_standby_and_local_transcriber():
    db = Database(); _reset(db)
    assert failover.run_probe_targets() == ['llm:primary']
    db.set_setting('secondary_provider_enabled', 'true', is_default=False)
    db.set_setting('whisper_backend', 'openai-api', is_default=False)
    db.set_setting('failover_whisper_enabled', 'true', is_default=False)
    failover.invalidate_cache()
    assert failover.run_probe_targets() == ['llm:primary', 'llm:secondary', 'whisper:active']


def test_run_probe_targets_uses_only_effective_phase_slots():
    db = Database(); _reset(db)
    phases = {
        'detection': {'credential_slot': 'secondary'},
        'review': {'credential_slot': 'secondary'},
        'verification': {'credential_slot': 'failover'},
    }
    assert failover.run_probe_targets(phases, whisper_required=False) == [
        'llm:secondary', 'llm:failover',
    ]
    assert failover.run_probe_targets({}, whisper_required=False) == []


def test_run_probe_targets_uses_effective_whisper_backend():
    db = Database(); _reset(db)
    db.set_setting('whisper_backend', 'openai-api', is_default=False)
    failover.invalidate_cache()
    assert failover.run_probe_targets({}, whisper_required=True) == ['whisper:active']
    db.set_setting('failover_state:whisper', json.dumps({
        'active': True, 'source': 'manual', 'since': '2026-01-01T00:00:00Z',
        'reason': 'operator',
    }), is_default=False)
    db.set_setting('failover_whisper_backend', 'openai-api', is_default=False)
    db.set_setting('failover_whisper_enabled', 'true', is_default=False)
    db.set_setting('failover_whisper_api_base_url', 'https://standby.example/v1', is_default=False)
    assert failover.run_probe_targets({}, whisper_required=True) == ['whisper:failover']
    assert failover.run_probe_targets({}, whisper_required=False) == []


def test_local_whisper_standby_does_not_probe_the_original_api():
    db = Database(); _reset(db)
    db.set_setting('whisper_backend', 'openai-api', is_default=False)
    db.set_setting('failover_whisper_enabled', 'true', is_default=False)
    db.set_setting('failover_whisper_backend', 'local', is_default=False)
    db.set_setting('failover_whisper_model', 'tiny', is_default=False)
    db.set_setting('failover_state:whisper', json.dumps({
        'active': True, 'source': 'auto', 'since': '2026-01-01T00:00:00Z',
        'reason': 'provider outage',
    }), is_default=False)
    failover.invalidate_cache()
    with patch.object(transcriber, 'local_transcription_available', return_value=True):
        assert failover.run_probe_targets({}, whisper_required=True) == []
    with patch.object(transcriber, 'local_transcription_available', return_value=False):
        assert failover.run_probe_targets({}, whisper_required=True) == ['whisper:active']


def test_run_probe_targets_falls_back_when_active_standby_is_unconfigured():
    db = Database(); _reset(db)
    db.set_setting('whisper_backend', 'openai-api', is_default=False)
    db.set_setting('failover_state:whisper', json.dumps({
        'active': True, 'source': 'auto', 'since': '2026-01-01T00:00:00Z',
        'reason': 'outage',
    }), is_default=False)
    failover.invalidate_cache()
    assert failover.run_probe_targets({}, whisper_required=True) == ['whisper:active']


def test_unresolved_route_fallback_probes_the_effective_configured_slot():
    db = Database(); _reset(db)
    db.set_setting('failover_state:llm:primary', json.dumps({
        'active': True, 'source': 'auto', 'since': '2026-01-01T00:00:00Z',
        'reason': 'outage',
    }), is_default=False)
    failover.invalidate_cache()
    with patch('main_app.processing._resolve_route_snapshot', return_value=None):
        assert failover.run_probe_targets(None, whisper_required=False) == ['llm:failover']

    db.set_setting('failover_llm_enabled', 'false', is_default=False)
    failover.invalidate_cache()
    with patch('main_app.processing._resolve_route_snapshot', return_value=None):
        assert failover.run_probe_targets(None, whisper_required=False) == ['llm:primary']


def test_runtime_trigger_resets_recovery_streak():
    db = Database(); _reset(db)
    db.set_setting('failover_recovery_probes', '3', is_default=False)
    failover.invalidate_cache()
    up = {'llm:primary': True, 'llm:failover': True}
    with _probe(up), patch.object(failover.webhook_service, 'fire_failover_event'):
        for _ in range(3):
            failover.probe_tick(db)
        assert failover.probe_state('llm:primary')['healthy_streak'] == 3
        failover.trigger('llm:primary', 'window outage')
        assert failover.probe_state('llm:primary')['healthy_streak'] == 0
        failover.probe_tick(db)
        assert failover.is_active('llm:primary') is True
        failover.probe_tick(db); failover.probe_tick(db)
        assert failover.is_active('llm:primary') is False


def test_recovery_ignores_probe_older_than_trigger():
    db = Database(); _reset(db)
    with patch.object(failover.webhook_service, 'fire_failover_event'):
        failover.trigger('llm:primary', 'window outage')
        since = failover.state('llm:primary')['since']
        db.set_setting('failover_probe:llm:primary', json.dumps({
            'reachable': True, 'checked_at': '2000-01-01T00:00:00Z', 'healthy_streak': 5}), is_default=False)
        failover.invalidate_cache()
        with patch.object(failover, 'utc_now_iso', return_value=since), \
                _probe({'llm:primary': True, 'llm:failover': True}):
            failover.probe_tick(db, ['llm:primary'])
    assert failover.is_active('llm:primary') is True


def test_delayed_healthy_result_is_discarded_after_trigger():
    db = Database(); _reset(db)
    record_started = threading.Event()
    release_record = threading.Event()

    def fake_probe(target, request_config=None):
        return {'reachable': True, 'status': 200, 'detail': ''}

    original_record = failover._record_probe

    def delayed_record(db_arg, target, result, context):
        if target == 'llm:primary':
            record_started.set()
            assert release_record.wait(5)
        return original_record(db_arg, target, result, context)

    with patch.object(failover, 'probe_target', side_effect=fake_probe), \
            patch.object(failover, '_record_probe', side_effect=delayed_record), \
            patch.object(failover.webhook_service, 'fire_failover_event'):
        worker = threading.Thread(
            target=failover.probe_tick, args=(db, ['llm:primary', 'llm:failover']))
        worker.start()
        assert record_started.wait(5)
        failover.trigger('llm:primary', 'probe overlap')
        release_record.set()
        worker.join(5)
    assert not worker.is_alive()
    assert failover.is_active('llm:primary') is True
    assert failover.probe_state('llm:primary')['checked_at'] is None
    assert failover.probe_state('llm:primary')['healthy_streak'] == 0


def test_probe_from_previous_generation_is_discarded_after_same_second_retrigger():
    db = Database(); _reset(db)
    probe_started = threading.Event()
    release_probe = threading.Event()

    def slow_probe(target, request_config=None):
        probe_started.set()
        assert release_probe.wait(5)
        return {'reachable': True, 'status': 200, 'detail': ''}

    with patch.object(failover, 'probe_target', side_effect=slow_probe), \
            patch.object(failover.webhook_service, 'fire_failover_event'), \
            patch.object(failover, 'utc_now_iso', return_value='2026-10-05T12:00:00Z'):
        failover.trigger('llm:primary', 'first outage')
        first_since = failover.state('llm:primary')['since']
        worker = threading.Thread(
            target=failover.probe_tick, args=(db, ['llm:primary']))
        worker.start()
        assert probe_started.wait(5)
        assert failover.cancel('llm:primary', source='auto') is True
        assert failover.trigger('llm:primary', 'second outage', source='auto') is True
        assert failover.state('llm:primary')['since'] == first_since
        release_probe.set()
        worker.join(5)
    assert not worker.is_alive()
    assert failover.state('llm:primary')['active'] is True
    assert failover.probe_state('llm:primary')['checked_at'] is None
    assert failover.probe_state('llm:primary')['healthy_streak'] == 0


def test_probe_result_is_discarded_after_target_configuration_changes():
    db = Database(); _reset(db)
    db.set_setting('openai_base_url', 'https://first.example/v1', is_default=False)
    failover.invalidate_cache()
    probe_started = threading.Event()
    release_probe = threading.Event()

    def slow_probe(target, request_config=None):
        probe_started.set()
        assert release_probe.wait(5)
        return {'reachable': True, 'status': 200, 'detail': ''}

    with patch.object(failover, 'probe_target', side_effect=slow_probe):
        worker = threading.Thread(
            target=failover.probe_tick, args=(db, ['llm:primary']))
        worker.start()
        assert probe_started.wait(5)
        db.set_setting('openai_base_url', 'https://second.example/v1', is_default=False)
        failover.invalidate_cache()
        release_probe.set()
        worker.join(5)
    assert not worker.is_alive()
    assert failover.probe_state('llm:primary')['checked_at'] is None
    assert failover.probe_state('llm:primary')['healthy_streak'] == 0


def test_probe_uses_the_captured_config_when_stale_cache_is_repopulated():
    db = Database(); _reset(db)
    db.set_setting('llm_provider', 'openai-compatible', is_default=False)
    db.set_setting('openai_base_url', 'https://captured.example/v1', is_default=False)
    db.set_setting('openai_api_key', 'captured-key', is_default=False)
    failover.invalidate_cache()
    capture = failover._capture_probe_context
    requests = []

    def capture_then_repopulate(db_arg, target):
        context = capture(db_arg, target)
        with failover.llm_client._provider_cache_lock:
            failover.llm_client._provider_cache.set('llm_provider', 'anthropic')
            failover.llm_client._provider_cache.set('openai_base_url', 'https://stale.example/v1')
            failover.llm_client._provider_cache.set('openai_api_key', 'stale-key')
        return context

    def record_request(endpoint, api_key):
        requests.append((endpoint, api_key))
        return {'ok': True, 'reachable': True, 'status': 200, 'detail': ''}

    with patch.object(failover, '_capture_probe_context', side_effect=capture_then_repopulate), \
            patch.object(failover.provider_probe, 'probe_models_endpoint', side_effect=record_request):
        failover.probe_tick(db, ['llm:primary'])
    expected_endpoint = failover.llm_client._normalize_base_url_for_provider(
        'openai-compatible', 'https://captured.example/v1')
    assert requests == [(expected_endpoint, 'captured-key')]
    assert failover._probe_request_config(db, 'llm:primary', {'llm_provider': 'ollama'}, {})[
        'api_key'] == 'not-needed'


class _Http:
    def __init__(self, result):
        self.result = result

    def __enter__(self):
        self.p = patch.object(failover.provider_probe, 'probe_models_endpoint', return_value=self.result)
        return self.p.__enter__()

    def __exit__(self, *exc):
        return self.p.__exit__(*exc)


def _whisper_api(db):
    db.set_setting('whisper_backend', 'openai-api', is_default=False)
    db.set_setting('whisper_api_base_url', 'http://example.com/v1', is_default=False)
    failover.invalidate_cache()


@pytest.mark.parametrize('status', [400, 401, 402, 408, 429, 500, 503])
def test_whisper_probe_rejects_unhealthy_http_statuses(status):
    db = Database(); _reset(db); _whisper_api(db)
    with _Http({'reachable': True, 'status': status, 'detail': ''}):
        assert failover.probe_target('whisper:active')['reachable'] is False
    with _Http({'reachable': False, 'status': None, 'detail': 'refused'}):
        assert failover.probe_target('whisper:active')['reachable'] is False


def test_whisper_probe_404_is_reachable():
    db = Database(); _reset(db); _whisper_api(db)
    with _Http({'reachable': True, 'status': 404, 'detail': ''}):
        assert failover.probe_target('whisper:active')['reachable'] is True


def test_whisper_probe_accepts_successful_status():
    db = Database(); _reset(db); _whisper_api(db)
    with _Http({'reachable': True, 'status': 200, 'detail': ''}):
        assert failover.probe_target('whisper:active')['reachable'] is True


def test_llm_probe_404_stays_unreachable():
    db = Database(); _reset(db)
    db.set_setting('failover_llm_base_url', 'http://example.com/v1', is_default=False)
    failover.invalidate_cache()
    with _Http({'reachable': True, 'status': 404, 'detail': ''}):
        assert failover.probe_target('llm:failover')['reachable'] is False


@pytest.mark.parametrize('result', [
    {'ok': False, 'reachable': True, 'status': 200, 'detail': 'malformed model list'},
    {'ok': False, 'reachable': True, 'status': 429, 'detail': 'rate limited'},
])
def test_invalid_llm_probe_response_is_not_healthy(result):
    db = Database(); _reset(db)
    db.set_setting('failover_llm_base_url', 'http://example.com/v1', is_default=False)
    failover.invalidate_cache()
    with _Http(result):
        assert failover.probe_target('llm:failover')['reachable'] is False


@pytest.mark.parametrize('invalid_result', [
    {'ok': False, 'reachable': True, 'status': 200, 'detail': 'malformed model list'},
    {'ok': False, 'reachable': True, 'status': 429, 'detail': 'rate limited'},
])
def test_only_valid_llm_probe_response_counts_toward_recovery(invalid_result):
    db = Database(); _reset(db)
    db.set_setting('llm_provider', 'openai-compatible', is_default=False)
    db.set_setting('openai_base_url', 'http://example.com/v1', is_default=False)
    db.set_setting('failover_recovery_probes', '1', is_default=False)
    failover.invalidate_cache()
    with patch.object(failover.webhook_service, 'fire_failover_event'):
        failover.trigger('llm:primary', 'provider outage', source='auto')
        with _Http(invalid_result):
            failover.probe_tick(db, ['llm:primary'])
        assert failover.is_active('llm:primary') is True
        assert failover.probe_state('llm:primary')['healthy_streak'] == 0
        with _Http({'ok': True, 'reachable': True, 'status': 200,
                    'detail': 'valid model list'}):
            failover.probe_tick(db, ['llm:primary'])
    assert failover.is_active('llm:primary') is False


@pytest.mark.parametrize('provider', ['anthropic', 'openrouter'])
def test_malformed_fixed_provider_response_does_not_recover(provider):
    db = Database(); _reset(db)
    db.set_setting('llm_provider', provider, is_default=False)
    db.set_setting('failover_llm_enabled', 'true', is_default=False)
    db.set_setting('failover_llm_provider', 'anthropic', is_default=False)
    db.set_setting('failover_llm_detection_model', 'claude-example', is_default=False)
    db.set_setting('failover_recovery_probes', '1', is_default=False)
    failover.invalidate_cache()
    malformed = {'ok': False, 'reachable': True, 'status': 200,
                 'detail': 'malformed provider response'}
    healthy = {'ok': True, 'reachable': True, 'status': 200,
               'detail': 'valid provider response'}
    with patch.object(failover.webhook_service, 'fire_failover_event'), \
            patch.object(failover.provider_probe, 'probe_fixed_endpoint',
                         side_effect=[malformed, healthy]) as probe:
        failover.trigger('llm:primary', 'provider outage', source='auto')
        failover.probe_tick(db, ['llm:primary'])
        assert failover.is_active('llm:primary') is True
        assert failover.probe_state('llm:primary')['healthy_streak'] == 0
        failover.probe_tick(db, ['llm:primary'])
    assert probe.call_count == 2
    assert failover.is_active('llm:primary') is False


def test_llm_probe_read_timeout_is_unreachable():
    # utils/connection_probe.run_probe reports a read timeout as
    # {'reachable': True, no 'status'}: the connection succeeded but no HTTP
    # response ever arrived, so this must not read as healthy.
    db = Database(); _reset(db)
    db.set_setting('failover_llm_base_url', 'http://example.com/v1', is_default=False)
    failover.invalidate_cache()
    with _Http({'ok': False, 'reachable': True,
               'detail': 'The server accepted the connection but did not answer within 10 seconds.'}):
        assert failover.probe_target('llm:failover')['reachable'] is False


def test_local_whisper_probes_follow_local_stack():
    db = Database(); _reset(db)
    db.set_setting('failover_whisper_enabled', 'true', is_default=False)
    db.set_setting('failover_whisper_backend', 'local', is_default=False)
    failover.invalidate_cache()
    with patch.object(transcriber, 'local_transcription_available', return_value=True):
        assert failover.probe_target('whisper:active')['reachable'] is True
        assert failover.probe_target('whisper:failover')['reachable'] is True
    with patch.object(transcriber, 'local_transcription_available', return_value=False):
        assert failover.probe_target('whisper:active')['reachable'] is False
        assert failover.probe_target('whisper:failover')['reachable'] is False


def test_local_recovery_outcome_is_persisted_with_accepted_probe():
    db = Database(); _reset(db)
    db.set_setting('whisper_model', 'tiny', is_default=False)
    db.set_setting('failover_state:whisper', json.dumps({
        'active': True, 'source': 'auto', 'since': '2026-01-01T00:00:00Z',
        'reason': 'local decode failed',
    }), is_default=False)
    failover.invalidate_cache()
    outcome = {
        'outcome': 'success', 'backend': 'local', 'model': 'tiny',
        'device': 'cpu', 'compute_type': 'auto',
        'observed_at': '2026-10-05T00:00:00.123456Z',
    }
    with patch.object(transcriber, 'probe_local_transcription', return_value={
        'reachable': True, 'status': None, 'detail': 'diagnostic decode succeeded',
        'local_outcome': outcome,
    }) as probe:
        failover.probe_tick(db, ['whisper:active'])

    request_config = probe.call_args.args[0]
    assert request_config['recover_runtime'] is True
    assert request_config['local_model'] == 'tiny'
    assert json.loads(db.get_setting('transcribe_last_local_outcome')) == outcome
    assert 'local_outcome' not in failover.probe_state('whisper:active')


def test_stale_local_recovery_does_not_overwrite_new_failure():
    db = Database(); _reset(db)
    db.set_setting('whisper_model', 'tiny', is_default=False)
    db.set_setting('failover_state:whisper', json.dumps({
        'active': True, 'source': 'auto', 'since': '2026-01-01T00:00:00Z',
        'reason': 'local decode failed',
    }), is_default=False)
    failover.invalidate_cache()
    newer_failure = {
        'outcome': 'failed', 'backend': 'local', 'model': 'tiny',
        'device': 'cpu', 'compute_type': 'auto',
        'observed_at': '2026-10-05T00:00:00.654321Z',
    }
    stale_success = {**newer_failure, 'outcome': 'success',
                     'observed_at': '2026-10-05T00:00:00.123456Z'}

    def delayed_probe(_config):
        db.set_setting('transcribe_last_local_outcome', json.dumps(newer_failure),
                       is_default=False)
        return {'reachable': True, 'status': None, 'detail': 'diagnostic decode succeeded',
                'local_outcome': stale_success}

    with patch.object(transcriber, 'probe_local_transcription', side_effect=delayed_probe):
        failover.probe_tick(db, ['whisper:active'])

    assert json.loads(db.get_setting('transcribe_last_local_outcome')) == newer_failure
    assert failover.probe_state('whisper:active')['checked_at'] is None


def test_unconfigured_target_never_counts_or_triggers():
    db = Database(); _reset(db)
    db.set_setting('secondary_provider_enabled', 'true', is_default=False)
    db.clear_setting('secondary_provider')
    failover.invalidate_cache()
    with patch.object(failover.webhook_service, 'fire_failover_event'):
        failover.probe_tick(db, ['llm:secondary'])
        failover.probe_tick(db, ['llm:secondary'])
    data = failover.probe_state('llm:secondary')
    assert data['reachable'] is None and data['checked_at']
    assert data['failed_streak'] == 0 and data['healthy_streak'] == 0
    assert failover.is_active('llm:secondary') is False
