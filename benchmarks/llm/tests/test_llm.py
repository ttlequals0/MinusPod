"""Tests for the account-rate-limit pause: detection, shared per-provider
wait, the pause cap, and that other transient-error retry behavior is
unaffected.
"""
from __future__ import annotations

import asyncio

import httpx
import pytest
from openai import APIStatusError, RateLimitError

from benchmark import llm
from benchmark.config import ProviderConfig


class FakeClock:
    """Deterministic, non-sleeping clock/sleep pair for the retry loop.

    sleep() yields to the event loop (so concurrent tasks interleave) but
    advances the clock immediately instead of waiting in real time. Two
    concurrent sleeps aimed at the same resume time converge on it rather
    than stacking, matching real wall-clock concurrency.
    """

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.t = start

    def now(self) -> float:
        return self.t

    async def sleep(self, seconds: float) -> None:
        target = self.t + seconds
        await asyncio.sleep(0)
        self.t = max(self.t, target)


def _provider(name: str = "anthropic_direct") -> ProviderConfig:
    return ProviderConfig(name=name, client="openai_compatible", api_key_env="K", base_url="https://x")


def _openai_error(cls, *, status: int, body: dict | None, headers: dict | None = None):
    request = httpx.Request("POST", "https://x/v1/chat/completions")
    response = httpx.Response(status, request=request, headers=headers or {})
    return cls(str(body), response=response, body=body)


async def _call_kwargs(**overrides):
    base = dict(
        provider=_provider(), model_id="m1", system_prompt="s", user_prompt="u",
        temperature=0.0, max_tokens=100, timeout=30, response_format="json_object",
        max_retries=1,
    )
    base.update(overrides)
    return base


# --- _account_rate_limit_error: detection/parsing ---

def test_account_rate_limit_detected_by_code():
    body = {"error": {"message": "Upstream rate limit exceeded", "type": "upstream_api_error",
                       "code": "assistant_rate_limit", "rate_limit_type": "five_hour",
                       "resets_at": 1_700_000_000, "resets_at_iso": "2026-01-01T00:00:00Z",
                       "seconds_until_reset": 300}}
    err = _openai_error(RateLimitError, status=429, body=body, headers={"Retry-After": "30"})
    parsed = llm._account_rate_limit_error(err)
    assert parsed is not None
    assert parsed.resets_at == 1_700_000_000
    assert parsed.retry_after == 30.0
    assert parsed.reason == "assistant_rate_limit"


def test_openrouter_429_is_not_an_account_rate_limit():
    """No code, no resets_at: a per-model provider throttle, not the wrapper's
    account limit. Detection must return None so the old backoff path runs."""
    body = {"error": {"message": "Rate limited by upstream provider", "code": None}}
    err = _openai_error(RateLimitError, status=429, body=body, headers={"Retry-After": "5"})
    assert llm._account_rate_limit_error(err) is None


def test_non_429_is_not_an_account_rate_limit():
    err = _openai_error(APIStatusError, status=500, body={"error": {"message": "boom"}})
    assert llm._account_rate_limit_error(err) is None


# --- call_with_retry: pause behavior (fake_call raises the parsed error directly) ---

_OK = llm.LLMResponse(text="ok", input_tokens=1, output_tokens=1,
                      json_format_used="native", underlying_provider="p")


def _rate_limited_once(monkeypatch, *, resets_at=None, retry_after=None, calls=None):
    """Patch llm.call to raise AccountRateLimitError on the first call and
    succeed afterwards; `calls` collects the clock time of each attempt."""
    seen = calls if calls is not None else []

    async def fake_call(**kwargs):
        seen.append(kwargs)
        if len(seen) == 1:
            raise llm.AccountRateLimitError("rate limited", resets_at=resets_at,
                                             retry_after=retry_after, reason="assistant_rate_limit")
        return _OK

    monkeypatch.setattr(llm, "call", fake_call)
    return seen


def _call(clock, state, **overrides):
    async def run():
        kwargs = {"max_retries": 0, "pause_state": state, "now_fn": clock.now, "sleep_fn": clock.sleep, **overrides}
        return await llm.call_with_retry(**await _call_kwargs(**kwargs))
    return asyncio.run(run())


def test_account_rate_limit_records_pause_and_raises_without_sleeping(monkeypatch):
    """The caller owns the wait: call_with_retry records resets_at + slack on
    the shared state and raises, so the concurrency slot can be released."""
    clock = FakeClock()
    resets_at = clock.t + 100.0
    calls = _rate_limited_once(monkeypatch, resets_at=resets_at, retry_after=30.0)
    state = llm.ProviderPauseState()

    with pytest.raises(llm.ProviderPausedError):
        _call(clock, state)
    assert len(calls) == 1
    assert clock.t == 1_000_000.0
    assert state.resume_at("anthropic_direct") == resets_at + llm.ACCOUNT_RESET_SLACK_SECONDS


def test_paused_provider_is_not_sent_to_again_until_resumed(monkeypatch):
    clock = FakeClock()
    calls = _rate_limited_once(monkeypatch, resets_at=clock.t + 100.0)
    state = llm.ProviderPauseState()
    with pytest.raises(llm.ProviderPausedError):
        _call(clock, state)
    with pytest.raises(llm.ProviderPausedError):
        _call(clock, state)
    assert len(calls) == 1
    clock.t = state.resume_at("anthropic_direct")
    assert _call(clock, state).text == "ok"
    assert len(calls) == 2


@pytest.mark.parametrize("resets_at_offset, retry_after, expected_offset", [
    (None, 45.0, 45.0),
    (None, None, llm.ACCOUNT_RESET_DEFAULT_SECONDS),
])
def test_resume_time_falls_back_to_retry_after_then_default(monkeypatch, resets_at_offset, retry_after,
                                                            expected_offset):
    clock = FakeClock()
    _rate_limited_once(monkeypatch, resets_at=None, retry_after=retry_after)
    state = llm.ProviderPauseState()
    with pytest.raises(llm.ProviderPausedError):
        _call(clock, state)
    assert state.resume_at("anthropic_direct") == pytest.approx(clock.t + expected_offset)


def test_wait_above_cap_records_error(monkeypatch):
    clock = FakeClock()

    async def fake_call(**kwargs):
        raise llm.AccountRateLimitError("rate limited", resets_at=clock.t + 100.0,
                                         retry_after=None, reason="assistant_rate_limit")

    monkeypatch.setattr(llm, "call", fake_call)
    state = llm.ProviderPauseState()
    with pytest.raises(llm.AccountRateLimitError):
        _call(clock, state, max_rate_limit_pause_seconds=60.0)
    # Over the cap: the error propagates and no pause is recorded.
    assert state.resume_at("anthropic_direct") == 0.0


def test_old_backoff_path_unaffected_by_account_rate_limit_handling(monkeypatch):
    """LLMTransientError (e.g. from an OpenRouter-style 429) still retries
    with the existing exponential backoff and consumes max_retries."""
    clock = FakeClock()
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        await clock.sleep(seconds)

    attempts = {"n": 0}

    async def fake_call(**kwargs):
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise llm.LLMTransientError("rate limited by upstream provider")
        return _OK

    monkeypatch.setattr(llm, "call", fake_call)
    resp = _call(clock, llm.ProviderPauseState(), max_retries=2, sleep_fn=fake_sleep)
    assert resp.text == "ok"
    assert attempts["n"] == 3
    assert sleeps == [2.0, 4.0]


def test_pause_logs_once_at_warning_not_on_extend(caplog):
    """A second 429 that extends an already-active pause must not log again
    (no spam per waiting call)."""
    import logging
    state = llm.ProviderPauseState()

    with caplog.at_level(logging.WARNING, logger="benchmark.llm"):
        state.start_or_extend("p", 1000.0, reason="assistant_rate_limit", now=900.0)
        state.start_or_extend("p", 1100.0, reason="assistant_rate_limit", now=950.0)

    pause_logs = [r for r in caplog.records if "paused" in r.getMessage()]
    assert len(pause_logs) == 1
    assert "p" in pause_logs[0].getMessage()
    assert state.resume_at("p") == 1100.0


# --- SDK construction: max_retries=0, non-429 transient retry unaffected ---

class _FakeCompletions:
    def __init__(self, create):
        self.create = create


class _FakeChat:
    def __init__(self, create):
        self.completions = _FakeCompletions(create)


def test_sdk_client_built_with_max_retries_zero_and_500_still_retries(monkeypatch):
    monkeypatch.setenv("K", "dummy-key")
    captured = {}
    attempts = {"n": 0}

    class _FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.chat = _FakeChat(self._create)

        async def _create(self, **kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise _openai_error(APIStatusError, status=500, body={"error": {"message": "boom"}})
            msg = type("M", (), {})()
            msg.choices = [type("C", (), {"message": type("Msg", (), {"content": "{}"})(),
                                           "finish_reason": "stop"})()]
            msg.usage = type("U", (), {"prompt_tokens": 1, "completion_tokens": 1})()
            msg.model = "m1"
            return msg

    monkeypatch.setattr("openai.AsyncOpenAI", _FakeAsyncOpenAI)
    clock = FakeClock()

    async def run():
        return await llm.call_with_retry(
            **await _call_kwargs(max_retries=1, now_fn=clock.now, sleep_fn=clock.sleep,
                                  pause_state=llm.ProviderPauseState()),
        )

    resp = asyncio.run(run())
    assert resp.text == "{}"
    assert captured.get("max_retries") == 0
    assert attempts["n"] == 2


def _fake_success_msg():
    msg = type("M", (), {})()
    msg.choices = [type("C", (), {"message": type("Msg", (), {"content": "{}"})(),
                                   "finish_reason": "stop"})()]
    msg.usage = type("U", (), {"prompt_tokens": 1, "completion_tokens": 1})()
    msg.model = "m1"
    return msg


def test_inner_retries_absorb_two_500s_within_one_outer_attempt(monkeypatch):
    """500, 500, then 200: the SDK-shape inner retry (restored alongside
    max_retries=0) resolves this without the outer call_with_retry loop ever
    seeing a failure, proven by passing it max_retries=0."""
    monkeypatch.setenv("K", "dummy-key")
    attempts = {"n": 0}

    class _FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            self.chat = _FakeChat(self._create)

        async def _create(self, **kwargs):
            attempts["n"] += 1
            if attempts["n"] <= 2:
                raise _openai_error(APIStatusError, status=500, body={"error": {"message": "boom"}})
            return _fake_success_msg()

    monkeypatch.setattr("openai.AsyncOpenAI", _FakeAsyncOpenAI)
    clock = FakeClock()
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        await clock.sleep(seconds)

    async def run():
        return await llm.call_with_retry(
            **await _call_kwargs(max_retries=0, now_fn=clock.now, sleep_fn=fake_sleep,
                                  pause_state=llm.ProviderPauseState()),
        )

    resp = asyncio.run(run())
    assert resp.text == "{}"
    assert attempts["n"] == 3
    # Inner backoff shape: 0.5s doubling, capped at 8s, with jitter (<= requested delay).
    assert len(sleeps) == 2
    assert all(0.0 <= s <= 0.5 for s in sleeps[:1])
    assert all(0.0 <= s <= 1.0 for s in sleeps[1:2])


def test_three_500s_raise_one_llm_transient_error_to_outer_loop(monkeypatch):
    monkeypatch.setenv("K", "dummy-key")
    attempts = {"n": 0}

    class _FakeAsyncOpenAI:
        def __init__(self, **kwargs):
            self.chat = _FakeChat(self._create)

        async def _create(self, **kwargs):
            attempts["n"] += 1
            raise _openai_error(APIStatusError, status=500, body={"error": {"message": "boom"}})

    monkeypatch.setattr("openai.AsyncOpenAI", _FakeAsyncOpenAI)
    clock = FakeClock()

    async def run():
        return await llm.call_with_retry(
            **await _call_kwargs(max_retries=0, now_fn=clock.now, sleep_fn=clock.sleep,
                                  pause_state=llm.ProviderPauseState()),
        )

    with pytest.raises(llm.LLMTransientError):
        asyncio.run(run())
    # 1 initial + 2 inner retries; the outer loop (max_retries=0) never retries again.
    assert attempts["n"] == 3


def test_wait_for_pause_rechecks_after_extension_mid_sleep():
    """A second 429 extends the pause while two callers are already asleep;
    both must wait for the extended time, not resend at the old one."""
    clock = FakeClock()
    state = llm.ProviderPauseState()
    t1 = clock.t + 100.0
    t2 = clock.t + 300.0
    state.start_or_extend("p", t1, reason="r1", now=clock.t)

    pending: list[asyncio.Event] = []

    async def controlled_sleep(seconds):
        ev = asyncio.Event()
        pending.append(ev)
        await ev.wait()

    async def waiter():
        await llm.wait_for_pause("p", pause_state=state, now_fn=clock.now, sleep_fn=controlled_sleep)
        return clock.now()

    async def run():
        task_a = asyncio.create_task(waiter())
        task_b = asyncio.create_task(waiter())
        while len(pending) < 2:
            await asyncio.sleep(0)
        # Extend the pause while both are asleep, waiting on their first sleep.
        state.start_or_extend("p", t2, reason="r2", now=clock.now())
        clock.t = t1
        for ev in pending:
            ev.set()
        pending.clear()
        # Both must loop back, see the extension, and start a second sleep
        # rather than returning at t1.
        while len(pending) < 2:
            await asyncio.sleep(0)
        clock.t = t2
        for ev in pending:
            ev.set()
        return await asyncio.gather(task_a, task_b)

    finish_times = asyncio.run(run())
    assert all(t >= t2 for t in finish_times)
