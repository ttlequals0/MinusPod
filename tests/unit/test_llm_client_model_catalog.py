"""Catalog fetch resilience: retry transient errors, serve last-good on failure."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import openai


def _make_client():
    from llm_client import OpenAICompatibleClient
    c = OpenAICompatibleClient(base_url='https://openrouter.ai/api/v1', api_key='k')
    c.credential_slot = 'secondary'
    c._ensure_client = lambda: None
    c._client = MagicMock()
    return c


def _models(ids):
    resp = MagicMock()
    resp.data = [SimpleNamespace(id=i, created=1) for i in ids]
    return resp


def _timeout():
    return openai.APITimeoutError(request=MagicMock())


class TestModelCatalogResilience(unittest.TestCase):
    def setUp(self):
        from llm_client import _clear_model_list_cache, _model_list_last_good
        _clear_model_list_cache()
        _model_list_last_good.clear()

    @patch('llm_client.time.sleep', return_value=None)
    def test_retries_transient_then_succeeds(self, _sleep):
        c = _make_client()
        c._client.models.list.side_effect = [_timeout(), _models(['a/b', 'c/d'])]
        models = c.list_models(bypass_cache=True)
        self.assertEqual([m.id for m in models], ['a/b', 'c/d'])
        self.assertEqual(c._client.models.list.call_count, 2)

    @patch('llm_client.time.sleep', return_value=None)
    def test_serves_last_good_after_a_later_failure(self, _sleep):
        c = _make_client()
        c._client.models.list.return_value = _models(['x/y'])
        self.assertEqual([m.id for m in c.list_models(bypass_cache=True)], ['x/y'])
        c._client.models.list.return_value = None
        c._client.models.list.side_effect = _timeout()
        with patch.object(c, '_try_ollama_native_list', return_value=None):
            models = c.list_models(bypass_cache=True)
        self.assertEqual([m.id for m in models], ['x/y'])

    @patch('llm_client.time.sleep', return_value=None)
    def test_returns_empty_when_no_last_good(self, _sleep):
        c = _make_client()
        c._client.models.list.side_effect = _timeout()
        with patch.object(c, '_try_ollama_native_list', return_value=None):
            self.assertEqual(c.list_models(bypass_cache=True), [])
        from llm_client import _MODEL_LIST_MAX_ATTEMPTS
        self.assertEqual(c._client.models.list.call_count, _MODEL_LIST_MAX_ATTEMPTS)

    @patch('llm_client.time.sleep', return_value=None)
    def test_non_transient_error_is_not_retried(self, _sleep):
        c = _make_client()
        c._client.models.list.side_effect = ValueError('bad request')
        with patch.object(c, '_try_ollama_native_list', return_value=None):
            self.assertEqual(c.list_models(bypass_cache=True), [])
        self.assertEqual(c._client.models.list.call_count, 1)

    @patch('llm_client.time.sleep', return_value=None)
    def test_hard_failure_does_not_serve_stale(self, _sleep):
        # A revoked key (non-transient) must surface [], not the prior catalog.
        c = _make_client()
        c._client.models.list.return_value = _models(['x/y'])
        self.assertEqual([m.id for m in c.list_models(bypass_cache=True)], ['x/y'])
        c._client.models.list.return_value = None
        c._client.models.list.side_effect = ValueError('401')
        with patch.object(c, '_try_ollama_native_list', return_value=None):
            self.assertEqual(c.list_models(bypass_cache=True), [])

    def test_clear_cache_drops_last_good(self):
        from llm_client import _clear_model_list_cache, _get_last_good_model_list
        c = _make_client()
        c._client.models.list.return_value = _models(['x/y'])
        c.list_models(bypass_cache=True)
        key = f"openai:{c.base_url}:{c.credential_slot}"
        self.assertIsNotNone(_get_last_good_model_list(key))
        _clear_model_list_cache()
        self.assertIsNone(_get_last_good_model_list(key))


if __name__ == '__main__':
    unittest.main()
