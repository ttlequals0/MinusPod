"""Provider account identity: a frozen route must never pair its endpoint
with a key that now belongs to a different account (F01).
"""
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('provider_account_change_test_')

import llm_client
import llm_route
from llm_client import ProviderAccountChangedError
from llm_route import (
    Route, account_identity, account_identity_for_slot, client_for_route,
    current_account_identity, route_account_mismatch,
)


@pytest.fixture
def provider_settings():
    """Patch the settings llm_route reads for account identity, as a dict the
    test mutates to stand in for an operator save."""
    settings = {'llm_provider': 'openai-compatible',
                'openai_base_url': 'https://old.example/v1'}
    def read(key):
        return settings.get(key)

    with patch.object(llm_client, '_get_cached_setting', side_effect=read), \
            patch.object(llm_route, '_get_cached_setting', side_effect=read):
        yield settings


def _openai_route(base_url, slot='primary'):
    return {'provider_key': 'openai-compatible', 'configured_model': 'm',
            'base_url': base_url, 'credential_slot': slot,
            'account_id': account_identity('openai-compatible', base_url)}


class TestIdentityTracksAccountNotKey:
    def test_endpoint_change_moves_the_identity(self, provider_settings):
        before = account_identity_for_slot('primary')
        provider_settings['openai_base_url'] = 'https://new.example/v1'
        assert account_identity_for_slot('primary') != before

    def test_key_rotation_leaves_the_identity_alone(self, provider_settings):
        before = account_identity_for_slot('primary')
        # A rotation writes only the secret; nothing identity reads changes.
        assert account_identity_for_slot('primary') == before

    def test_secondary_provider_type_change_moves_the_identity(self, provider_settings):
        provider_settings['secondary_provider'] = 'openrouter'
        before = account_identity_for_slot('secondary')
        provider_settings['secondary_provider'] = 'anthropic'
        assert account_identity_for_slot('secondary') != before

    def test_secondary_identity_is_none_without_a_provider_type(self, provider_settings):
        assert account_identity_for_slot('secondary') is None

    def test_primary_identity_follows_the_route_provider_type(self, provider_settings):
        # llm_provider is openai-compatible, but a primary anthropic route
        # authenticates with anthropic_api_key, so its account is anthropic's.
        assert (current_account_identity('anthropic', 'primary')
                == account_identity('anthropic', None))


class TestClientForRouteRefusesAChangedAccount:
    def test_new_key_is_not_sent_to_the_frozen_endpoint(self, provider_settings):
        route = _openai_route('https://old.example/v1')
        provider_settings['openai_base_url'] = 'https://new.example/v1'
        with patch.object(llm_route, 'get_client_for_provider') as get_client:
            with pytest.raises(ProviderAccountChangedError) as excinfo:
                client_for_route(route)
        get_client.assert_not_called()
        assert excinfo.value.credential_slot == 'primary'
        assert excinfo.value.phase is None

    def test_same_account_still_builds_a_client(self, provider_settings):
        route = _openai_route('https://old.example/v1')
        client = object()
        with patch.object(llm_route, 'get_client_for_provider',
                          return_value=client) as get_client:
            assert client_for_route(route) is client
        get_client.assert_called_once_with(
            'openai-compatible', base_url='https://old.example/v1',
            credential_slot='primary')

    def test_key_removal_is_not_an_account_change(self, provider_settings):
        route = _openai_route('https://old.example/v1')
        # Clearing a key leaves the endpoint (and so the account) in place;
        # the auth failure belongs at call time, not here.
        with patch.object(llm_route, 'get_client_for_provider') as get_client:
            client_for_route(route)
        get_client.assert_called_once()

    def test_route_without_an_account_id_is_not_checked(self, provider_settings):
        provider_settings['openai_base_url'] = 'https://new.example/v1'
        with patch.object(llm_route, 'get_client_for_provider') as get_client:
            client_for_route({'provider_key': 'openai-compatible',
                              'base_url': 'https://old.example/v1'})
        get_client.assert_called_once()

    def test_route_object_carries_its_phase_onto_the_error(self, provider_settings):
        route = Route(phase='chapters', provider_key='openai-compatible',
                      model_id='m', base_url='https://old.example/v1',
                      slot='primary', credential_slot='primary',
                      account_id=account_identity('openai-compatible',
                                                  'https://old.example/v1'))
        provider_settings['openai_base_url'] = 'https://new.example/v1'
        with pytest.raises(ProviderAccountChangedError) as excinfo:
            client_for_route(route)
        assert excinfo.value.phase == 'chapters'

    def test_secondary_route_refused_after_a_type_switch(self, provider_settings):
        provider_settings['secondary_provider'] = 'openrouter'
        route = {'provider_key': 'openrouter', 'base_url': None,
                 'credential_slot': 'secondary',
                 'account_id': account_identity_for_slot('secondary')}
        provider_settings['secondary_provider'] = 'anthropic'
        with patch.object(llm_route, 'get_client_for_provider') as get_client:
            with pytest.raises(ProviderAccountChangedError):
                client_for_route(route)
        get_client.assert_not_called()


class TestCrossWorkerCacheRefresh:
    def test_identity_follows_an_invalidated_cache(self, temp_db):
        """Another worker's save is picked up through the same TTL cache the
        credential is read from."""
        temp_db.set_setting('llm_provider', 'openai-compatible')
        temp_db.set_setting('openai_base_url', 'https://old.example/v1')
        llm_client.invalidate_provider_cache()
        before = account_identity_for_slot('primary')
        temp_db.set_setting('openai_base_url', 'https://new.example/v1')
        llm_client.invalidate_provider_cache()
        assert account_identity_for_slot('primary') != before


class TestRouteMismatchReporting:
    def test_reports_frozen_and_current_ids(self, provider_settings):
        route = _openai_route('https://old.example/v1')
        provider_settings['openai_base_url'] = 'https://new.example/v1'
        frozen, current = route_account_mismatch(route)
        assert frozen == route['account_id']
        assert current == account_identity('openai-compatible',
                                           'https://new.example/v1')

    def test_no_mismatch_returns_none(self, provider_settings):
        assert route_account_mismatch(_openai_route('https://old.example/v1')) is None


class TestSnapshotGuard:
    """A recovered run reloads its persisted routes, so the guard must catch a
    change that landed while it was not running."""

    def _processing(self):
        import main_app.processing as processing
        return processing

    def test_recovered_snapshot_with_a_changed_account_raises(self, provider_settings):
        processing = self._processing()
        snapshot = {'detection': _openai_route('https://old.example/v1')}
        provider_settings['openai_base_url'] = 'https://new.example/v1'
        with pytest.raises(ProviderAccountChangedError):
            processing._assert_route_snapshot_current(snapshot)

    def test_unchanged_snapshot_passes(self, provider_settings):
        processing = self._processing()
        snapshot = {'detection': _openai_route('https://old.example/v1')}
        processing._assert_route_snapshot_current(snapshot)

    def test_no_snapshot_passes(self, provider_settings):
        self._processing()._assert_route_snapshot_current(None)


class TestFailureHandlerRequeues:
    def test_account_change_requeues_and_drops_the_frozen_routes(self, provider_settings):
        import run_context
        import main_app.processing as processing

        fake_db = MagicMock()
        ctx = run_context.begin('example-podcast', 'a1b2c3d4e5f6', run_id='run-1')
        ctx.set_route_snapshot({'detection': _openai_route('https://old.example/v1')})
        provider_settings['openai_base_url'] = 'https://new.example/v1'
        try:
            with patch.object(processing, 'db', fake_db), \
                    patch.object(processing, '_publish_status'), \
                    patch.object(processing, '_require_publication_owner'), \
                    patch.object(processing, 'clear_route_snapshot') as clear, \
                    patch.object(processing, '_requeue_episode_after_hold') as requeue:
                processing._handle_processing_failure(
                    'example-podcast', 'a1b2c3d4e5f6', 'An episode', 'A Show',
                    {}, RuntimeError('detection failed'), 0.0)
        finally:
            run_context.end(ctx)
        clear.assert_called_once_with('run-1')
        assert requeue.call_count == 1
        message = requeue.call_args.kwargs['message']
        assert message.startswith('Requeued (provider_account_changed)')
