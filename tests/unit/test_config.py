"""Regression tests for normalize_model_key mapping drift."""
import pytest

from config import normalize_model_key


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Anthropic canonical
        ("Claude Sonnet 4.5", "claudesonnet45"),
        ("claude-sonnet-4-5-20250929", "claudesonnet45"),
        ("anthropic/claude-sonnet-4-5", "claudesonnet45"),
        ("claude-opus-4-6", "claudeopus46"),
        ("claude-haiku-4-5-20251001", "claudehaiku45"),
        # OpenAI
        ("gpt-4o-mini", "gpt4omini"),
        ("gpt-4o-2024-05-13", "gpt4o"),
        # OpenRouter variants collapse to base
        ("anthropic/claude-sonnet-4-5:free", "claudesonnet45"),
        ("meta/llama-3.1-405b:extended", "llama31405b"),
    ],
)
def test_normalize_preserves_known_mappings(raw, expected):
    assert normalize_model_key(raw) == expected


@pytest.mark.parametrize(
    "a,b",
    [
        # Pairs that must NOT collapse to the same key
        ("claude-sonnet-4-5", "claude-sonnet-4-6"),
        ("claude-opus-4-6", "claude-opus-4-7"),
        ("claude-sonnet-4", "claude-haiku-4"),
    ],
)
def test_normalize_keeps_distinct_models_distinct(a, b):
    assert normalize_model_key(a) != normalize_model_key(b)


def test_known_gpt_collision_documented():
    """gpt-4omini and gpt-4o-mini collapse to the same key. This is a
    known artifact of the lossy normalization; the test fixes the
    behavior so a future tweak cannot silently introduce or resolve
    this collision."""
    assert normalize_model_key("gpt-4omini") == normalize_model_key("gpt-4o-mini")
