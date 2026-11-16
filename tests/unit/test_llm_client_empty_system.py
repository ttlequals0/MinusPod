"""A blank system prompt sends no system message: gateways answer 400 to an empty text block."""
import json
import unittest
from unittest.mock import MagicMock, patch

import llm_client
from llm_client import OllamaNativeClient, OpenAICompatibleClient

USER = [{"role": "user", "content": "hi"}]


def _openai_response():
    r = MagicMock()
    choice = MagicMock()
    choice.message.content = "ok"
    choice.finish_reason = "stop"
    choice.message.reasoning = None
    choice.message.reasoning_content = None
    r.choices = [choice]
    r.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
    r.model = "m"
    return r


def _openai_client():
    c = OpenAICompatibleClient(base_url='http://localhost:8000/v1',
                               api_key='k', default_model='m')
    c._token_param_cache.clear()
    c._client = MagicMock()
    c._client.chat.completions.create.return_value = _openai_response()
    return c


def _sent_openai(system, **kw):
    c = _openai_client()
    c.messages_create(model="m", max_tokens=10, system=system, messages=list(USER), **kw)
    return c._client.chat.completions.create.call_args[1]["messages"]


def _sent_native(system, **kw):
    c = OllamaNativeClient(base_url='http://localhost:11434/v1', api_key='k',
                           default_model='m', ollama_num_ctx=8192)
    resp = MagicMock()
    resp.ok = True
    resp.status_code = 200
    resp.text = ""
    resp.json.return_value = {"model": "m", "message": {"role": "assistant", "content": "ok"},
                              "done_reason": "stop", "prompt_eval_count": 1, "eval_count": 1}
    with patch('utils.safe_http.safe_post', return_value=resp) as post:
        c.messages_create(model="m", max_tokens=10, system=system, messages=list(USER), **kw)
    body = post.call_args[1].get("json")
    if body is None:
        body = json.loads(post.call_args[1]["data"])
    return body["messages"]


class TestEmptySystemOmitted(unittest.TestCase):
    def test_openai_empty_and_whitespace_omit_system(self):
        for s in ("", "   ", "\n\t"):
            self.assertEqual(_sent_openai(s), USER)

    def test_openai_non_empty_unchanged(self):
        self.assertEqual(_sent_openai("be brief"),
                         [{"role": "system", "content": "be brief"}] + USER)

    def test_native_empty_and_whitespace_omit_system(self):
        for s in ("", "  "):
            self.assertEqual(_sent_native(s), USER)

    def test_native_non_empty_unchanged(self):
        self.assertEqual(_sent_native("be brief"),
                         [{"role": "system", "content": "be brief"}] + USER)

    def _fallback_client(self):
        c = _openai_client()
        c._get_json_format_supported = lambda model: False
        return c

    def test_json_fallback_empty_system_adds_system_message_not_user(self):
        c = self._fallback_client()
        c.messages_create(model="m", max_tokens=10, system="", messages=list(USER),
                          response_format={"type": "json_object"})
        sent = c._client.chat.completions.create.call_args[1]["messages"]
        self.assertEqual(sent[0]["role"], "system")
        self.assertIn("<output_format>", sent[0]["content"])
        self.assertTrue(sent[0]["content"].strip())
        self.assertEqual(sent[1:], USER)

    def test_json_fallback_non_empty_system_appends(self):
        c = self._fallback_client()
        c.messages_create(model="m", max_tokens=10, system="sys", messages=list(USER),
                          response_format={"type": "json_object"})
        sent = c._client.chat.completions.create.call_args[1]["messages"]
        self.assertEqual(sent[0]["content"], "sys" + llm_client._JSON_FORMAT_SYSTEM_INSTRUCTION)
        self.assertEqual(sent[1:], USER)


class TestAnthropicEmptySystemOmitted(unittest.TestCase):
    def _sent(self, system, **kw):
        from llm_client import AnthropicClient
        c = AnthropicClient(api_key="dummy")
        sdk = MagicMock()
        r = MagicMock()
        r.content = []
        r.usage = MagicMock(input_tokens=1, output_tokens=1,
                            cache_creation_input_tokens=0, cache_read_input_tokens=0)
        r.stop_reason = "end_turn"
        r.model = "claude-haiku-5-5"
        sdk.messages.create.return_value = r
        c._client = sdk
        c.messages_create(model="claude-haiku-5-5", max_tokens=10, system=system,
                          messages=list(USER), **kw)
        return sdk.messages.create.call_args.kwargs

    def test_empty_and_whitespace_send_no_system_param(self):
        for s in ("", "  \n"):
            self.assertNotIn("system", self._sent(s))

    def test_non_empty_still_sends_cached_block(self):
        self.assertEqual(self._sent("be brief")["system"][0]["text"], "be brief")

    def test_json_object_with_empty_system_gets_instruction_block(self):
        kw = self._sent("", response_format={"type": "json_object"})
        self.assertIn("<output_format>", kw["system"][0]["text"])


if __name__ == "__main__":
    unittest.main()
