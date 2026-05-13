"""Async LLM dispatch for the benchmark.

Two flavors:
- ``anthropic`` -> Anthropic's AsyncAnthropic SDK
- ``openai_compatible`` -> OpenAI AsyncOpenAI SDK with custom base_url
  (covers OpenRouter, Together, OpenAI direct, Groq, Fireworks, DeepInfra).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional

from .config import ProviderConfig, secret

logger = logging.getLogger(__name__)

# Process-level memo of Anthropic models that have rejected `temperature` as
# deprecated. Populated lazily on the first 400 per model so subsequent calls
# skip the wasted round-trip. See _call_anthropic.
_ANTHROPIC_TEMPERATURE_DEPRECATED: set[str] = set()


@dataclass(frozen=True)
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    json_format_used: str  # "native" | "prompt_injection"
    underlying_provider: str
    # Anthropic: "end_turn" | "max_tokens" | "stop_sequence" | "tool_use" | None
    # OpenAI-compatible: "stop" | "length" | "content_filter" | "tool_calls" | None
    # Used by the benchmark to flag chatty models that hit max_tokens; None when
    # the provider didn't surface the value.
    stop_reason: Optional[str] = None


class LLMTransientError(RuntimeError):
    pass


class LLMNonRetryableError(RuntimeError):
    pass


async def call(
    *,
    provider: ProviderConfig,
    model_id: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
    response_format: str = "json_object",
) -> LLMResponse:
    if provider.client == "anthropic":
        return await _call_anthropic(
            provider=provider,
            model_id=model_id,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )
    if provider.client == "openai_compatible":
        return await _call_openai_compatible(
            provider=provider,
            model_id=model_id,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            response_format=response_format,
        )
    raise LLMNonRetryableError(f"Unknown provider client {provider.client!r}")


async def _call_anthropic(
    *,
    provider: ProviderConfig,
    model_id: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
) -> LLMResponse:
    from anthropic import AsyncAnthropic
    from anthropic import APIStatusError, APIConnectionError, APITimeoutError, RateLimitError

    client = AsyncAnthropic(api_key=secret(provider.api_key_env), timeout=timeout)
    # Anthropic deprecated `temperature` for the Claude 4.x family; the API
    # returns 400 "`temperature` is deprecated for this model.". We memoize
    # per model so each affected model burns at most one wasted round-trip
    # for the lifetime of the process; subsequent calls skip `temperature`
    # upfront. Mirrors the response_format fallback in _call_openai_compatible
    # but cached so a full sweep doesn't 400-then-200 every Anthropic call.
    skip_temperature = model_id in _ANTHROPIC_TEMPERATURE_DEPRECATED
    kwargs: dict[str, Any] = dict(
        model=model_id,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
    )
    if not skip_temperature:
        kwargs["temperature"] = temperature
    try:
        msg = await client.messages.create(**kwargs)
    except (RateLimitError, APITimeoutError, APIConnectionError) as e:
        raise LLMTransientError(str(e)) from e
    except APIStatusError as e:
        if (
            getattr(e, "status_code", 0) == 400
            and "temperature" in str(e).lower()
            and "temperature" in kwargs
        ):
            _ANTHROPIC_TEMPERATURE_DEPRECATED.add(model_id)
            kwargs.pop("temperature", None)
            try:
                msg = await client.messages.create(**kwargs)
            except (RateLimitError, APITimeoutError, APIConnectionError) as e2:
                raise LLMTransientError(str(e2)) from e2
            except APIStatusError as e2:
                if 500 <= getattr(e2, "status_code", 0) < 600:
                    raise LLMTransientError(str(e2)) from e2
                raise LLMNonRetryableError(str(e2)) from e2
        else:
            if 500 <= getattr(e, "status_code", 0) < 600:
                raise LLMTransientError(str(e)) from e
            raise LLMNonRetryableError(str(e)) from e

    text = "".join(block.text for block in msg.content if getattr(block, "type", None) == "text")
    return LLMResponse(
        text=text,
        input_tokens=msg.usage.input_tokens,
        output_tokens=msg.usage.output_tokens,
        json_format_used="prompt_injection",
        underlying_provider="Anthropic",
        stop_reason=getattr(msg, "stop_reason", None),
    )


async def _call_openai_compatible(
    *,
    provider: ProviderConfig,
    model_id: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
    response_format: str,
) -> LLMResponse:
    from openai import AsyncOpenAI
    from openai import APIConnectionError, APIStatusError, APITimeoutError, RateLimitError

    # OpenRouter recommends HTTP-Referer + X-Title headers for app attribution.
    # Routes calls to the project's free-tier allowance and shows up named in
    # OpenRouter's dashboard. Detected by base_url so it doesn't fire on
    # other openai_compatible providers.
    default_headers: dict[str, str] | None = None
    if provider.base_url and "openrouter.ai" in provider.base_url:
        default_headers = {
            "HTTP-Referer": "https://github.com/ttlequals0/MinusPod",
            "X-Title": "MinusPod LLM Benchmark",
        }

    client = AsyncOpenAI(
        api_key=secret(provider.api_key_env),
        base_url=provider.base_url,
        timeout=timeout,
        default_headers=default_headers,
    )
    kwargs: dict[str, Any] = dict(
        model=model_id,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )
    json_format_used = "prompt_injection"
    if response_format == "json_object":
        kwargs["response_format"] = {"type": "json_object"}
        json_format_used = "native"

    try:
        msg = await client.chat.completions.create(**kwargs)
    except (RateLimitError, APITimeoutError, APIConnectionError) as e:
        raise LLMTransientError(str(e)) from e
    except APIStatusError as e:
        status = getattr(e, "status_code", 0)
        if status == 400 and "response_format" in str(e).lower() and json_format_used == "native":
            kwargs.pop("response_format", None)
            try:
                msg = await client.chat.completions.create(**kwargs)
                json_format_used = "prompt_injection"
            except (RateLimitError, APITimeoutError, APIConnectionError) as e2:
                raise LLMTransientError(str(e2)) from e2
            except APIStatusError as e2:
                if 500 <= getattr(e2, "status_code", 0) < 600:
                    raise LLMTransientError(str(e2)) from e2
                raise LLMNonRetryableError(str(e2)) from e2
        elif 500 <= status < 600:
            raise LLMTransientError(str(e)) from e
        else:
            raise LLMNonRetryableError(str(e)) from e

    # OpenRouter sometimes returns a successful HTTP response with `choices=None`
    # when the upstream provider hits its own internal read-timeout (observed
    # around 120s on cohere/command-r-plus). Classify as transient so the
    # runner retries with backoff instead of crashing on `None[0]`.
    choices = msg.choices or []
    if not choices or not getattr(choices[0], "message", None):
        raise LLMTransientError(
            f"empty response from {model_id} (no choices in body; likely upstream timeout)"
        )
    choice = choices[0]
    text = choice.message.content or ""
    usage = msg.usage
    underlying = msg.model or model_id
    return LLMResponse(
        text=text,
        input_tokens=getattr(usage, "prompt_tokens", 0),
        output_tokens=getattr(usage, "completion_tokens", 0),
        json_format_used=json_format_used,
        underlying_provider=underlying,
        stop_reason=getattr(choice, "finish_reason", None),
    )


async def call_with_retry(
    *,
    provider: ProviderConfig,
    model_id: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    max_tokens: int,
    timeout: int,
    response_format: str,
    max_retries: int,
) -> LLMResponse:
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            return await call(
                provider=provider,
                model_id=model_id,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
                response_format=response_format,
            )
        except LLMTransientError as e:
            last_exc = e
            if attempt >= max_retries:
                raise
            backoff = min(2.0 * (2 ** attempt), 60.0)
            logger.warning("transient error on %s attempt %d/%d: %s; sleeping %.1fs",
                           model_id, attempt + 1, max_retries + 1, e, backoff)
            await asyncio.sleep(backoff)
    raise last_exc  # type: ignore[misc]
