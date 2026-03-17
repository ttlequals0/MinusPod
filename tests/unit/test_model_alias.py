"""Tests for Anthropic model alias filtering and resolution."""

import pytest
from unittest.mock import patch, MagicMock

from llm_client import (
    LLMModel,
    _filter_anthropic_aliases,
    _has_date_suffix,
    _strip_date_suffix,
    _alias_cache,
    resolve_anthropic_alias,
    AnthropicClient,
    OpenAICompatibleClient,
    LLMResponse,
)


@pytest.fixture(autouse=True)
def clear_alias_cache():
    """Clear the alias resolution cache before each test."""
    _alias_cache.clear()
    yield
    _alias_cache.clear()


# ---------------------------------------------------------------------------
# _has_date_suffix
# ---------------------------------------------------------------------------

def test_has_date_suffix_true():
    assert _has_date_suffix('claude-sonnet-4-5-20250929')
    assert _has_date_suffix('claude-opus-4-5-20251101')
    assert _has_date_suffix('claude-haiku-4-5-20251001')


def test_has_date_suffix_false():
    assert not _has_date_suffix('claude-sonnet-4-6')
    assert not _has_date_suffix('claude-opus-4-6')
    assert not _has_date_suffix('gpt-4o-mini')


# ---------------------------------------------------------------------------
# _strip_date_suffix
# ---------------------------------------------------------------------------

def test_strip_date_suffix():
    assert _strip_date_suffix('claude-sonnet-4-5-20250929') == 'claude-sonnet-4-5'
    assert _strip_date_suffix('claude-opus-4-5-20251101') == 'claude-opus-4-5'
    assert _strip_date_suffix('claude-haiku-4-5-20251001') == 'claude-haiku-4-5'
    assert _strip_date_suffix('claude-sonnet-4-6') == 'claude-sonnet-4-6'
    assert _strip_date_suffix('claude-opus-4-6') == 'claude-opus-4-6'


# ---------------------------------------------------------------------------
# _filter_anthropic_aliases
# ---------------------------------------------------------------------------

def test_filter_keeps_new_models_without_dated_counterpart():
    """4.6 models should NOT be filtered when only 4.5 dated models exist."""
    models = [
        LLMModel(id='claude-sonnet-4-6', name='Claude Sonnet 4.6'),
        LLMModel(id='claude-sonnet-4-5-20250929', name='Claude Sonnet 4.5'),
        LLMModel(id='claude-opus-4-6', name='Claude Opus 4.6'),
        LLMModel(id='claude-opus-4-5-20251101', name='Claude Opus 4.5'),
        LLMModel(id='claude-haiku-4-5-20251001', name='Claude Haiku 4.5'),
    ]
    result = _filter_anthropic_aliases(models)
    result_ids = {m.id for m in result}
    assert 'claude-sonnet-4-6' in result_ids
    assert 'claude-opus-4-6' in result_ids
    assert 'claude-sonnet-4-5-20250929' in result_ids
    assert 'claude-opus-4-5-20251101' in result_ids
    assert 'claude-haiku-4-5-20251001' in result_ids


def test_filter_removes_alias_when_exact_dated_exists():
    """claude-sonnet-4-5 alias is removed when claude-sonnet-4-5-20250929 exists."""
    models = [
        LLMModel(id='claude-sonnet-4-5', name='Claude Sonnet 4.5 alias'),
        LLMModel(id='claude-sonnet-4-5-20250929', name='Claude Sonnet 4.5'),
        LLMModel(id='claude-sonnet-4-6', name='Claude Sonnet 4.6'),
    ]
    result = _filter_anthropic_aliases(models)
    result_ids = {m.id for m in result}
    assert 'claude-sonnet-4-5' not in result_ids
    assert 'claude-sonnet-4-5-20250929' in result_ids
    assert 'claude-sonnet-4-6' in result_ids


def test_filter_removes_alias_when_future_dated_exists():
    """claude-sonnet-4-6 alias is removed when a dated 4.6 model exists."""
    models = [
        LLMModel(id='claude-sonnet-4-6', name='Claude Sonnet 4.6 alias'),
        LLMModel(id='claude-sonnet-4-6-20260301', name='Claude Sonnet 4.6'),
    ]
    result = _filter_anthropic_aliases(models)
    result_ids = {m.id for m in result}
    assert 'claude-sonnet-4-6' not in result_ids
    assert 'claude-sonnet-4-6-20260301' in result_ids


def test_filter_keeps_model_without_dated_counterpart():
    models = [
        LLMModel(id='claude-mystery-7', name='Claude Mystery 7'),
    ]
    result = _filter_anthropic_aliases(models)
    assert len(result) == 1
    assert result[0].id == 'claude-mystery-7'


def test_filter_handles_empty_list():
    assert _filter_anthropic_aliases([]) == []


def test_filter_preserves_non_claude_models():
    models = [
        LLMModel(id='gpt-4o', name='GPT-4o'),
        LLMModel(id='claude-sonnet-4-6', name='Claude Sonnet 4.6'),
        LLMModel(id='claude-sonnet-4-5-20250929', name='dated'),
    ]
    result = _filter_anthropic_aliases(models)
    result_ids = {m.id for m in result}
    assert 'gpt-4o' in result_ids
    assert 'claude-sonnet-4-5-20250929' in result_ids
    assert 'claude-sonnet-4-6' in result_ids


# ---------------------------------------------------------------------------
# resolve_anthropic_alias
# ---------------------------------------------------------------------------

def test_resolve_passes_through_dated_id():
    result = resolve_anthropic_alias('claude-sonnet-4-5-20250929')
    assert result == 'claude-sonnet-4-5-20250929'


def test_resolve_passes_through_non_claude():
    result = resolve_anthropic_alias('gpt-4o-mini')
    assert result == 'gpt-4o-mini'


@patch('llm_client.get_llm_client')
def test_resolve_alias_to_dated(mock_get_client):
    """claude-sonnet-4-5 resolves to its dated counterpart."""
    mock_client = MagicMock(spec=AnthropicClient)
    mock_client.list_models.return_value = [
        LLMModel(id='claude-sonnet-4-5-20250929', name='Claude Sonnet 4.5'),
        LLMModel(id='claude-opus-4-5-20251101', name='Claude Opus 4.5'),
    ]
    mock_get_client.return_value = mock_client

    result = resolve_anthropic_alias('claude-sonnet-4-5')
    assert result == 'claude-sonnet-4-5-20250929'


@patch('llm_client.get_llm_client')
def test_resolve_returns_unchanged_when_no_version_match(mock_get_client):
    """claude-sonnet-4-6 stays unchanged when only 4.5 dated models exist."""
    mock_client = MagicMock(spec=AnthropicClient)
    mock_client.list_models.return_value = [
        LLMModel(id='claude-sonnet-4-5-20250929', name='Claude Sonnet 4.5'),
        LLMModel(id='claude-opus-4-5-20251101', name='Claude Opus 4.5'),
    ]
    mock_get_client.return_value = mock_client

    result = resolve_anthropic_alias('claude-sonnet-4-6')
    assert result == 'claude-sonnet-4-6'


@patch('llm_client.get_llm_client')
def test_resolve_returns_original_on_no_match(mock_get_client):
    mock_client = MagicMock(spec=AnthropicClient)
    mock_client.list_models.return_value = []
    mock_get_client.return_value = mock_client

    result = resolve_anthropic_alias('claude-mystery-7')
    assert result == 'claude-mystery-7'


@patch('llm_client.get_llm_client')
def test_resolve_caches_result(mock_get_client):
    mock_client = MagicMock(spec=AnthropicClient)
    mock_client.list_models.return_value = [
        LLMModel(id='claude-sonnet-4-5-20250929', name='Claude Sonnet 4.5'),
    ]
    mock_get_client.return_value = mock_client

    # First call hits the API
    result1 = resolve_anthropic_alias('claude-sonnet-4-5')
    assert result1 == 'claude-sonnet-4-5-20250929'
    assert mock_client.list_models.call_count == 1

    # Second call uses cache -- no additional API call
    result2 = resolve_anthropic_alias('claude-sonnet-4-5')
    assert result2 == 'claude-sonnet-4-5-20250929'
    assert mock_client.list_models.call_count == 1


# ---------------------------------------------------------------------------
# messages_create returns requested model, not response.model
# ---------------------------------------------------------------------------

@patch('llm_client.AnthropicClient._ensure_client')
def test_anthropic_messages_create_uses_requested_model(mock_ensure):
    client = AnthropicClient(api_key='test-key')

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text='hello')]
    mock_response.model = 'claude-sonnet-4-5-20250929-internal-v2'
    mock_response.usage = MagicMock(input_tokens=10, output_tokens=5)

    mock_messages = MagicMock()
    mock_messages.create.return_value = mock_response
    client._client = MagicMock()
    client._client.messages = mock_messages

    result = client.messages_create(
        model='claude-sonnet-4-5-20250929',
        max_tokens=100,
        system='test',
        messages=[{'role': 'user', 'content': 'hi'}],
    )
    assert result.model == 'claude-sonnet-4-5-20250929'


@patch('llm_client.OpenAICompatibleClient._ensure_client')
def test_openai_messages_create_uses_requested_model(mock_ensure):
    client = OpenAICompatibleClient(base_url='http://test/v1', api_key='test')

    mock_choice = MagicMock()
    mock_choice.message.content = 'hello'
    mock_choice.message.reasoning = None
    mock_choice.message.reasoning_content = None

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.model = 'claude-sonnet-4-5-20250929-variant'
    mock_response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)

    client._client = MagicMock()
    client._client.chat.completions.create.return_value = mock_response
    # Pre-cache token param to avoid fallback logic
    client._token_param_cache['my-model'] = 'max_completion_tokens'

    result = client.messages_create(
        model='my-model',
        max_tokens=100,
        system='test',
        messages=[{'role': 'user', 'content': 'hi'}],
    )
    assert result.model == 'my-model'
