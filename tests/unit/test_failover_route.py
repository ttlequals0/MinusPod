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


def test_same_as_pass_review_on_failover_uses_failover_review_model():
    cfg = {**FAILOVER_CFG, 'models': {**FAILOVER_CFG['models'], 'review': 'qwen3:14b'}}
    with patch.object(failover, 'failover_llm_config', return_value=cfg):
        route = llm_route.resolve_review_route(
            review_provider_setting='same_as_pass', review_model_setting=None,
            pass_provider='openai-compatible', pass_model='qwen3:8b',
            pass_base_url='http://127.0.0.1:11434/v1', pass_credential_slot='failover')
        assert route.model_id == 'qwen3:14b' and route.credential_slot == 'failover'
    with patch.object(failover, 'failover_llm_config', return_value=FAILOVER_CFG):
        route = llm_route.resolve_review_route(
            review_provider_setting='same_as_pass', review_model_setting=None,
            pass_provider='openai-compatible', pass_model='qwen3:4b',
            pass_base_url='http://127.0.0.1:11434/v1', pass_credential_slot='failover')
        assert route.model_id == 'qwen3:8b'


def test_client_for_failover_dict_does_not_raise_account_changed():
    snap = {'phase': 'detection', 'provider_key': 'anthropic', 'configured_model': 'claude-sonnet-5',
            'base_url': None, 'credential_slot': 'primary', 'account_id': PRIMARY.account_id}
    with _active({'llm:primary'}), _configured(), \
            patch.object(failover, 'failover_llm_config', return_value=FAILOVER_CFG), \
            patch.object(llm_route, 'get_client_for_provider', return_value='client') as build:
        assert llm_route.client_for_route(apply_failover_dict(snap)) == 'client'
    assert build.call_args.kwargs['credential_slot'] == 'failover'


def test_reviewer_live_route_follows_mid_pass_trigger():
    from unittest.mock import MagicMock
    from ad_reviewer import AdReviewer
    reviewer = AdReviewer(MagicMock())
    reviewer._active_route = Route(**{**PRIMARY.__dict__, 'phase': 'review'})
    with _active(set()), _configured():
        assert reviewer._live_route() is reviewer._active_route
    with _active({'llm:primary'}), _configured(), \
            patch.object(failover, 'failover_llm_config', return_value=FAILOVER_CFG):
        route = reviewer._live_route()
    assert (route.credential_slot, route.model_id) == ('failover', 'qwen3:8b')
