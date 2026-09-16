"""Tests for the per-phase LLM route resolver (src/llm_route.py).

Stage settings (detection_provider, verification_provider,
chapters_provider, review_provider) store a SLOT (primary/secondary), not a
provider type.
"""
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap
bootstrap('llm_route_test_')

import llm_route
from config import ModelNotConfiguredError, coerce_bool_setting
from database import Database
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
    # pass model too: a leftover review_model override is not consulted.
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
    distinct base_url + credential_slot so each resolves its own secret."""
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


def test_resolved_same_as_pass_review_slot_follows_detection():
    """same_as_pass review runs on detection's slot, whatever verification uses."""
    settings = {
        'claude_model': 'claude-sonnet-5',
        'review_provider': 'same_as_pass',
        'verification_provider': 'secondary',
        'secondary_provider_enabled': 'true',
        'secondary_provider': 'openrouter',
    }

    assert llm_route.resolved_stage_slot(_db(settings), 'review') == 'primary'

    settings['detection_provider'] = 'secondary'
    assert llm_route.resolved_stage_slot(_db(settings), 'review') == 'secondary'


def test_resolved_explicit_review_slot_is_honored():
    settings = {
        'claude_model': 'claude-sonnet-5',
        'review_provider': 'primary',
        'verification_provider': 'secondary',
        'secondary_provider_enabled': 'true',
        'secondary_provider': 'openrouter',
    }

    assert llm_route.resolved_stage_slot(_db(settings), 'review') == 'primary'


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


class TestPopulatedDatabaseUpgrade:
    """A real, populated pre-secondary-provider database: existing settings
    and other data, no secondary config, no stage-provider slot settings.
    Reopening it (as an app restart on the new code would) must not lose
    data or rebuild any table, and every stage must resolve to primary,
    reproducing the single-provider routes the install already had."""

    def test_populated_db_upgrades_cleanly(self, temp_dir, monkeypatch):
        monkeypatch.delenv('LLM_PROVIDER', raising=False)

        previous_instance = Database._instance
        Database._instance = None
        db = Database(data_dir=temp_dir)
        try:
            # Settings a pre-existing single-provider install would have.
            db.set_setting('llm_provider', 'openai-compatible', is_default=False)
            db.set_setting('openai_base_url', 'https://llm.example.internal/v1',
                            is_default=False)
            db.set_setting('claude_model', 'gpt-4o-mini', is_default=False)
            conn = db.get_connection()
            conn.execute(
                "INSERT INTO podcasts (slug, source_url, title) VALUES (?, ?, ?)",
                ('example-podcast', 'https://example.com/feed.xml', 'Example Show'))
            conn.commit()
            podcast_count_before = conn.execute(
                "SELECT COUNT(*) AS c FROM podcasts").fetchone()['c']

            # Reopen the same database file: the existing-DB migration path
            # (_create_new_tables_only + _run_schema_migrations), not a
            # fresh-DB rebuild.
            Database._instance = None
            db2 = Database(data_dir=temp_dir)

            # No data loss, no destructive rebuild.
            assert db2.get_setting('llm_provider') == 'openai-compatible'
            assert db2.get_setting('openai_base_url') == 'https://llm.example.internal/v1'
            assert db2.get_setting('claude_model') == 'gpt-4o-mini'
            conn2 = db2.get_connection()
            assert conn2.execute(
                "SELECT COUNT(*) AS c FROM podcasts").fetchone()['c'] == podcast_count_before
            row = conn2.execute(
                "SELECT title FROM podcasts WHERE slug = ?", ('example-podcast',)).fetchone()
            assert row['title'] == 'Example Show'

            # No stage-provider slot settings and no secondary config exist.
            for key in ('detection_provider', 'verification_provider',
                        'chapters_provider', 'review_provider',
                        'secondary_provider', 'secondary_provider_base_url'):
                assert db2.get_setting(key) is None
            assert not coerce_bool_setting(db2.get_setting('secondary_provider_enabled'))

            # Every stage resolves to primary, matching the pre-existing
            # single-provider behavior.
            for stage in ('detection', 'verification', 'chapters', 'review'):
                assert llm_route.resolved_stage_slot(db2, stage) == 'primary'

            with patch.object(llm_route, 'Database', return_value=db2), \
                    patch.object(llm_route, 'get_effective_provider',
                                 return_value='openai-compatible'), \
                    patch.object(llm_route, 'get_effective_base_url',
                                 return_value='https://llm.example.internal/v1'):
                detection = resolve_route('detection')
                verification = resolve_route('verification')
                chapters = resolve_route('chapters')
                review = resolve_route(
                    'review', pass_provider=detection.provider_key,
                    pass_model=detection.model_id, pass_base_url=detection.base_url,
                    pass_credential_slot=detection.credential_slot)

            for route in (detection, verification, chapters, review):
                assert route.provider_key == 'openai-compatible'
                assert route.slot == 'primary'
                assert route.credential_slot == 'primary'
                assert route.base_url == 'https://llm.example.internal/v1'
            assert detection.model_id == 'gpt-4o-mini'
        finally:
            Database._instance = previous_instance


class TestClientForRoutePrecedence:
    """override -> resolved route's client -> fallback."""

    def test_override_wins_over_route_and_fallback(self):
        override = object()
        with patch.object(llm_route, 'route_for_phase',
                          return_value={'provider_key': 'openrouter'}), \
                patch.object(llm_route, 'get_client_for_provider') as get_client:
            result = llm_route.client_for_route(
                'detection', override=override,
                fallback=lambda: pytest.fail('fallback must not run'))
        assert result is override
        get_client.assert_not_called()

    def test_phase_name_resolves_run_snapshot_route(self):
        client = object()
        route = {'provider_key': 'openrouter', 'base_url': 'https://or/api/v1',
                 'credential_slot': 'secondary'}
        with patch.object(llm_route, 'route_for_phase', return_value=route) as lookup, \
                patch.object(llm_route, 'get_client_for_provider',
                             return_value=client) as get_client:
            result = llm_route.client_for_route(
                'chapters', fallback=lambda: pytest.fail('fallback must not run'))
        assert result is client
        lookup.assert_called_once_with('chapters')
        get_client.assert_called_once_with(
            'openrouter', base_url='https://or/api/v1', credential_slot='secondary')

    def test_dict_route_defaults_credential_slot_to_primary(self):
        with patch.object(llm_route, 'get_client_for_provider') as get_client:
            llm_route.client_for_route({'provider_key': 'anthropic'})
        get_client.assert_called_once_with(
            'anthropic', base_url=None, credential_slot='primary')

    def test_route_object_uses_its_own_fields(self):
        route = Route(phase='review', provider_key='ollama', model_id='m',
                      base_url='http://localhost:11434/v1', slot='primary',
                      credential_slot='primary')
        with patch.object(llm_route, 'get_client_for_provider') as get_client:
            llm_route.client_for_route(route)
        get_client.assert_called_once_with(
            'ollama', base_url='http://localhost:11434/v1', credential_slot='primary')

    def test_fallback_runs_only_when_no_route(self):
        fallback_client = object()
        with patch.object(llm_route, 'route_for_phase', return_value=None), \
                patch.object(llm_route, 'get_client_for_provider') as get_client:
            result = llm_route.client_for_route(
                'detection', fallback=lambda: fallback_client)
        assert result is fallback_client
        get_client.assert_not_called()

    def test_no_route_and_no_fallback_returns_none(self):
        with patch.object(llm_route, 'route_for_phase', return_value=None), \
                patch.object(llm_route, 'get_client_for_provider') as get_client:
            assert llm_route.client_for_route('detection') is None
        get_client.assert_not_called()
