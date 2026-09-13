"""Tests for the per-phase LLM route resolver (src/llm_route.py)."""
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap
bootstrap('llm_route_test_')

import llm_route
from config import ModelNotConfiguredError
from llm_route import Route, resolve_route


def _db(settings):
    db = MagicMock()
    db.get_setting.side_effect = lambda k: settings.get(k)
    return db


def _resolving(settings, effective_provider='anthropic'):
    return (
        patch.object(llm_route, 'Database', return_value=_db(settings)),
        patch.object(llm_route, 'get_effective_provider', return_value=effective_provider),
    )


def test_detection_route_uses_settings_and_global_provider_default():
    settings = {'claude_model': 'claude-sonnet-5'}
    with patch.object(llm_route, 'Database', return_value=_db(settings)), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
        route = resolve_route('detection')
    assert route == Route(phase='detection', provider_key='anthropic',
                           model_id='claude-sonnet-5', base_url=None)


def test_detection_route_raises_when_model_unconfigured():
    with patch.object(llm_route, 'Database', return_value=_db({})), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
        with pytest.raises(ModelNotConfiguredError, match='claude_model'):
            resolve_route('detection')


def test_verification_inherits_detection_provider_by_default():
    settings = {
        'claude_model': 'claude-sonnet-5',
        'detection_provider': 'openrouter',
        'verification_model': 'claude-opus-4-8',
    }
    with patch.object(llm_route, 'Database', return_value=_db(settings)), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
        route = resolve_route('verification')
    assert route.provider_key == 'openrouter'
    assert route.model_id == 'claude-opus-4-8'


def test_verification_uses_explicit_provider_when_set():
    settings = {
        'claude_model': 'claude-sonnet-5',
        'detection_provider': 'openrouter',
        'verification_provider': 'ollama',
        'verification_model': 'claude-opus-4-8',
    }
    with patch.object(llm_route, 'Database', return_value=_db(settings)), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
        route = resolve_route('verification')
    assert route.provider_key == 'ollama'


def test_verification_model_falls_back_to_detection_model():
    settings = {'claude_model': 'claude-sonnet-5'}
    with patch.object(llm_route, 'Database', return_value=_db(settings)), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
        route = resolve_route('verification')
    assert route.model_id == 'claude-sonnet-5'


def test_chapters_inherits_detection_provider_by_default():
    settings = {
        'claude_model': 'claude-sonnet-5',
        'detection_provider': 'openrouter',
    }
    with patch.object(llm_route, 'Database', return_value=_db(settings)), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
        route = resolve_route('chapters')
    assert route.provider_key == 'openrouter'
    assert route.model_id == 'claude-sonnet-5'  # falls back to claude_model


def test_review_same_as_pass_inherits_both_pass_provider_and_model():
    with patch.object(llm_route, 'Database', return_value=_db({})), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
        route = resolve_route('review', pass_provider='openrouter',
                               pass_model='claude-sonnet-5')
    assert route.provider_key == 'openrouter'
    assert route.model_id == 'claude-sonnet-5'


def test_review_same_as_pass_ignores_review_model_setting():
    # review_provider defaults to same_as_pass, which pins the model to the
    # pass model too -- a leftover review_model override is not consulted.
    settings = {'review_model': 'claude-opus-4-8'}
    with patch.object(llm_route, 'Database', return_value=_db(settings)), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
        route = resolve_route('review', pass_provider='anthropic',
                               pass_model='claude-sonnet-5')
    assert route.model_id == 'claude-sonnet-5'


def test_review_same_as_pass_requires_pass_provider_and_model():
    with patch.object(llm_route, 'Database', return_value=_db({})), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
        with pytest.raises(ValueError, match='pass_provider'):
            resolve_route('review')


def test_review_explicit_provider_uses_its_own_provider_and_model():
    settings = {
        'review_provider': 'openrouter',
        'review_model': 'claude-opus-4-8',
    }
    with patch.object(llm_route, 'Database', return_value=_db(settings)), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
        route = resolve_route('review', pass_provider='anthropic',
                               pass_model='claude-sonnet-5')
    assert route.provider_key == 'openrouter'
    assert route.model_id == 'claude-opus-4-8'


def test_review_explicit_provider_with_same_as_pass_model_uses_pass_model():
    settings = {'review_provider': 'openrouter'}
    with patch.object(llm_route, 'Database', return_value=_db(settings)), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
        route = resolve_route('review', pass_provider='anthropic',
                               pass_model='claude-sonnet-5')
    assert route.provider_key == 'openrouter'
    assert route.model_id == 'claude-sonnet-5'


def test_identical_model_id_under_two_providers_yields_distinct_routes():
    settings = {
        'claude_model': 'shared-model-id',
        'detection_provider': 'anthropic',
        'verification_provider': 'openrouter',
        'verification_model': 'shared-model-id',
    }
    with patch.object(llm_route, 'Database', return_value=_db(settings)), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
        detection = resolve_route('detection')
        verification = resolve_route('verification')
    assert detection.model_id == verification.model_id == 'shared-model-id'
    assert detection.provider_key != verification.provider_key
    assert detection != verification


def test_unknown_phase_raises():
    with pytest.raises(ValueError, match='Unknown LLM phase'):
        resolve_route('bogus')


def test_base_url_for_openai_compatible_provider_uses_effective_base_url():
    settings = {
        'claude_model': 'local-model',
        'detection_provider': 'openai-compatible',
    }
    with patch.object(llm_route, 'Database', return_value=_db(settings)), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'), \
            patch.object(llm_route, 'get_effective_base_url',
                          return_value='http://localhost:8000/v1'):
        route = resolve_route('detection')
    assert route.base_url == 'http://localhost:8000/v1'
    assert 'key' not in route.base_url and '@' not in route.base_url
