"""System One pre-splits over-budget windows and halves on a context-overflow 400."""
import json

import httpx
import pytest
from openai import APIStatusError

from tests.app_bootstrap import bootstrap
bootstrap("systemone_context_overflow_")

from config import SYSTEMONE_TUNABLE_DEFAULTS
from systemone.adapter import SystemOneContextOverflowError, SystemOneSettings, run_chat_completion
from systemone.tuning import SystemOneSettingsError, validate_profile
from utils.llm_call import LOSS_CONTEXT_OVERFLOW, window_loss_class

LINES = [f"[{i}] {'filler words ' * 20}line {i}." for i in range(40)]
AD_SIDS = {10, 11, 12}
PROMPT = "Transcript:\n" + "\n".join(LINES) + "\n"


def _overflow():
    request = httpx.Request("POST", "https://example.com/v1")
    response = httpx.Response(400, request=request)
    return APIStatusError("request exceeds the available context size", response=response, body=None)


def _fetcher(calls, *, fail_over=None):
    def fetch(payload, **kwargs):
        questions = payload["questions"]
        calls.append(len(questions))
        if fail_over is not None and len(questions) > fail_over:
            raise _overflow()
        answers = {k: {"noul": 0.98 if int(k[1:]) in AD_SIDS else 0.02} for k in questions}
        return {"answers": answers, "usage": {"input_tokens": 100, "output_tokens": 5}}
    return fetch


def _run(fetcher, **settings):
    return run_chat_completion(
        messages=[{"role": "user", "content": PROMPT}], request_model="jev-latest",
        settings=SystemOneSettings(model="jev-latest", category_pass=False, **settings),
        fetcher=fetcher, sponsor_lookup=lambda: (),
    )


def _ads(result):
    return json.loads(result["choices"][0]["message"]["content"])["ads"]


def test_no_budget_sends_the_window_whole():
    calls = []
    result = _run(_fetcher(calls))
    assert calls == [40]
    assert len(_ads(result)) == 1


def test_over_budget_window_is_split_and_answers_merged():
    calls = []
    result = _run(_fetcher(calls), max_prompt_tokens=400)
    assert len(calls) > 1 and sum(calls) == 40
    assert len(_ads(result)) == 1
    assert result["usage"]["prompt_tokens"] == 100 * len(calls)


def test_within_budget_window_is_not_split():
    calls = []
    _run(_fetcher(calls), max_prompt_tokens=100000)
    assert calls == [40]


def test_overflow_400_halves_and_retries():
    calls = []
    result = _run(_fetcher(calls, fail_over=10))
    assert calls[0] == 40 and min(calls) == 10
    assert len(_ads(result)) == 1


def test_overflow_beyond_depth_raises_typed_loss():
    calls = []
    with pytest.raises(SystemOneContextOverflowError) as caught:
        _run(_fetcher(calls, fail_over=0))
    assert caught.value.estimated_tokens > 0
    assert "context size" in caught.value.server_text
    assert len(calls) == 4
    assert window_loss_class(caught.value) == LOSS_CONTEXT_OVERFLOW


def test_other_400_is_not_retried():
    def fetch(payload, **kwargs):
        raise APIStatusError("bad field", response=httpx.Response(
            400, request=httpx.Request("POST", "https://example.com")), body=None)
    with pytest.raises(APIStatusError):
        _run(fetch)


@pytest.mark.parametrize("provider", list(SYSTEMONE_TUNABLE_DEFAULTS))
def test_max_prompt_tokens_validation(provider):
    defaults = SYSTEMONE_TUNABLE_DEFAULTS[provider]
    assert defaults["maxPromptTokens"] is None
    validate_profile({**defaults, "maxPromptTokens": 7000}, defaults)
    validate_profile({**defaults, "maxPromptTokens": None}, defaults)
    for bad in (0, -1, 1.5, True, "7000"):
        with pytest.raises(SystemOneSettingsError):
            validate_profile({**defaults, "maxPromptTokens": bad}, defaults)
