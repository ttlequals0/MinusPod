"""OpenRouter model selection (issue #331): router-alias injection into the
model dropdown list, and the model-not-found hint on all-windows-failed."""
import ad_detector
from ad_detector import _model_not_found_hint
from api.settings import _ensure_openrouter_aliases_present
from config import OPENROUTER_ROUTER_ALIASES
from llm_client import is_not_found_error

ALIAS_IDS = [a[0] for a in OPENROUTER_ROUTER_ALIASES]


def _err(message='boom', status=None):
    e = Exception(message)
    if status is not None:
        e.status_code = status
    return e


class TestOpenRouterAliasInjection:
    def test_injects_all_aliases_into_empty_list(self):
        models = []
        _ensure_openrouter_aliases_present(models)
        assert [m['id'] for m in models] == ALIAS_IDS
        assert all(m['created'] is None for m in models)
        assert all(m['name'] for m in models)

    def test_aliases_prepended_before_existing_models(self):
        models = [{'id': 'anthropic/claude-sonnet-4-5', 'name': 'Claude', 'created': None}]
        _ensure_openrouter_aliases_present(models)
        ids = [m['id'] for m in models]
        assert ids[:len(ALIAS_IDS)] == ALIAS_IDS
        assert ids[-1] == 'anthropic/claude-sonnet-4-5'

    def test_does_not_duplicate_an_already_present_alias(self):
        models = [{'id': 'openrouter/free', 'name': 'existing', 'created': '1'}]
        _ensure_openrouter_aliases_present(models)
        ids = [m['id'] for m in models]
        assert ids.count('openrouter/free') == 1
        assert 'openrouter/auto' in ids


class TestIsNotFoundError:
    def test_404_status_code(self):
        assert is_not_found_error(_err(status=404))

    def test_not_found_token_in_message(self):
        assert is_not_found_error(_err('{"error":{"type":"not_found_error"}}'))

    def test_not_found_phrase_in_message(self):
        assert is_not_found_error(_err('model not found'))

    def test_rate_limit_is_not_not_found(self):
        assert not is_not_found_error(_err('rate limit exceeded', status=429))

    def test_none_is_not_not_found(self):
        assert not is_not_found_error(None)


class TestModelNotFoundHint:
    def test_404_returns_hint_naming_model_and_provider(self, monkeypatch):
        monkeypatch.setattr(ad_detector, 'get_effective_provider', lambda: 'openrouter')
        hint = _model_not_found_hint(_err(status=404), 'openrouter/free')
        assert "openrouter/free" in hint
        assert "openrouter" in hint

    def test_message_match_returns_hint(self, monkeypatch):
        monkeypatch.setattr(ad_detector, 'get_effective_provider', lambda: 'ollama')
        assert _model_not_found_hint(_err('model not found'), 'gpt-oss:120b')

    def test_rate_limit_returns_empty(self):
        assert _model_not_found_hint(_err('rate limit exceeded', status=429), 'anthropic/claude') == ''

    def test_none_returns_empty(self):
        assert _model_not_found_hint(None, 'anything') == ''
