"""Tests for the per-provider LLM client cache and circuit breaker (llm_client.py)."""
import unittest
from unittest.mock import patch

from tests.app_bootstrap import bootstrap
bootstrap('route_client_cache_test_')

import llm_client
from llm_client import get_client_for_provider, get_llm_client, OpenAICompatibleClient


class TestGetClientForProviderCache(unittest.TestCase):
    def setUp(self):
        llm_client._client_cache.clear()
        llm_client._circuit_breakers.clear()

    tearDown = setUp

    @patch('llm_client._record_token_usage')
    @patch('llm_client.get_effective_openrouter_api_key', return_value='sk-or')
    @patch('llm_client.get_effective_openai_api_key', return_value='sk-oai')
    @patch('llm_client.get_effective_base_url', return_value='http://a/v1')
    def test_two_providers_yield_two_distinct_cached_clients(self, *_mocks):
        openrouter_client = get_client_for_provider('openrouter')
        openai_client = get_client_for_provider('openai-compatible')

        self.assertIsNot(openrouter_client, openai_client)
        self.assertIsInstance(openrouter_client, OpenAICompatibleClient)
        self.assertIsInstance(openai_client, OpenAICompatibleClient)

    @patch('llm_client._record_token_usage')
    @patch('llm_client.get_effective_openrouter_api_key', return_value='sk-or')
    def test_same_provider_and_base_returns_cached_instance(self, *_mocks):
        first = get_client_for_provider('openrouter')
        second = get_client_for_provider('openrouter')
        self.assertIs(first, second)

    @patch('llm_client._record_token_usage')
    @patch('llm_client.get_effective_openai_api_key', return_value='sk-oai')
    def test_different_base_for_same_provider_yields_distinct_clients(self, *_mocks):
        first = get_client_for_provider('openai-compatible', base_url='http://a/v1')
        second = get_client_for_provider('openai-compatible', base_url='http://b/v1')
        self.assertIsNot(first, second)
        self.assertEqual(first.base_url, 'http://a/v1')
        self.assertEqual(second.base_url, 'http://b/v1')

    @patch('llm_client._record_token_usage')
    @patch('llm_client.get_effective_openai_api_key', return_value='sk-oai')
    def test_different_base_for_same_provider_shares_one_circuit_breaker(self, *_mocks):
        first = get_client_for_provider('openai-compatible', base_url='http://a/v1')
        second = get_client_for_provider('openai-compatible', base_url='http://b/v1')
        self.assertIsNot(first, second)
        self.assertIs(first._circuit_breaker, second._circuit_breaker)

    @patch('llm_client._record_token_usage')
    @patch('llm_client.get_effective_openrouter_api_key', return_value='sk-or')
    @patch('llm_client.get_effective_openai_api_key', return_value='sk-oai')
    @patch('llm_client.get_effective_base_url', return_value='http://a/v1')
    def test_each_client_gets_distinct_circuit_breaker(self, *_mocks):
        openrouter_client = get_client_for_provider('openrouter')
        openai_client = get_client_for_provider('openai-compatible')

        self.assertIsNotNone(openrouter_client._circuit_breaker)
        self.assertIsNotNone(openai_client._circuit_breaker)
        self.assertIsNot(openrouter_client._circuit_breaker, openai_client._circuit_breaker)

    @patch('llm_client._record_token_usage')
    @patch('llm_client.get_effective_openrouter_api_key', return_value='sk-or')
    @patch('llm_client.get_effective_openai_api_key', return_value='sk-oai')
    @patch('llm_client.get_effective_base_url', return_value='http://a/v1')
    def test_tripping_one_providers_breaker_does_not_open_the_other(self, *_mocks):
        openrouter_client = get_client_for_provider('openrouter')
        openai_client = get_client_for_provider('openai-compatible')

        for _ in range(5):
            openrouter_client._circuit_breaker.record_failure(Exception('boom'))

        self.assertEqual(openrouter_client._circuit_breaker.state, 'open')
        self.assertEqual(openai_client._circuit_breaker.state, 'closed')

    @patch('llm_client._record_token_usage')
    @patch('llm_client.get_effective_openrouter_api_key', return_value='super-secret-key')
    @patch('llm_client.get_effective_openai_api_key', return_value='another-secret')
    @patch('llm_client.get_effective_base_url', return_value='http://a/v1')
    def test_cache_key_never_contains_api_key(self, *_mocks):
        get_client_for_provider('openrouter')
        get_client_for_provider('openai-compatible')

        for key in llm_client._client_cache.keys():
            for part in key:
                self.assertNotIn('super-secret-key', str(part))
                self.assertNotIn('another-secret', str(part))
        for key in llm_client._circuit_breakers.keys():
            self.assertNotIn('super-secret-key', str(key))
            self.assertNotIn('another-secret', str(key))

    @patch('llm_client._record_token_usage')
    @patch('llm_client.get_effective_anthropic_api_key', return_value='sk-ant')
    @patch('llm_client.get_effective_provider', return_value='anthropic')
    def test_get_llm_client_delegates_to_per_provider_cache(self, *_mocks):
        client = get_llm_client()
        self.assertIs(llm_client._client_cache.get(('anthropic', None, 'primary')), client)


class TestCredentialSlotRouting(unittest.TestCase):
    """A secondary slot resolves its API key from secondary_provider_api_key,
    even when it shares a provider type with primary."""

    def setUp(self):
        llm_client._client_cache.clear()
        llm_client._circuit_breakers.clear()

    tearDown = setUp

    @patch('llm_client._record_token_usage')
    @patch('llm_client.get_effective_secondary_provider_api_key', return_value='sk-secondary')
    @patch('llm_client.get_effective_openai_api_key', return_value='sk-primary')
    def test_secondary_slot_same_type_different_base_gets_secondary_key(self, *_mocks):
        primary = get_client_for_provider('openai-compatible', base_url='http://primary-host/v1')
        secondary = get_client_for_provider(
            'openai-compatible', base_url='http://secondary-host/v1',
            credential_slot='secondary')

        self.assertIsNot(primary, secondary)
        self.assertEqual(primary.api_key, 'sk-primary')
        self.assertEqual(secondary.api_key, 'sk-secondary')
        self.assertIsNot(primary._circuit_breaker, secondary._circuit_breaker)

    @patch('llm_client._record_token_usage')
    @patch('llm_client.get_effective_secondary_provider_api_key', return_value='sk-secondary-ant')
    @patch('llm_client.get_effective_anthropic_api_key', return_value='sk-primary-ant')
    def test_anthropic_primary_and_secondary_are_distinct_clients_and_breakers(self, *_mocks):
        """Regression: anthropic has no per-slot base_url, so without
        credential_slot in the cache key/breaker key, a secondary anthropic
        account would silently collide with primary's cached client."""
        primary = get_client_for_provider('anthropic')
        secondary = get_client_for_provider('anthropic', credential_slot='secondary')

        self.assertIsNot(primary, secondary)
        self.assertEqual(primary.api_key, 'sk-primary-ant')
        self.assertEqual(secondary.api_key, 'sk-secondary-ant')
        self.assertIsNot(primary._circuit_breaker, secondary._circuit_breaker)

    @patch('llm_client._record_token_usage')
    @patch('llm_client.get_effective_secondary_provider_api_key', return_value='sk-secondary-or')
    @patch('llm_client.get_effective_openrouter_api_key', return_value='sk-primary-or')
    def test_openrouter_primary_and_secondary_are_distinct_clients_and_breakers(self, *_mocks):
        """Regression: openrouter resolves the same base_url for both slots,
        so without credential_slot in the cache key/breaker key, a secondary
        openrouter account would silently collide with primary's."""
        primary = get_client_for_provider('openrouter')
        secondary = get_client_for_provider('openrouter', credential_slot='secondary')

        self.assertIsNot(primary, secondary)
        self.assertEqual(primary.api_key, 'sk-primary-or')
        self.assertEqual(secondary.api_key, 'sk-secondary-or')
        self.assertIsNot(primary._circuit_breaker, secondary._circuit_breaker)

    @patch('llm_client._record_token_usage')
    @patch('llm_client.get_effective_secondary_provider_api_key', return_value='sk-secondary')
    @patch('llm_client.get_effective_openai_api_key', return_value='sk-primary')
    def test_default_credential_slot_is_primary_and_unchanged(self, *_mocks):
        client = get_client_for_provider('openai-compatible', base_url='http://a/v1')
        self.assertEqual(client.api_key, 'sk-primary')

    @patch('llm_client._record_token_usage')
    @patch('llm_client.get_effective_secondary_provider_api_key', return_value=None)
    @patch('llm_client.get_effective_anthropic_api_key', return_value='sk-primary-anthropic')
    def test_secondary_anthropic_with_no_key_does_not_fall_back_to_primary(self, *_mocks):
        client = get_client_for_provider('anthropic', credential_slot='secondary')
        self.assertIsNone(client.api_key)

    @patch('llm_client._record_token_usage')
    @patch('llm_client.get_effective_secondary_provider_api_key', return_value='top-secret-secondary-key')
    @patch('llm_client.get_effective_openai_api_key', return_value='sk-primary')
    def test_cache_key_never_contains_secondary_api_key(self, *_mocks):
        get_client_for_provider('openai-compatible', base_url='http://a/v1')
        get_client_for_provider('openai-compatible', base_url='http://b/v1',
                                credential_slot='secondary')

        for key in llm_client._client_cache.keys():
            for part in key:
                self.assertNotIn('top-secret-secondary-key', str(part))
        for key in llm_client._circuit_breakers.keys():
            self.assertNotIn('top-secret-secondary-key', str(key))

    @patch('llm_client._record_token_usage')
    @patch('llm_client.get_effective_secondary_provider_api_key', return_value='top-secret-anthropic-secondary')
    @patch('llm_client.get_effective_anthropic_api_key', return_value='top-secret-anthropic-primary')
    def test_anthropic_cache_and_breaker_keys_never_contain_api_keys(self, *_mocks):
        get_client_for_provider('anthropic')
        get_client_for_provider('anthropic', credential_slot='secondary')

        for key in llm_client._client_cache.keys():
            for part in key:
                self.assertNotIn('top-secret-anthropic-primary', str(part))
                self.assertNotIn('top-secret-anthropic-secondary', str(part))
        for key in llm_client._circuit_breakers.keys():
            self.assertNotIn('top-secret-anthropic-primary', str(key))
            self.assertNotIn('top-secret-anthropic-secondary', str(key))


if __name__ == '__main__':
    unittest.main()
