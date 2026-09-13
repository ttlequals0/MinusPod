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
        self.assertIs(llm_client._client_cache.get(('anthropic', None)), client)


if __name__ == '__main__':
    unittest.main()
