"""Tests for LLM response truncation warning."""
import logging

from llm_client import LLMClient


class ConcreteLLMClient(LLMClient):
    """Minimal concrete implementation for testing base class methods."""

    def messages_create(self, **kwargs):
        pass

    def list_models(self, bypass_cache=False):
        return []

    def get_provider_name(self):
        return "test"


class TestWarnIfTruncated:
    """Test LLMClient._warn_if_truncated."""

    def test_warns_on_max_tokens(self, caplog):
        client = ConcreteLLMClient()
        with caplog.at_level(logging.WARNING):
            client._warn_if_truncated("max_tokens", 2000, "claude-haiku-4-5")
        assert "truncated" in caplog.text
        assert "2000" in caplog.text
        assert "claude-haiku-4-5" in caplog.text

    def test_warns_on_length(self, caplog):
        client = ConcreteLLMClient()
        with caplog.at_level(logging.WARNING):
            client._warn_if_truncated("length", 4096, "gpt-4o")
        assert "truncated" in caplog.text
        assert "4096" in caplog.text

    def test_no_warning_on_stop(self, caplog):
        client = ConcreteLLMClient()
        with caplog.at_level(logging.WARNING):
            client._warn_if_truncated("end_turn", 2000, "model")
        assert "truncated" not in caplog.text

    def test_no_warning_on_none(self, caplog):
        client = ConcreteLLMClient()
        with caplog.at_level(logging.WARNING):
            client._warn_if_truncated(None, 2000, "model")
        assert "truncated" not in caplog.text
