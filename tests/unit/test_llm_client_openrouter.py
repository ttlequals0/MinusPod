"""Tests for OpenRouter provider integration in llm_client."""
import unittest
from unittest.mock import patch, MagicMock


class TestGetEffectiveOpenrouterApiKey(unittest.TestCase):
    """Verify DB-first, env-fallback logic for OpenRouter API key."""

    @patch('llm_client._get_cached_secret', return_value='sk-or-db-key')
    def test_returns_db_value_when_set(self, _mock):
        from llm_client import get_effective_openrouter_api_key
        self.assertEqual(get_effective_openrouter_api_key(), 'sk-or-db-key')

    @patch('llm_client._get_cached_secret', return_value=None)
    @patch.dict('os.environ', {'OPENROUTER_API_KEY': 'sk-or-env-key'})
    def test_falls_back_to_env_var(self, _mock):
        from llm_client import get_effective_openrouter_api_key
        self.assertEqual(get_effective_openrouter_api_key(), 'sk-or-env-key')

    @patch('llm_client._get_cached_secret', return_value=None)
    @patch.dict('os.environ', {}, clear=True)
    def test_returns_none_when_unset(self, _mock):
        from llm_client import get_effective_openrouter_api_key
        self.assertIsNone(get_effective_openrouter_api_key())

    @patch('llm_client._get_cached_secret', return_value='sk-or-db-key')
    @patch.dict('os.environ', {'OPENROUTER_API_KEY': 'sk-or-env-key'})
    def test_db_takes_precedence_over_env(self, _mock):
        from llm_client import get_effective_openrouter_api_key
        self.assertEqual(get_effective_openrouter_api_key(), 'sk-or-db-key')


class TestGetEffectiveOllamaApiKey(unittest.TestCase):
    """Verify DB-first, env-fallback logic for Ollama API key."""

    @patch('llm_client._get_cached_secret', return_value='ollama-db-key')
    def test_returns_db_value_when_set(self, _mock):
        from llm_client import get_effective_ollama_api_key
        self.assertEqual(get_effective_ollama_api_key(), 'ollama-db-key')

    @patch('llm_client._get_cached_secret', return_value=None)
    @patch.dict('os.environ', {'OLLAMA_API_KEY': 'ollama-env-key'})
    def test_falls_back_to_env_var(self, _mock):
        from llm_client import get_effective_ollama_api_key
        self.assertEqual(get_effective_ollama_api_key(), 'ollama-env-key')

    @patch('llm_client._get_cached_secret', return_value=None)
    @patch.dict('os.environ', {}, clear=True)
    def test_returns_none_when_unset(self, _mock):
        from llm_client import get_effective_ollama_api_key
        self.assertIsNone(get_effective_ollama_api_key())


class TestGetLlmTimeoutOpenRouter(unittest.TestCase):
    """Verify OpenRouter gets the fast cloud timeout, not the local timeout."""

    @patch('llm_client.get_effective_provider', return_value='openrouter')
    def test_openrouter_gets_default_timeout(self, _mock):
        from llm_client import get_llm_timeout
        from config import LLM_TIMEOUT_DEFAULT
        self.assertEqual(get_llm_timeout(), LLM_TIMEOUT_DEFAULT)

    @patch('llm_client.get_effective_provider', return_value='ollama')
    def test_ollama_gets_local_timeout(self, _mock):
        from llm_client import get_llm_timeout
        from config import LLM_TIMEOUT_LOCAL
        self.assertEqual(get_llm_timeout(), LLM_TIMEOUT_LOCAL)


class TestGetLlmMaxRetriesOpenRouter(unittest.TestCase):
    """Verify OpenRouter gets the cloud retry count, not the local one."""

    @patch('llm_client.get_effective_provider', return_value='openrouter')
    def test_openrouter_gets_default_retries(self, _mock):
        from llm_client import get_llm_max_retries
        from config import LLM_RETRY_MAX_RETRIES
        self.assertEqual(get_llm_max_retries(), LLM_RETRY_MAX_RETRIES)

    @patch('llm_client.get_effective_provider', return_value='ollama')
    def test_ollama_gets_local_retries(self, _mock):
        from llm_client import get_llm_max_retries
        from config import LLM_RETRY_MAX_RETRIES_LOCAL
        self.assertEqual(get_llm_max_retries(), LLM_RETRY_MAX_RETRIES_LOCAL)


class TestGetApiKeyOpenRouter(unittest.TestCase):
    """Verify get_api_key returns the OpenRouter key for the openrouter provider."""

    @patch('llm_client.get_effective_openrouter_api_key', return_value='sk-or-key')
    @patch('llm_client.get_effective_provider', return_value='openrouter')
    def test_returns_openrouter_key(self, _prov, _key):
        from llm_client import get_api_key
        self.assertEqual(get_api_key(), 'sk-or-key')


class TestGetLlmClientOpenRouter(unittest.TestCase):
    """Verify get_llm_client creates an OpenAICompatibleClient for openrouter."""

    @patch('llm_client._record_token_usage')
    @patch('llm_client.get_effective_openrouter_api_key', return_value='sk-or-test')
    @patch('llm_client.get_effective_provider', return_value='openrouter')
    def test_creates_openai_compatible_client_with_extra_headers(self, _prov, _key, _cb):
        import llm_client
        from llm_client import get_llm_client, OpenAICompatibleClient
        from config import OPENROUTER_BASE_URL, OPENROUTER_HTTP_REFERER, OPENROUTER_APP_TITLE

        # Clear cached client
        llm_client._cached_client = None

        client = get_llm_client(force_new=True)

        self.assertIsInstance(client, OpenAICompatibleClient)
        self.assertEqual(client.base_url, OPENROUTER_BASE_URL)
        self.assertEqual(client.api_key, 'sk-or-test')
        self.assertEqual(client.extra_headers['HTTP-Referer'], OPENROUTER_HTTP_REFERER)
        self.assertEqual(client.extra_headers['X-Title'], OPENROUTER_APP_TITLE)

        # Clean up
        llm_client._cached_client = None

    @patch('llm_client._record_token_usage')
    @patch('llm_client.get_effective_openrouter_api_key', return_value=None)
    @patch('llm_client.get_effective_provider', return_value='openrouter')
    def test_falls_back_to_not_needed_when_no_key(self, _prov, _key, _cb):
        import llm_client
        from llm_client import get_llm_client

        llm_client._cached_client = None
        client = get_llm_client(force_new=True)

        self.assertEqual(client.api_key, 'not-needed')

        llm_client._cached_client = None


class TestModelMatchesProviderOpenRouter(unittest.TestCase):
    """Verify OpenRouter accepts any model, while Anthropic still filters."""

    def test_openrouter_accepts_claude_model(self):
        from llm_client import model_matches_provider, PROVIDER_OPENROUTER
        self.assertTrue(model_matches_provider('anthropic/claude-sonnet-4-5', PROVIDER_OPENROUTER))

    def test_openrouter_accepts_non_claude_model(self):
        from llm_client import model_matches_provider, PROVIDER_OPENROUTER
        self.assertTrue(model_matches_provider('openai/gpt-4o', PROVIDER_OPENROUTER))

    def test_anthropic_rejects_non_claude_model(self):
        from llm_client import model_matches_provider, PROVIDER_ANTHROPIC
        self.assertFalse(model_matches_provider('openai/gpt-4o', PROVIDER_ANTHROPIC))

    def test_anthropic_accepts_claude_model(self):
        from llm_client import model_matches_provider, PROVIDER_ANTHROPIC
        self.assertTrue(model_matches_provider('claude-sonnet-4-5-20250514', PROVIDER_ANTHROPIC))


class TestVerifyLlmConnectionOpenRouter(unittest.TestCase):
    """Verify that OpenRouter startup verification calls verify_connection."""

    @patch('llm_client.get_llm_client')
    @patch('llm_client.get_effective_openrouter_api_key', return_value='sk-or-key')
    @patch('llm_client.get_effective_provider', return_value='openrouter')
    def test_calls_verify_connection_when_key_set(self, _prov, _key, mock_get_client):
        mock_client = MagicMock()
        mock_client.verify_connection.return_value = True
        mock_get_client.return_value = mock_client

        from llm_client import verify_llm_connection
        result = verify_llm_connection()

        self.assertTrue(result)
        mock_client.verify_connection.assert_called_once_with(timeout=10.0)

    @patch('llm_client.get_effective_openrouter_api_key', return_value=None)
    @patch('llm_client.get_effective_provider', return_value='openrouter')
    def test_returns_false_when_no_key(self, _prov, _key):
        from llm_client import verify_llm_connection
        self.assertFalse(verify_llm_connection())

    @patch('llm_client.get_llm_client')
    @patch('llm_client.get_effective_openrouter_api_key', return_value='sk-or-key')
    @patch('llm_client.get_effective_provider', return_value='openrouter')
    def test_returns_false_on_connection_failure(self, _prov, _key, mock_get_client):
        mock_client = MagicMock()
        mock_client.verify_connection.return_value = False
        mock_get_client.return_value = mock_client

        from llm_client import verify_llm_connection
        result = verify_llm_connection()

        self.assertFalse(result)

    @patch('llm_client.get_llm_client', side_effect=Exception('connection refused'))
    @patch('llm_client.get_effective_openrouter_api_key', return_value='sk-or-key')
    @patch('llm_client.get_effective_provider', return_value='openrouter')
    def test_returns_false_on_exception(self, _prov, _key, _client):
        from llm_client import verify_llm_connection
        self.assertFalse(verify_llm_connection())


if __name__ == '__main__':
    unittest.main()
