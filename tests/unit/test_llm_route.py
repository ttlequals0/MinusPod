"""Tests for the per-phase LLM route resolver (src/llm_route.py).

Stage settings (detection_provider, verification_provider,
chapters_provider, review_provider) store a SLOT (primary/secondary), not a
provider type (checkpoint 02b task 1).
"""
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


def test_detection_route_uses_settings_and_global_provider_default():
    settings = {'claude_model': 'claude-sonnet-5'}
    with patch.object(llm_route, 'Database', return_value=_db(settings)), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
        route = resolve_route('detection')
    assert route == Route(phase='detection', provider_key='anthropic',
                           model_id='claude-sonnet-5', base_url=None,
                           slot='primary', credential_slot='primary')


def test_detection_route_raises_when_model_unconfigured():
    with patch.object(llm_route, 'Database', return_value=_db({})), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
        with pytest.raises(ModelNotConfiguredError, match='claude_model'):
            resolve_route('detection')


def test_detection_route_uses_secondary_slot_when_configured():
    settings = {
        'claude_model': 'claude-sonnet-5',
        'detection_provider': 'secondary',
        'secondary_provider_enabled': 'true',
        'secondary_provider': 'openrouter',
    }
    with patch.object(llm_route, 'Database', return_value=_db(settings)), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
        route = resolve_route('detection')
    assert route.provider_key == 'openrouter'
    assert route.slot == 'secondary'
    assert route.credential_slot == 'secondary'


def test_detection_secondary_slot_falls_back_to_primary_when_disabled():
    settings = {
        'claude_model': 'claude-sonnet-5',
        'detection_provider': 'secondary',
        'secondary_provider_enabled': 'false',
        'secondary_provider': 'openrouter',
    }
    with patch.object(llm_route, 'Database', return_value=_db(settings)), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
        route = resolve_route('detection')
    assert route.provider_key == 'anthropic'
    assert route.slot == 'primary'
    assert route.credential_slot == 'primary'


def test_detection_secondary_slot_falls_back_to_primary_when_type_unset():
    settings = {
        'claude_model': 'claude-sonnet-5',
        'detection_provider': 'secondary',
        'secondary_provider_enabled': 'true',
    }
    with patch.object(llm_route, 'Database', return_value=_db(settings)), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
        route = resolve_route('detection')
    assert route.provider_key == 'anthropic'
    assert route.slot == 'primary'
    assert route.credential_slot == 'primary'


def test_detection_invalid_slot_value_falls_back_to_primary():
    settings = {
        'claude_model': 'claude-sonnet-5',
        'detection_provider': 'anthropic',  # a raw type, not a slot
    }
    with patch.object(llm_route, 'Database', return_value=_db(settings)), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
        route = resolve_route('detection')
    assert route.slot == 'primary'
    assert route.provider_key == 'anthropic'


def test_verification_inherits_detection_slot_by_default():
    settings = {
        'claude_model': 'claude-sonnet-5',
        'detection_provider': 'secondary',
        'secondary_provider_enabled': 'true',
        'secondary_provider': 'openrouter',
        'verification_model': 'claude-opus-4-8',
    }
    with patch.object(llm_route, 'Database', return_value=_db(settings)), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
        route = resolve_route('verification')
    assert route.provider_key == 'openrouter'
    assert route.slot == 'secondary'
    assert route.credential_slot == 'secondary'
    assert route.model_id == 'claude-opus-4-8'


def test_verification_same_as_detection_explicit_value_inherits():
    settings = {
        'claude_model': 'claude-sonnet-5',
        'detection_provider': 'secondary',
        'secondary_provider_enabled': 'true',
        'secondary_provider': 'openrouter',
        'verification_provider': 'same_as_detection',
    }
    with patch.object(llm_route, 'Database', return_value=_db(settings)), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
        route = resolve_route('verification')
    assert route.provider_key == 'openrouter'
    assert route.slot == 'secondary'


def test_verification_uses_explicit_slot_when_set():
    settings = {
        'claude_model': 'claude-sonnet-5',
        'detection_provider': 'secondary',
        'secondary_provider_enabled': 'true',
        'secondary_provider': 'openrouter',
        'verification_provider': 'primary',
        'verification_model': 'claude-opus-4-8',
    }
    with patch.object(llm_route, 'Database', return_value=_db(settings)), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
        route = resolve_route('verification')
    assert route.provider_key == 'anthropic'
    assert route.slot == 'primary'


def test_verification_model_falls_back_to_detection_model():
    settings = {'claude_model': 'claude-sonnet-5'}
    with patch.object(llm_route, 'Database', return_value=_db(settings)), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
        route = resolve_route('verification')
    assert route.model_id == 'claude-sonnet-5'


def test_chapters_inherits_detection_slot_by_default():
    settings = {
        'claude_model': 'claude-sonnet-5',
        'detection_provider': 'secondary',
        'secondary_provider_enabled': 'true',
        'secondary_provider': 'openrouter',
    }
    with patch.object(llm_route, 'Database', return_value=_db(settings)), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
        route = resolve_route('chapters')
    assert route.provider_key == 'openrouter'
    assert route.model_id == 'claude-sonnet-5'  # falls back to claude_model


def test_review_same_as_pass_inherits_pass_provider_and_model():
    with patch.object(llm_route, 'Database', return_value=_db({})), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
        route = resolve_route('review', pass_provider='openrouter',
                               pass_model='claude-sonnet-5')
    assert route.provider_key == 'openrouter'
    assert route.model_id == 'claude-sonnet-5'


def test_review_same_as_pass_inherits_pass_base_url_and_credential_slot():
    with patch.object(llm_route, 'Database', return_value=_db({})), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
        route = resolve_route(
            'review', pass_provider='openrouter', pass_model='claude-sonnet-5',
            pass_base_url='https://openrouter.ai/api/v1', pass_credential_slot='secondary')
    assert route.base_url == 'https://openrouter.ai/api/v1'
    assert route.credential_slot == 'secondary'
    assert route.slot == 'secondary'


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


def test_review_explicit_secondary_slot_uses_its_own_provider_and_model():
    settings = {
        'review_provider': 'secondary',
        'review_model': 'claude-opus-4-8',
        'secondary_provider_enabled': 'true',
        'secondary_provider': 'openrouter',
    }
    with patch.object(llm_route, 'Database', return_value=_db(settings)), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
        route = resolve_route('review', pass_provider='anthropic',
                               pass_model='claude-sonnet-5')
    assert route.provider_key == 'openrouter'
    assert route.model_id == 'claude-opus-4-8'
    assert route.credential_slot == 'secondary'


def test_review_explicit_secondary_disabled_falls_back_to_primary(caplog):
    settings = {
        'review_provider': 'secondary',
        'review_model': 'claude-opus-4-8',
        'secondary_provider_enabled': 'false',
        'secondary_provider': 'openrouter',
    }
    with patch.object(llm_route, 'Database', return_value=_db(settings)), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
        route = resolve_route('review', pass_provider='anthropic',
                               pass_model='claude-sonnet-5')
    assert route.provider_key == 'anthropic'
    assert route.credential_slot == 'primary'


def test_review_explicit_slot_with_same_as_pass_model_uses_pass_model():
    settings = {
        'review_provider': 'secondary',
        'secondary_provider_enabled': 'true',
        'secondary_provider': 'openrouter',
    }
    with patch.object(llm_route, 'Database', return_value=_db(settings)), \
            patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
        route = resolve_route('review', pass_provider='anthropic',
                               pass_model='claude-sonnet-5')
    assert route.provider_key == 'openrouter'
    assert route.model_id == 'claude-sonnet-5'


def test_two_slots_same_type_different_base_yield_distinct_credential_slots():
    """Both primary and secondary configured as openai-compatible, but with
    different base URLs: routes must carry the same provider_key with
    distinct base_url + credential_slot so Task 2 reads the right secret."""
    settings = {
        'claude_model': 'shared-model',
        'detection_provider': 'primary',
        'chapters_provider': 'secondary',
        'secondary_provider_enabled': 'true',
        'secondary_provider': 'openai-compatible',
        'secondary_provider_base_url': 'http://secondary-host:9000/v1',
    }
    with patch.object(llm_route, 'Database', return_value=_db(settings)), \
            patch.object(llm_route, 'get_effective_provider', return_value='openai-compatible'), \
            patch.object(llm_route, 'get_effective_base_url',
                          return_value='http://primary-host:8000/v1'):
        detection = resolve_route('detection')
        chapters = resolve_route('chapters')
    assert detection.provider_key == chapters.provider_key == 'openai-compatible'
    assert detection.base_url == 'http://primary-host:8000/v1'
    assert chapters.base_url == 'http://secondary-host:9000/v1'
    assert detection.credential_slot == 'primary'
    assert chapters.credential_slot == 'secondary'
    assert detection != chapters


def test_identical_model_id_under_two_slots_yields_distinct_routes():
    settings = {
        'claude_model': 'shared-model-id',
        'detection_provider': 'primary',
        'verification_provider': 'secondary',
        'secondary_provider_enabled': 'true',
        'secondary_provider': 'openrouter',
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
        'detection_provider': 'primary',
    }
    with patch.object(llm_route, 'Database', return_value=_db(settings)), \
            patch.object(llm_route, 'get_effective_provider', return_value='openai-compatible'), \
            patch.object(llm_route, 'get_effective_base_url',
                          return_value='http://localhost:8000/v1'):
        route = resolve_route('detection')
    assert route.base_url == 'http://localhost:8000/v1'
    assert 'key' not in route.base_url and '@' not in route.base_url


class TestMigrationDefaults:
    """No stage-provider settings and no secondary config at all: every
    stage must resolve exactly like today's single-provider routes."""

    def test_no_settings_reproduces_single_provider_routes(self):
        settings = {'claude_model': 'claude-sonnet-5'}
        with patch.object(llm_route, 'Database', return_value=_db(settings)), \
                patch.object(llm_route, 'get_effective_provider', return_value='anthropic'):
            detection = resolve_route('detection')
            verification = resolve_route('verification')
            chapters = resolve_route('chapters')
            review = resolve_route('review', pass_provider=detection.provider_key,
                                    pass_model=detection.model_id)
        for route in (detection, verification, chapters, review):
            assert route.provider_key == 'anthropic'
            assert route.slot == 'primary'
            assert route.credential_slot == 'primary'
            assert route.model_id == 'claude-sonnet-5'
