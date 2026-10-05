"""Failover prober (#806)."""
import json
from unittest.mock import patch

from tests.app_bootstrap import bootstrap
bootstrap('failover_prober_test_')

import failover
from database import Database


def _reset(db):
    for t in failover.PROBE_TARGETS:
        db.clear_setting(f'failover_probe:{t}')
    for t in failover.TARGETS:
        db.clear_setting(f'failover_state:{t}')
    db.set_setting('failover_llm_enabled', 'true', is_default=False)
    db.set_setting('failover_llm_provider', 'openai-compatible', is_default=False)
    db.set_setting('failover_llm_detection_model', 'qwen3:8b', is_default=False)
    db.set_setting('secondary_provider_enabled', 'false', is_default=False)
    db.set_setting('whisper_backend', 'local', is_default=False)
    db.set_setting('failover_whisper_enabled', 'false', is_default=False)
    db.set_setting('failover_recovery_probes', '2', is_default=False)
    failover.invalidate_cache()


def _probe(results):
    return patch.object(failover, 'probe_target',
                        side_effect=lambda t: {'reachable': results[t], 'status': 200 if results[t] else 503, 'detail': ''})


def test_enabled_targets_follow_configuration():
    db = Database(); _reset(db)
    assert failover.enabled_probe_targets() == ['llm:primary', 'llm:failover']
    db.set_setting('secondary_provider_enabled', 'true', is_default=False)
    db.set_setting('whisper_backend', 'openai-api', is_default=False)
    failover.invalidate_cache()
    assert failover.enabled_probe_targets() == ['llm:primary', 'llm:secondary', 'llm:failover', 'whisper:active']


def test_two_failures_trigger_and_recovery_cancels():
    db = Database(); _reset(db)
    down = {'llm:primary': False, 'llm:failover': True}
    up = {'llm:primary': True, 'llm:failover': True}
    with _probe(down), patch.object(failover, 'fire_failover_event'):
        failover.probe_tick(db)
        assert failover.is_active('llm:primary') is False
        assert failover.probe_state('llm:primary')['failed_streak'] == 1
        failover.probe_tick(db)
        assert failover.is_active('llm:primary') is True
        assert failover.state('llm:primary')['source'] == 'probe'
    with _probe(up), patch.object(failover, 'fire_failover_event'):
        failover.probe_tick(db)
        assert failover.is_active('llm:primary') is True   # streak 1 of 2
        failover.probe_tick(db)
        assert failover.is_active('llm:primary') is False
    assert failover.probe_state('llm:primary')['healthy_streak'] == 2


def test_manual_failover_not_cancelled_by_probes():
    db = Database(); _reset(db)
    failover.trigger('llm:primary', 'operator', source='manual')
    with _probe({'llm:primary': True, 'llm:failover': True}), patch.object(failover, 'fire_failover_event'):
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
        assert p.call_count == 2   # tick probed both; ensure_fresh probed nothing
    stale = json.loads(db.get_setting('failover_probe:llm:primary'))
    stale['checked_at'] = '2000-01-01T00:00:00Z'
    db.set_setting('failover_probe:llm:primary', json.dumps(stale), is_default=False)
    failover.invalidate_cache()
    with _probe({'llm:primary': True}) as p:
        failover.ensure_fresh_probes(['llm:primary'])
        assert p.call_count == 1
