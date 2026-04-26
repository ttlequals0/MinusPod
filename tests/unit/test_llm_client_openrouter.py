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


class _FakeResponse:
    def __init__(self, headers):
        self.headers = headers


class TestExtractRetryAfter(unittest.TestCase):
    """Verify extract_retry_after pulls the header off provider exceptions."""

    def test_returns_seconds_from_delta_header(self):
        from llm_client import extract_retry_after
        err = Exception("rate limit hit (429)")
        err.response = _FakeResponse({"Retry-After": "12"})
        self.assertEqual(extract_retry_after(err), 12.0)

    def test_lowercase_header_name_also_works(self):
        from llm_client import extract_retry_after
        err = Exception("rate limit (429)")
        err.response = _FakeResponse({"retry-after": "5"})
        self.assertEqual(extract_retry_after(err), 5.0)

    def test_returns_none_when_no_response(self):
        from llm_client import extract_retry_after
        err = Exception("rate limit (429)")
        self.assertIsNone(extract_retry_after(err))

    def test_returns_none_when_header_missing(self):
        from llm_client import extract_retry_after
        err = Exception("rate limit (429)")
        err.response = _FakeResponse({})
        self.assertIsNone(extract_retry_after(err))

    def test_clamps_to_max_seconds(self):
        from llm_client import extract_retry_after
        err = Exception("rate limit (429)")
        err.response = _FakeResponse({"Retry-After": "9999"})
        self.assertEqual(extract_retry_after(err, max_seconds=300.0), 300.0)


class TestCircuitBreakerSkipsOn429(unittest.TestCase):
    """A 429 from the provider must not trip the circuit breaker."""

    def _build_breaker(self):
        from utils.circuit_breaker import CircuitBreaker
        return CircuitBreaker("test-429", failure_threshold=2, recovery_timeout=60)

    def test_anthropic_messages_create_skips_failure_record(self):
        from llm_client import AnthropicClient
        client = AnthropicClient(api_key="test-key")
        breaker = self._build_breaker()
        client.set_circuit_breaker(breaker)
        # Bypass _ensure_client by injecting a mock that raises a rate-limit error.
        rate_err = Exception("rate limit exceeded (429)")
        mock = MagicMock()
        mock.messages.create.side_effect = rate_err
        client._client = mock

        for _ in range(5):
            with self.assertRaises(Exception):
                client.messages_create(
                    model="claude-test", max_tokens=10, system="", messages=[]
                )
        self.assertEqual(breaker._failure_count, 0)
        self.assertEqual(breaker.state, "closed")

    def test_openai_messages_create_skips_failure_record(self):
        from llm_client import OpenAICompatibleClient
        client = OpenAICompatibleClient(base_url="http://x/v1", api_key="k")
        breaker = self._build_breaker()
        client.set_circuit_breaker(breaker)
        rate_err = Exception("rate limit exceeded (429)")
        mock = MagicMock()
        mock.chat.completions.create.side_effect = rate_err
        client._client = mock
        # Pre-seed the token-param cache so we hit the cached-param branch.
        client._token_param_cache["any-model"] = "max_completion_tokens"

        for _ in range(5):
            with self.assertRaises(Exception):
                client.messages_create(
                    model="any-model", max_tokens=10, system="", messages=[]
                )
        self.assertEqual(breaker._failure_count, 0)
        self.assertEqual(breaker.state, "closed")

    def test_non_rate_limit_error_still_trips_breaker(self):
        from llm_client import AnthropicClient
        client = AnthropicClient(api_key="test-key")
        breaker = self._build_breaker()
        client.set_circuit_breaker(breaker)
        mock = MagicMock()
        mock.messages.create.side_effect = Exception("internal server error 500")
        client._client = mock
        with self.assertRaises(Exception):
            client.messages_create(
                model="claude-test", max_tokens=10, system="", messages=[]
            )
        self.assertEqual(breaker._failure_count, 1)


class TestAdDetectorLLMClientNotCached(unittest.TestCase):
    """Regression: AdDetector must read through to ``get_llm_client`` on every
    access. Caching the instance hides later provider switches until the
    worker restarts."""

    @patch('ad_detector.get_llm_client')
    @patch('ad_detector.get_api_key', return_value="key")
    def test_property_reads_through_to_get_llm_client_each_time(self, _key, mock_get):
        first, second = MagicMock(), MagicMock()
        mock_get.side_effect = [first, second]
        from ad_detector import AdDetector
        det = AdDetector(api_key="key")
        self.assertIs(det._llm_client, first)
        self.assertIs(det._llm_client, second)
        self.assertEqual(mock_get.call_count, 2)

    @patch('ad_detector.get_api_key', return_value=None)
    def test_returns_none_when_no_api_key(self, _key):
        from ad_detector import AdDetector
        det = AdDetector(api_key=None)
        self.assertIsNone(det._llm_client)



class TestGetLlmClientConfigInvalidation(unittest.TestCase):
    """Each gunicorn worker has its own ``_cached_client``. Only the worker
    that handles a settings PUT runs ``force_new``. Sibling workers must
    detect the config change at next call and rebuild themselves."""

    def setUp(self):
        import llm_client
        llm_client._cached_client = None
        llm_client._cached_client_config_key = None

    def tearDown(self):
        import llm_client
        llm_client._cached_client = None
        llm_client._cached_client_config_key = None

    @patch('llm_client._record_token_usage')
    @patch('llm_client.get_effective_openrouter_api_key', return_value='sk-or')
    @patch('llm_client.get_effective_provider', return_value='openrouter')
    def test_same_config_returns_cached_client(self, _prov, _key, _cb):
        from llm_client import get_llm_client
        first = get_llm_client()
        second = get_llm_client()
        self.assertIs(first, second)

    @patch('llm_client._record_token_usage')
    @patch('llm_client.get_effective_anthropic_api_key', return_value='sk-ant')
    @patch('llm_client.get_effective_openrouter_api_key', return_value='sk-or')
    def test_provider_change_forces_rebuild_without_force_new(
        self, _or_key, _ant_key, _cb
    ):
        import llm_client
        from llm_client import get_llm_client
        with patch('llm_client.get_effective_provider', return_value='openrouter'):
            first = get_llm_client()
        with patch('llm_client.get_effective_provider', return_value='anthropic'):
            second = get_llm_client()
        self.assertIsNot(first, second)
        self.assertEqual(llm_client._cached_client_config_key, 'anthropic')

    @patch('llm_client._record_token_usage')
    @patch('llm_client.get_effective_openai_api_key', return_value='sk-oai')
    @patch('llm_client.get_effective_provider', return_value='openai-compatible')
    def test_base_url_change_forces_rebuild_without_force_new(
        self, _prov, _key, _cb
    ):
        import llm_client
        from llm_client import get_llm_client
        with patch('llm_client.get_effective_base_url', return_value='http://a/v1'):
            first = get_llm_client()
        with patch('llm_client.get_effective_base_url', return_value='http://b/v1'):
            second = get_llm_client()
        self.assertIsNot(first, second)
        self.assertEqual(second.base_url, 'http://b/v1')
        self.assertEqual(llm_client._cached_client_config_key, 'openai-compatible:http://b/v1')

    @patch('llm_client.get_effective_provider', return_value='anthropic')
    def test_anthropic_config_key_is_stable(self, _prov):
        from llm_client import _current_config_key
        self.assertEqual(_current_config_key(), 'anthropic')


if __name__ == '__main__':
    unittest.main()
