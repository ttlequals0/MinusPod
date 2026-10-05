"""Failover route override (#806)."""
from unittest.mock import patch

from tests.app_bootstrap import bootstrap
bootstrap('failover_route_test_')

import failover
import llm_route
import run_context
from llm_route import Route, apply_failover, apply_failover_dict

PRIMARY = Route(phase='detection', provider_key='anthropic', model_id='claude-sonnet-5',
                base_url=None, slot='primary', credential_slot='primary',
                account_id=llm_route.account_identity('anthropic', None))
FAILOVER_CFG = {'provider': 'openai-compatible', 'base_url': 'http://127.0.0.1:11434/v1',
                'timeout': None, 'max_retries': None,
                'models': {'detection': 'qwen3:8b', 'review': '', 'verification': 'qwen3:4b', 'chapters': ''}}


def _active(targets):
    return patch.object(failover, 'is_active', side_effect=lambda t: t in targets)


def _configured(value=True):
    return patch.object(failover, 'is_configured', return_value=value)


def test_inactive_returns_same_route():
    with _active(set()), _configured():
        assert apply_failover(PRIMARY) is PRIMARY


def test_active_primary_rewrites_to_failover_slot():
    with _active({'llm:primary'}), _configured(), \
            patch.object(failover, 'failover_llm_config', return_value=FAILOVER_CFG):
        route = apply_failover(PRIMARY)
    assert route.provider_key == 'openai-compatible'
    assert route.model_id == 'qwen3:8b'
    assert route.slot == 'failover' and route.credential_slot == 'failover'
    assert route.base_url == 'http://127.0.0.1:11434/v1'
    assert route.account_id == llm_route.account_identity('openai-compatible', 'http://127.0.0.1:11434/v1')


def test_phase_model_inheritance():
    chapters = Route(**{**PRIMARY.__dict__, 'phase': 'chapters'})
    verification = Route(**{**PRIMARY.__dict__, 'phase': 'verification'})
    with _active({'llm:primary'}), _configured(), \
            patch.object(failover, 'failover_llm_config', return_value=FAILOVER_CFG):
        assert apply_failover(chapters).model_id == 'qwen3:8b'
        assert apply_failover(verification).model_id == 'qwen3:4b'


def test_secondary_slot_untouched_when_only_primary_failed():
    secondary = Route(**{**PRIMARY.__dict__, 'slot': 'secondary', 'credential_slot': 'secondary'})
    with _active({'llm:primary'}), _configured():
        assert apply_failover(secondary) is secondary


def test_unconfigured_failover_never_overrides():
    with _active({'llm:primary'}), _configured(False):
        assert apply_failover(PRIMARY) is PRIMARY


def test_dict_override_marks_origin():
    snap = {'phase': 'detection', 'provider_key': 'anthropic', 'configured_model': 'claude-sonnet-5',
            'base_url': None, 'credential_slot': 'primary', 'account_id': PRIMARY.account_id}
    with _active({'llm:primary'}), _configured(), \
            patch.object(failover, 'failover_llm_config', return_value=FAILOVER_CFG):
        out = apply_failover_dict(snap)
    assert out['credential_slot'] == 'failover' and out['failover_from'] == 'primary'
    assert out['configured_model'] == 'qwen3:8b'


def test_route_for_phase_applies_live_override():
    ctx = run_context.RunContext.__new__(run_context.RunContext)
    ctx.route_snapshot = {'detection': {'phase': 'detection', 'provider_key': 'anthropic',
                                        'configured_model': 'claude-sonnet-5', 'base_url': None,
                                        'credential_slot': 'primary', 'account_id': PRIMARY.account_id}}
    with patch.object(run_context, 'current', return_value=ctx), _active({'llm:primary'}), \
            _configured(), patch.object(failover, 'failover_llm_config', return_value=FAILOVER_CFG):
        assert run_context.route_for_phase('detection')['credential_slot'] == 'failover'
    with patch.object(run_context, 'current', return_value=ctx), _active(set()), _configured():
        assert run_context.route_for_phase('detection')['credential_slot'] == 'primary'
