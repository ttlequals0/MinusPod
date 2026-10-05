"""Failover state service (#806)."""
from unittest.mock import patch

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
    with patch.object(failover, 'fire_failover_event') as fire:
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


def test_whisper_configured_rules():
    db = Database(); _reset(db)
    db.set_setting('failover_whisper_enabled', 'true', is_default=False)
    db.set_setting('failover_whisper_backend', 'openai-api', is_default=False)
    failover.invalidate_cache()
    assert failover.is_configured('whisper') is False
    db.set_setting('failover_whisper_api_base_url', 'http://127.0.0.1:8001/v1', is_default=False)
    failover.invalidate_cache()
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
    with patch.object(failover, 'fire_failover_event'):
        assert failover.trigger('llm:primary', 'window outage') is False
    assert failover.state('llm:primary')['source'] == 'manual'
    _reset(db)
