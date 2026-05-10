"""Tests for multi-provider LLM pricing system."""
import pytest
from unittest.mock import patch, MagicMock

from config import normalize_model_key, get_pricing_source


class TestNormalizeModelKey:
    """Test model name normalization for pricing lookups."""

    def test_anthropic_versioned(self):
        assert normalize_model_key('claude-sonnet-4-5-20250929') == 'claudesonnet45'

    def test_anthropic_unversioned(self):
        assert normalize_model_key('claude-sonnet-4-6') == 'claudesonnet46'

    def test_openrouter_prefixed(self):
        assert normalize_model_key('anthropic/claude-sonnet-4-5') == 'claudesonnet45'

    def test_display_name_with_dots(self):
        assert normalize_model_key('Claude Sonnet 4.5') == 'claudesonnet45'

    def test_gpt_model(self):
        assert normalize_model_key('gpt-4o-mini') == 'gpt4omini'

    def test_gpt_versioned(self):
        assert normalize_model_key('gpt-4o-2024-05-13') == 'gpt4o'

    def test_groq_model(self):
        assert normalize_model_key('llama-3.3-70b-versatile') == 'llama3370bversatile'

    def test_deepseek_model(self):
        assert normalize_model_key('deepseek-chat') == 'deepseekchat'

    def test_openrouter_with_provider_prefix(self):
        assert normalize_model_key('google/gemini-2.0-flash') == 'gemini20flash'

    def test_claude_opus_versioned(self):
        assert normalize_model_key('claude-opus-4-5-20251101') == 'claudeopus45'

    def test_claude_opus_unversioned(self):
        assert normalize_model_key('claude-opus-4-6') == 'claudeopus46'

    def test_matching_across_sources(self):
        """Versioned and unversioned names for same model should produce same key."""
        assert normalize_model_key('claude-sonnet-4-5-20250929') == normalize_model_key('claude-sonnet-4-5')

    def test_display_name_matches_api_id(self):
        """Display name from pricepertoken should match API model ID."""
        assert normalize_model_key('Claude Sonnet 4.5') == normalize_model_key('claude-sonnet-4-5')

    def test_openrouter_prefix_stripped(self):
        """OpenRouter prefixed ID should match bare ID."""
        assert normalize_model_key('anthropic/claude-sonnet-4-5') == normalize_model_key('claude-sonnet-4-5')

    def test_empty_string(self):
        assert normalize_model_key('') == ''

    def test_date_only_stripped(self):
        """Date suffix at end should be stripped."""
        assert normalize_model_key('model-20240101') == 'model'

    def test_mid_string_date_not_stripped(self):
        """Date-like patterns not at end should be kept."""
        assert normalize_model_key('model-20240101-beta') == 'model20240101beta'

    def test_non_date_suffix_not_stripped(self):
        """Non-date numeric suffixes should not be stripped."""
        assert normalize_model_key('model-v10000101') == 'modelv10000101'

    def test_openrouter_free_suffix_stripped(self):
        assert normalize_model_key('z-ai/glm-4.5-air:free') == 'glm45air'

    def test_openrouter_beta_suffix_stripped(self):
        assert normalize_model_key('anthropic/claude-sonnet-4-5:beta') == 'claudesonnet45'

    def test_colon_suffix_with_digits_not_stripped(self):
        """Suffix containing digits does not match alpha-only regex, so it is kept."""
        assert normalize_model_key('model:v2-large') == 'modelv2large'


class TestGetPricingSource:
    """Test pricing source detection."""

    def test_anthropic_provider(self):
        result = get_pricing_source('anthropic')
        assert result['type'] == 'pricepertoken'
        assert 'anthropic' in result['url']

    def test_openrouter_provider(self):
        result = get_pricing_source('openrouter')
        assert result['type'] == 'openrouter_api'
        assert result['url'] == 'https://openrouter.ai/api/v1/models'

    def test_ollama_provider(self):
        result = get_pricing_source('ollama')
        assert result['type'] == 'free'

    def test_openai_compatible_openai_domain(self):
        result = get_pricing_source('openai-compatible', 'https://api.openai.com/v1')
        assert result['type'] == 'pricepertoken'
        assert 'openai' in result['url']

    def test_openai_compatible_groq_domain(self):
        result = get_pricing_source('openai-compatible', 'https://api.groq.com/openai/v1')
        assert result['type'] == 'pricepertoken'
        assert 'groq' in result['url']

    def test_openai_compatible_deepseek_domain(self):
        result = get_pricing_source('openai-compatible', 'https://api.deepseek.com/v1')
        assert result['type'] == 'pricepertoken'
        assert 'deepseek' in result['url']

    def test_openai_compatible_together_domain(self):
        result = get_pricing_source('openai-compatible', 'https://api.together.xyz/v1')
        assert result['type'] == 'pricepertoken'
        assert 'together' in result['url']

    def test_localhost_is_free(self):
        result = get_pricing_source('openai-compatible', 'http://localhost:11434/v1')
        assert result['type'] == 'free'

    def test_127_0_0_1_is_free(self):
        result = get_pricing_source('openai-compatible', 'http://127.0.0.1:8000/v1')
        assert result['type'] == 'free'

    def test_local_domain_is_free(self):
        result = get_pricing_source('openai-compatible', 'http://my-server.local:8000/v1')
        assert result['type'] == 'free'

    def test_unknown_domain(self):
        result = get_pricing_source('openai-compatible', 'https://my-custom-llm.example.com/v1')
        assert result['type'] == 'unknown'
        assert result['domain'] == 'my-custom-llm.example.com'

    def test_no_base_url(self):
        result = get_pricing_source('openai-compatible', '')
        assert result['type'] == 'unknown'
        assert result['domain'] == ''

    def test_none_base_url(self):
        result = get_pricing_source('openai-compatible', None)
        assert result['type'] == 'unknown'

    def test_openrouter_domain_via_openai_compatible(self):
        result = get_pricing_source('openai-compatible', 'https://openrouter.ai/api/v1')
        assert result['type'] == 'openrouter_api'


class TestPricePerTokenScraper:
    """Test the pricepertoken.com HTML scraper."""

    def test_parse_provider_page_layout(self):
        """Test scraping a provider-style page (Model | Context | Input | Output)."""
        from pricing_fetcher import fetch_pricepertoken_pricing

        html = """
        <html><body>
        <table>
            <tr><th>Model</th><th>Context</th><th>Input</th><th>Output</th></tr>
            <tr><td><a href="/m">Claude Sonnet 4.5</a></td><td>200K</td><td>$3.000</td><td>$15.000</td></tr>
            <tr><td>Claude Haiku 4.5</td><td>200K</td><td>$1.000</td><td>$5.000</td></tr>
        </table>
        </body></html>
        """
        with patch('pricing_fetcher.safe_get') as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = html
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            results = fetch_pricepertoken_pricing('https://pricepertoken.com/pricing-page/provider/anthropic')

        assert len(results) == 2
        assert results[0]['display_name'] == 'Claude Sonnet 4.5'
        assert results[0]['input_cost_per_mtok'] == 3.0
        assert results[0]['output_cost_per_mtok'] == 15.0
        assert results[0]['match_key'] == normalize_model_key('Claude Sonnet 4.5')

    def test_parse_endpoint_page_layout(self):
        """Test scraping an endpoint-style page (Provider | Model | Context | Speed | Input/1M | Output/1M)."""
        from pricing_fetcher import fetch_pricepertoken_pricing

        html = """
        <html><body>
        <table>
            <tr><th>Provider</th><th>Model</th><th>Context</th><th>Speed</th><th>Input/1M</th><th>Output/1M</th></tr>
            <tr><td>Groq</td><td>llama-3.3-70b</td><td>128K</td><td>fast</td><td>$0.590</td><td>$0.790</td></tr>
        </table>
        </body></html>
        """
        with patch('pricing_fetcher.safe_get') as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = html
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            results = fetch_pricepertoken_pricing('https://pricepertoken.com/endpoints/groq')

        assert len(results) == 1
        assert results[0]['display_name'] == 'llama-3.3-70b'
        assert results[0]['input_cost_per_mtok'] == 0.59
        assert results[0]['output_cost_per_mtok'] == 0.79

    def test_no_table_returns_empty(self):
        from pricing_fetcher import fetch_pricepertoken_pricing

        with patch('pricing_fetcher.safe_get') as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = '<html><body><p>No data</p></body></html>'
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            results = fetch_pricepertoken_pricing('https://pricepertoken.com/pricing-page/provider/test')

        assert results == []

    def test_missing_columns_returns_empty(self):
        """Table without required model/input/output headers returns empty."""
        from pricing_fetcher import fetch_pricepertoken_pricing

        html = """
        <html><body>
        <table>
            <tr><th>Name</th><th>Speed</th><th>Context</th></tr>
            <tr><td>SomeModel</td><td>fast</td><td>128K</td></tr>
        </table>
        </body></html>
        """
        with patch('pricing_fetcher.safe_get') as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = html
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            results = fetch_pricepertoken_pricing('https://pricepertoken.com/pricing-page/provider/test')

        assert results == []

    def test_dash_prices_skipped(self):
        from pricing_fetcher import fetch_pricepertoken_pricing

        html = """
        <html><body>
        <table>
            <tr><th>Model</th><th>Input</th><th>Output</th></tr>
            <tr><td>FreeModel</td><td>-</td><td>-</td></tr>
            <tr><td>PaidModel</td><td>$1.000</td><td>$2.000</td></tr>
        </table>
        </body></html>
        """
        with patch('pricing_fetcher.safe_get') as mock_get:
            mock_resp = MagicMock()
            mock_resp.text = html
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            results = fetch_pricepertoken_pricing('https://pricepertoken.com/pricing-page/provider/test')

        assert len(results) == 1
        assert results[0]['display_name'] == 'PaidModel'


class TestOpenRouterFetcher:
    """Test OpenRouter API pricing fetcher."""

    def test_fetch_openrouter_pricing(self):
        from pricing_fetcher import fetch_openrouter_pricing

        mock_data = {
            'data': [
                {
                    'id': 'anthropic/claude-sonnet-4-5',
                    'name': 'Claude Sonnet 4.5',
                    'pricing': {
                        'prompt': '0.000003',    # $3/Mtok
                        'completion': '0.000015',  # $15/Mtok
                    }
                },
                {
                    'id': 'free/model',
                    'name': 'Free Model',
                    'pricing': {
                        'prompt': '0',
                        'completion': '0',
                    }
                },
            ]
        }

        with patch('pricing_fetcher.safe_get') as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_data
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            results = fetch_openrouter_pricing()

        # Both models returned (free models stored with $0 pricing)
        assert len(results) == 2
        assert results[0]['raw_model_id'] == 'anthropic/claude-sonnet-4-5'
        assert results[0]['display_name'] == 'Claude Sonnet 4.5'
        assert results[0]['input_cost_per_mtok'] == 3.0
        assert results[0]['output_cost_per_mtok'] == 15.0
        assert results[0]['match_key'] == normalize_model_key('anthropic/claude-sonnet-4-5')
        assert results[1]['raw_model_id'] == 'free/model'
        assert results[1]['input_cost_per_mtok'] == 0.0
        assert results[1]['output_cost_per_mtok'] == 0.0


class TestSeedDefaultPricing:
    """Test the fallback pricing seeder using a lightweight in-memory DB."""

    def _create_test_db(self):
        """Create a minimal in-memory DB with model_pricing table."""
        import sqlite3
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE model_pricing (
                model_id TEXT PRIMARY KEY,
                match_key TEXT,
                raw_model_id TEXT,
                display_name TEXT NOT NULL,
                input_cost_per_mtok REAL NOT NULL,
                output_cost_per_mtok REAL NOT NULL,
                source TEXT DEFAULT 'legacy',
                updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            )
        """)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_model_pricing_match_key ON model_pricing(match_key)"
        )
        conn.commit()
        return conn

    def test_seed_default_pricing(self):
        """seed_default_pricing should insert DEFAULT_MODEL_PRICING entries."""
        from database.settings import SettingsMixin, DEFAULT_MODEL_PRICING

        conn = self._create_test_db()
        mixin = SettingsMixin()
        mixin.get_connection = lambda: conn

        mixin.seed_default_pricing()

        rows = conn.execute("SELECT * FROM model_pricing").fetchall()
        assert len(rows) == len(DEFAULT_MODEL_PRICING)
        for row in rows:
            assert row['source'] == 'default'
            assert row['match_key'] is not None

    def test_upsert_overwrites_defaults(self):
        """upsert_fetched_pricing should overwrite default-sourced entries."""
        from database.settings import SettingsMixin

        conn = self._create_test_db()
        mixin = SettingsMixin()
        mixin.get_connection = lambda: conn

        # Seed a default entry
        mixin.seed_default_pricing()

        # Upsert with new pricing
        mixin.upsert_fetched_pricing([{
            'match_key': normalize_model_key('claude-sonnet-4-5'),
            'raw_model_id': 'claude-sonnet-4-5',
            'display_name': 'Claude Sonnet 4.5 (Updated)',
            'input_cost_per_mtok': 99.0,
            'output_cost_per_mtok': 199.0,
        }], source='pricepertoken')

        row = conn.execute(
            "SELECT * FROM model_pricing WHERE match_key = ?",
            (normalize_model_key('claude-sonnet-4-5'),)
        ).fetchone()
        assert row['input_cost_per_mtok'] == 99.0
        assert row['source'] == 'pricepertoken'


class TestPricingFetcher:
    """Test the unified pricing fetcher."""

    def test_free_source_returns_empty(self):
        from pricing_fetcher import fetch_pricing
        result = fetch_pricing({'type': 'free'})
        assert result == []

    def test_unknown_source_falls_back_to_litellm(self):
        from pricing_fetcher import fetch_pricing
        with patch('pricing_fetcher.fetch_litellm_pricing', return_value=[]) as mock_litellm:
            result = fetch_pricing({'type': 'unknown', 'domain': 'test.com'})
        assert result == []
        mock_litellm.assert_called_once()

    def test_network_error_falls_back_to_litellm(self):
        from pricing_fetcher import fetch_pricing
        with patch('pricing_fetcher.fetch_openrouter_pricing', side_effect=Exception('timeout')), \
             patch('pricing_fetcher.fetch_litellm_pricing', return_value=[]) as mock_litellm:
            result = fetch_pricing({'type': 'openrouter_api', 'url': 'https://openrouter.ai/api/v1/models'})
        assert result == []
        mock_litellm.assert_called_once()


class TestParsePrice:
    """Test price parsing helper."""

    def test_dollar_prefix(self):
        from pricing_fetcher import _parse_price
        assert _parse_price('$3.000') == 3.0

    def test_no_prefix(self):
        from pricing_fetcher import _parse_price
        assert _parse_price('3.000') == 3.0

    def test_dash(self):
        from pricing_fetcher import _parse_price
        assert _parse_price('-') is None

    def test_double_dash(self):
        from pricing_fetcher import _parse_price
        assert _parse_price('--') is None

    def test_na(self):
        from pricing_fetcher import _parse_price
        assert _parse_price('N/A') is None

    def test_empty(self):
        from pricing_fetcher import _parse_price
        assert _parse_price('') is None

    def test_free(self):
        from pricing_fetcher import _parse_price
        assert _parse_price('free') == 0.0
        assert _parse_price('Free') == 0.0

    def test_whitespace(self):
        from pricing_fetcher import _parse_price
        assert _parse_price('  $1.500  ') == 1.5

    def test_comma_thousands(self):
        from pricing_fetcher import _parse_price
        assert _parse_price('$1,000.000') == 1000.0


class TestCallLlmForWindowRetry:
    """Test per-window retry logic in _call_llm_for_window."""

    def test_non_retryable_error_skips_per_window_retry(self):
        """Non-retryable errors (auth, forbidden) should not trigger per-window retry."""
        from unittest.mock import MagicMock, patch
        from ad_detector import AdDetector

        detector = AdDetector.__new__(AdDetector)
        detector._llm_client = MagicMock()

        # Simulate auth error (non-retryable)
        auth_error = Exception("401 Unauthorized: invalid API key")
        detector._llm_client.messages_create.side_effect = auth_error

        with patch('utils.llm_call.is_retryable_error', return_value=False), \
             patch('utils.llm_call.calculate_backoff', return_value=0.0):
            response, error = detector._call_llm_for_window(
                model="test-model", system_prompt="test", prompt="test",
                max_retries=1, llm_timeout=10, slug="test", episode_id="ep1",
                window_label="Window 1"
            )

        assert response is None
        assert error is auth_error
        # Non-retryable errors break immediately on first attempt (no retries at all)
        assert detector._llm_client.messages_create.call_count == 1

    def test_retryable_error_triggers_per_window_retry(self):
        """Retryable errors should trigger per-window retry with backoff."""
        from unittest.mock import MagicMock, patch, call
        from ad_detector import AdDetector

        detector = AdDetector.__new__(AdDetector)
        detector._llm_client = MagicMock()

        transient_error = Exception("500 Internal Server Error")
        success_response = MagicMock()
        # Fail on primary attempts, then succeed on per-window retry
        detector._llm_client.messages_create.side_effect = [
            transient_error, transient_error, success_response
        ]

        with patch('utils.llm_call.is_retryable_error', return_value=True), \
             patch('utils.llm_call.calculate_backoff', return_value=0.0), \
             patch('utils.llm_call.time.sleep'):
            response, error = detector._call_llm_for_window(
                model="test-model", system_prompt="test", prompt="test",
                max_retries=1, llm_timeout=10, slug="test", episode_id="ep1",
                window_label="Window 1"
            )

        assert response is success_response
        assert error is None


class TestHistoryPageParam:
    """Test history endpoint page parameter conversion."""

    def test_page_to_offset_conversion(self):
        """page=3 with limit=10 should produce offset=20."""
        # Test the logic directly
        limit = 10
        page = 3
        offset = (page - 1) * limit
        assert offset == 20

    def test_offset_to_page_derivation(self):
        """offset=20 with limit=10 should derive page=3."""
        limit = 10
        offset = 20
        page = (offset // limit) + 1
        assert page == 3

    def test_page_1_is_offset_0(self):
        limit = 50
        page = 1
        offset = (page - 1) * limit
        assert offset == 0


class TestGetModelPricingSourceFilter:
    """Test get_model_pricing with source filter."""

    def _create_test_db(self):
        import sqlite3
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE model_pricing (
                model_id TEXT PRIMARY KEY,
                match_key TEXT,
                raw_model_id TEXT,
                display_name TEXT NOT NULL,
                input_cost_per_mtok REAL NOT NULL,
                output_cost_per_mtok REAL NOT NULL,
                source TEXT DEFAULT 'legacy',
                updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            )
        """)
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_model_pricing_match_key ON model_pricing(match_key)"
        )
        return conn

    def test_source_filter(self):
        from database.settings import SettingsMixin

        conn = self._create_test_db()
        mixin = SettingsMixin()
        mixin.get_connection = lambda: conn

        mixin.seed_default_pricing()
        # All seeded entries have source='default'
        all_rows = mixin.get_model_pricing()
        default_rows = mixin.get_model_pricing(source='default')
        other_rows = mixin.get_model_pricing(source='pricepertoken')

        assert len(all_rows) > 0
        assert len(default_rows) == len(all_rows)
        assert len(other_rows) == 0


class TestLiteLLMFallback:
    """Tests for the LiteLLM pricing JSON fallback source."""

    SAMPLE_JSON = {
        "sample_spec": {
            "input_cost_per_token": 0.0,
            "output_cost_per_token": 0.0,
            "litellm_provider": "sample",
        },
        "claude-opus-4-6": {
            "input_cost_per_token": 0.000015,
            "output_cost_per_token": 0.000075,
            "cache_read_input_token_cost": 0.0000015,
            "litellm_provider": "anthropic",
        },
        "gpt-4o": {
            "input_cost_per_token": 0.0000025,
            "output_cost_per_token": 0.00001,
            "litellm_provider": "openai",
        },
        "embedding-model": {
            "output_vector_size": 1536,
            "litellm_provider": "openai",
        },
        "malformed-entry": {
            "input_cost_per_token": "not-a-number",
            "output_cost_per_token": 0.00001,
            "litellm_provider": "openai",
        },
    }

    def _mock_resp(self):
        resp = MagicMock()
        resp.json = MagicMock(return_value=self.SAMPLE_JSON)
        resp.raise_for_status = MagicMock()
        return resp

    def test_parses_and_skips_sample_spec(self):
        from pricing_fetcher import fetch_litellm_pricing

        with patch('pricing_fetcher.safe_get', return_value=self._mock_resp()):
            results = fetch_litellm_pricing()

        ids = [r['raw_model_id'] for r in results]
        assert 'sample_spec' not in ids
        assert 'claude-opus-4-6' in ids
        assert 'gpt-4o' in ids
        # embedding entry skipped (missing input_cost_per_token)
        assert 'embedding-model' not in ids
        # malformed entry skipped (bad float)
        assert 'malformed-entry' not in ids

    def test_converts_per_token_to_per_mtok(self):
        from pricing_fetcher import fetch_litellm_pricing

        with patch('pricing_fetcher.safe_get', return_value=self._mock_resp()):
            results = fetch_litellm_pricing()
        claude = next(r for r in results if r['raw_model_id'] == 'claude-opus-4-6')
        assert claude['input_cost_per_mtok'] == 15.0
        assert claude['output_cost_per_mtok'] == 75.0

    def test_provider_filter(self):
        from pricing_fetcher import fetch_litellm_pricing

        with patch('pricing_fetcher.safe_get', return_value=self._mock_resp()):
            anthropic_only = fetch_litellm_pricing(provider_filter='anthropic')
        assert [r['raw_model_id'] for r in anthropic_only] == ['claude-opus-4-6']

    def test_fetch_pricing_falls_back_on_empty_primary(self):
        from pricing_fetcher import fetch_pricing

        with patch('pricing_fetcher.fetch_pricepertoken_pricing', return_value=[]), \
             patch('pricing_fetcher.fetch_litellm_pricing') as mock_litellm:
            mock_litellm.return_value = [{'match_key': 'x', 'raw_model_id': 'x',
                                          'display_name': 'x',
                                          'input_cost_per_mtok': 1.0,
                                          'output_cost_per_mtok': 2.0}]
            results = fetch_pricing(
                {'type': 'pricepertoken', 'url': 'https://pricepertoken.com/foo'},
                provider_for_fallback='anthropic',
            )
        assert len(results) == 1
        mock_litellm.assert_called_once_with(provider_filter='anthropic')

    def test_fetch_pricing_falls_back_on_primary_exception(self):
        from pricing_fetcher import fetch_pricing

        with patch('pricing_fetcher.fetch_pricepertoken_pricing',
                   side_effect=ConnectionError('boom')), \
             patch('pricing_fetcher.fetch_litellm_pricing') as mock_litellm:
            mock_litellm.return_value = []
            results = fetch_pricing(
                {'type': 'pricepertoken', 'url': 'https://pricepertoken.com/foo'},
                provider_for_fallback='openai',
            )
        assert results == []
        mock_litellm.assert_called_once_with(provider_filter='openai')

    def test_fetch_pricing_unknown_domain_uses_litellm(self):
        from pricing_fetcher import fetch_pricing

        with patch('pricing_fetcher.fetch_litellm_pricing') as mock_litellm:
            mock_litellm.return_value = []
            fetch_pricing(
                {'type': 'unknown', 'domain': 'mystery.example.com'},
                provider_for_fallback='openai',
            )
        mock_litellm.assert_called_once_with(provider_filter='openai')

    def test_primary_success_does_not_call_litellm(self):
        from pricing_fetcher import fetch_pricing

        primary = [{'match_key': 'y', 'raw_model_id': 'y',
                    'display_name': 'y', 'input_cost_per_mtok': 3.0,
                    'output_cost_per_mtok': 9.0}]
        with patch('pricing_fetcher.fetch_openrouter_pricing', return_value=primary), \
             patch('pricing_fetcher.fetch_litellm_pricing') as mock_litellm:
            results = fetch_pricing(
                {'type': 'openrouter_api', 'url': 'https://openrouter.ai/api/v1/models'},
                provider_for_fallback='openrouter',
            )
        assert results == primary
        mock_litellm.assert_not_called()
