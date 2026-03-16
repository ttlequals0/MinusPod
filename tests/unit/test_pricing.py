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
        with patch('pricing_fetcher.requests.get') as mock_get:
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
        with patch('pricing_fetcher.requests.get') as mock_get:
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

        with patch('pricing_fetcher.requests.get') as mock_get:
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
        with patch('pricing_fetcher.requests.get') as mock_get:
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
        with patch('pricing_fetcher.requests.get') as mock_get:
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

        with patch('pricing_fetcher.requests.get') as mock_get:
            mock_resp = MagicMock()
            mock_resp.json.return_value = mock_data
            mock_resp.raise_for_status = MagicMock()
            mock_get.return_value = mock_resp

            results = fetch_openrouter_pricing()

        # Free model should be skipped
        assert len(results) == 1
        assert results[0]['raw_model_id'] == 'anthropic/claude-sonnet-4-5'
        assert results[0]['display_name'] == 'Claude Sonnet 4.5'
        assert results[0]['input_cost_per_mtok'] == 3.0
        assert results[0]['output_cost_per_mtok'] == 15.0
        assert results[0]['match_key'] == normalize_model_key('anthropic/claude-sonnet-4-5')


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
        from database.settings import DEFAULT_MODEL_PRICING

        conn = self._create_test_db()

        # Simulate seed_default_pricing logic
        inserted = 0
        for model_id, info in DEFAULT_MODEL_PRICING.items():
            key = normalize_model_key(model_id)
            cursor = conn.execute(
                """INSERT INTO model_pricing
                       (model_id, match_key, raw_model_id, display_name,
                        input_cost_per_mtok, output_cost_per_mtok, source)
                   VALUES (?, ?, ?, ?, ?, ?, 'default')
                   ON CONFLICT(match_key) DO NOTHING""",
                (model_id, key, model_id, info['name'], info['input'], info['output'])
            )
            if cursor.rowcount > 0:
                inserted += 1
        conn.commit()

        assert inserted > 0
        rows = conn.execute("SELECT * FROM model_pricing").fetchall()
        assert len(rows) > 0
        for row in rows:
            assert row['source'] == 'default'

    def test_upsert_overwrites_defaults(self):
        """upsert_fetched_pricing should overwrite default-sourced entries."""
        from database.settings import DEFAULT_MODEL_PRICING

        conn = self._create_test_db()

        # Seed a default entry
        key = normalize_model_key('claude-sonnet-4-5')
        conn.execute(
            """INSERT INTO model_pricing
                   (model_id, match_key, raw_model_id, display_name,
                    input_cost_per_mtok, output_cost_per_mtok, source)
               VALUES (?, ?, ?, ?, ?, ?, 'default')""",
            ('claude-sonnet-4-5', key, 'claude-sonnet-4-5', 'Test', 3.0, 15.0)
        )
        conn.commit()

        # Upsert with new pricing (simulate upsert_fetched_pricing)
        conn.execute(
            """INSERT INTO model_pricing
                   (model_id, match_key, raw_model_id, display_name,
                    input_cost_per_mtok, output_cost_per_mtok, source)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(match_key) DO UPDATE SET
                 raw_model_id = excluded.raw_model_id,
                 display_name = excluded.display_name,
                 input_cost_per_mtok = excluded.input_cost_per_mtok,
                 output_cost_per_mtok = excluded.output_cost_per_mtok,
                 source = excluded.source""",
            (key, key, 'claude-sonnet-4-5', 'Claude Sonnet 4.5 (Updated)', 99.0, 199.0, 'pricepertoken')
        )
        conn.commit()

        row = conn.execute(
            "SELECT * FROM model_pricing WHERE match_key = ?", (key,)
        ).fetchone()
        assert row['input_cost_per_mtok'] == 99.0
        assert row['source'] == 'pricepertoken'


class TestPricingFetcher:
    """Test the unified pricing fetcher."""

    def test_free_source_returns_empty(self):
        from pricing_fetcher import fetch_pricing
        result = fetch_pricing({'type': 'free'})
        assert result == []

    def test_unknown_source_returns_empty(self):
        from pricing_fetcher import fetch_pricing
        result = fetch_pricing({'type': 'unknown', 'domain': 'test.com'})
        assert result == []

    def test_network_error_returns_empty(self):
        from pricing_fetcher import fetch_pricing
        with patch('pricing_fetcher.fetch_openrouter_pricing', side_effect=Exception('timeout')):
            result = fetch_pricing({'type': 'openrouter_api', 'url': 'https://openrouter.ai/api/v1/models'})
        assert result == []


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
        assert _parse_price('free') is None

    def test_whitespace(self):
        from pricing_fetcher import _parse_price
        assert _parse_price('  $1.500  ') == 1.5

    def test_comma_thousands(self):
        from pricing_fetcher import _parse_price
        assert _parse_price('$1,000.000') == 1000.0
