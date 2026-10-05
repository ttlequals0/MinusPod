"""Failover route override (#806)."""
from unittest.mock import MagicMock, patch

from tests.app_bootstrap import bootstrap
bootstrap('failover_route_test_')

import failover
import llm_route
import run_context
from llm_route import Route, apply_failover, apply_failover_dict
from main_app import processing
from ad_reviewer import AdReviewer

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
            patch.object(llm_route.llm_client, 'get_client_for_provider', return_value='client') as build:
        assert llm_route.client_for_route(apply_failover_dict(snap)) == 'client'
    assert build.call_args.kwargs['credential_slot'] == 'failover'


def test_reviewer_live_route_follows_mid_pass_trigger():
    reviewer = AdReviewer(MagicMock())
    reviewer._active_route = Route(**{**PRIMARY.__dict__, 'phase': 'review'})
    with _active(set()), _configured():
        assert reviewer._live_route() is reviewer._active_route
    with _active({'llm:primary'}), _configured(), \
            patch.object(failover, 'failover_llm_config', return_value=FAILOVER_CFG):
        route = reviewer._live_route()
    assert (route.credential_slot, route.model_id) == ('failover', 'qwen3:8b')


def test_new_snapshot_retains_original_routes_while_standby_is_active():
    secondary = Route(**{**PRIMARY.__dict__, 'phase': 'verification',
                         'slot': 'secondary', 'credential_slot': 'secondary'})
    routes = {phase: Route(**{**PRIMARY.__dict__, 'phase': phase})
              for phase in ('detection', 'review', 'chapters')}
    routes['verification'] = secondary
    with patch.object(processing, 'resolve_route', side_effect=lambda phase, **kw: routes[phase]), \
            patch.object(processing, 'Database', return_value=MagicMock()), \
            _active({'llm:primary', 'llm:secondary'}), _configured(), \
            patch.object(failover, 'failover_llm_config', return_value=FAILOVER_CFG):
        snapshot = processing._resolve_route_snapshot()
        ctx = run_context.RunContext.__new__(run_context.RunContext)
        ctx.route_snapshot = snapshot
        with patch.object(run_context, 'current', return_value=ctx):
            assert run_context.route_for_phase('detection')['configured_model'] == 'qwen3:8b'
            assert run_context.route_for_phase('verification')['configured_model'] == 'qwen3:4b'
    with patch.object(run_context, 'current', return_value=ctx), _active(set()):
        assert run_context.route_for_phase('detection') is snapshot['detection']
        assert run_context.route_for_phase('verification') is snapshot['verification']
    assert snapshot['detection']['credential_slot'] == 'primary'
    assert snapshot['verification']['credential_slot'] == 'secondary'
    assert snapshot['detection']['account_id'] == PRIMARY.account_id


def test_reviewer_started_on_standby_restores_original_frozen_pass():
    reviewer = AdReviewer(MagicMock())
    ctx = run_context.RunContext.__new__(run_context.RunContext)
    ctx.route_snapshot = {
        'detection': {'provider_key': PRIMARY.provider_key, 'configured_model': PRIMARY.model_id,
                      'base_url': PRIMARY.base_url, 'credential_slot': 'primary'},
        'review': {'provider_key': PRIMARY.provider_key, 'configured_model': PRIMARY.model_id,
                   'base_url': PRIMARY.base_url, 'credential_slot': 'primary',
                   'gate': {'review_provider': 'same_as_pass', 'review_model': 'same_as_pass'}}}
    with patch.object(run_context, 'current', return_value=ctx), \
            _active({'llm:primary'}), _configured(), \
            patch.object(failover, 'failover_llm_config', return_value=FAILOVER_CFG):
        reviewer._active_route = reviewer._resolve_route('openai-compatible', 'qwen3:8b')
        assert reviewer._live_route().credential_slot == 'failover'
    with _active(set()):
        restored = reviewer._live_route()
    assert restored.credential_slot == 'primary'
    assert restored.model_id == PRIMARY.model_id
    assert restored.provider_key == PRIMARY.provider_key


def test_admission_uses_live_standby_without_mutating_original_snapshot():
    snapshot = {'detection': {'provider_key': PRIMARY.provider_key,
                             'configured_model': PRIMARY.model_id,
                             'base_url': PRIMARY.base_url, 'credential_slot': 'primary'}}
    with patch.object(processing, 'Database'), \
            patch.object(processing, '_admission_mode_rows', return_value=({}, {})), \
            patch.object(processing, 'resolve_processing_mode', return_value='standard'), \
            patch.object(processing, 'resolve_skip_second_pass', return_value=True), \
            patch.object(processing, '_chapters_enabled_for_admission', return_value=False), \
            _active({'llm:primary'}), _configured(), \
            patch.object(failover, 'failover_llm_config', return_value=FAILOVER_CFG):
        routes = processing._active_phases_for_admission(
            'example-podcast', snapshot=snapshot, gates={'review': False, 'chapters_enabled': False})
    assert routes['detection']['credential_slot'] == 'failover'
    assert routes['detection']['configured_model'] == 'qwen3:8b'
    assert snapshot['detection']['credential_slot'] == 'primary'
