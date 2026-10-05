"""Failover prober (#806)."""
import itertools
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

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
                        side_effect=lambda t: {'reachable': results.get(t, True), 'status': 200 if results.get(t, True) else 503, 'detail': ''})


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
        assert p.call_count == 3   # tick probed all three; ensure_fresh probed nothing
    stale = json.loads(db.get_setting('failover_probe:llm:primary'))
    stale['checked_at'] = '2000-01-01T00:00:00Z'
    db.set_setting('failover_probe:llm:primary', json.dumps(stale), is_default=False)
    failover.invalidate_cache()
    with _probe({'llm:primary': True}) as p:
        failover.ensure_fresh_probes(['llm:primary'])
        assert p.call_count == 1


def test_runtime_trigger_resets_recovery_streak():
    db = Database(); _reset(db)
    db.set_setting('failover_recovery_probes', '3', is_default=False)
    failover.invalidate_cache()
    up = {'llm:primary': True, 'llm:failover': True}
    with _probe(up), patch.object(failover, 'fire_failover_event'):
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
    with patch.object(failover, 'fire_failover_event'):
        failover.trigger('llm:primary', 'window outage')
        since = failover.state('llm:primary')['since']
        db.set_setting('failover_probe:llm:primary', json.dumps({
            'reachable': True, 'checked_at': '2000-01-01T00:00:00Z', 'healthy_streak': 5}), is_default=False)
        failover.invalidate_cache()
        with patch.object(failover, 'utc_now_iso', return_value=since), \
                _probe({'llm:primary': True, 'llm:failover': True}):
            failover.probe_tick(db, ['llm:primary'])
    assert failover.is_active('llm:primary') is True


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


def test_whisper_probe_404_is_reachable_and_503_is_not():
    db = Database(); _reset(db); _whisper_api(db)
    with _Http({'reachable': True, 'status': 404, 'detail': ''}):
        assert failover.probe_target('whisper:active')['reachable'] is True
    with _Http({'reachable': True, 'status': 503, 'detail': ''}):
        assert failover.probe_target('whisper:active')['reachable'] is False
    with _Http({'reachable': True, 'status': 401, 'detail': ''}):
        assert failover.probe_target('whisper:active')['reachable'] is False
    with _Http({'reachable': False, 'status': None, 'detail': 'refused'}):
        assert failover.probe_target('whisper:active')['reachable'] is False


def test_llm_probe_404_stays_unreachable():
    db = Database(); _reset(db)
    db.set_setting('failover_llm_base_url', 'http://example.com/v1', is_default=False)
    failover.invalidate_cache()
    with _Http({'reachable': True, 'status': 404, 'detail': ''}):
        assert failover.probe_target('llm:failover')['reachable'] is False


def test_local_whisper_probes_follow_local_stack():
    import transcriber
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


def test_unconfigured_target_never_counts_or_triggers():
    db = Database(); _reset(db)
    db.set_setting('secondary_provider_enabled', 'true', is_default=False)
    db.clear_setting('secondary_provider')
    failover.invalidate_cache()
    with patch.object(failover, 'fire_failover_event'):
        failover.probe_tick(db, ['llm:secondary'])
        failover.probe_tick(db, ['llm:secondary'])
    data = failover.probe_state('llm:secondary')
    assert data['reachable'] is None and data['checked_at']
    assert data['failed_streak'] == 0 and data['healthy_streak'] == 0
    assert failover.is_active('llm:secondary') is False
