"""
LLM Client Abstraction for MinusPod

Supports multiple backends:
- anthropic: Direct Anthropic API (default, uses API credits)
- openrouter: OpenRouter API (access 200+ models via one API key)
- openai-compatible: OpenAI-compatible APIs (Claude Code wrapper, Ollama, etc.)

Configuration via environment variables:
    LLM_PROVIDER: "anthropic" (default), "openrouter", or "openai-compatible"

    For anthropic:
        ANTHROPIC_API_KEY: Your API key

    For openrouter:
        OPENROUTER_API_KEY: Your OpenRouter API key

    For openai-compatible:
        OPENAI_BASE_URL: API endpoint (default: http://localhost:8000/v1)
        OPENAI_API_KEY: API key if required (default: "not-needed")
"""

import logging
import os
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Optional, Any

from utils.circuit_breaker import CircuitBreaker, CircuitBreakerOpen

from config import (
    LLM_TIMEOUT_DEFAULT,
    LLM_TIMEOUT_LOCAL,
    LLM_RETRY_MAX_RETRIES,
    LLM_RETRY_MAX_RETRIES_LOCAL,
    OPENROUTER_BASE_URL,
    OPENROUTER_HTTP_REFERER,
    OPENROUTER_APP_TITLE,
    PROVIDER_ANTHROPIC,
    PROVIDER_OPENROUTER,
    PROVIDER_OPENAI_COMPATIBLE,
    PROVIDER_OLLAMA,
    PROVIDERS_NON_ANTHROPIC,
)

logger = logging.getLogger(__name__)
io_logger = logging.getLogger('podcast.llm_io')


def _log_content(label: str, content: str, max_length: int = 2000):
    """Log LLM content at DEBUG level with intelligent truncation.

    Shows head (80%) + tail (20%) for content exceeding max_length.
    """
    if not io_logger.isEnabledFor(logging.DEBUG):
        return
    if len(content) <= max_length:
        io_logger.debug(f"{label} ({len(content)} chars):\n{content}")
    else:
        head_len = int(max_length * 0.8)
        tail_len = max_length - head_len
        io_logger.debug(
            f"{label} ({len(content)} chars, truncated):\n"
            f"{content[:head_len]}\n"
            f"... [{len(content) - max_length} chars omitted] ...\n"
            f"{content[-tail_len:]}"
        )


# Re-export error classes for backward compatibility
# These will be imported from here instead of directly from anthropic
try:
    from anthropic import APIError, APIConnectionError, RateLimitError, InternalServerError
    ANTHROPIC_ERRORS_AVAILABLE = True
except ImportError:
    ANTHROPIC_ERRORS_AVAILABLE = False
    # Create dummy classes if anthropic not installed
    class APIError(Exception): pass
    class APIConnectionError(Exception): pass
    class RateLimitError(Exception): pass
    class InternalServerError(Exception): pass


@dataclass
class LLMResponse:
    """Unified response format from any LLM backend."""
    content: str
    model: str
    usage: Optional[Dict[str, int]] = None
    raw_response: Any = None  # Original response object for advanced use


@dataclass
class LLMModel:
    """Model information."""
    id: str
    name: str
    created: Optional[str] = None


# =========================================================================
# DB-backed provider settings with short TTL cache
# =========================================================================

_provider_cache: Dict[str, Any] = {}
_provider_cache_lock = threading.Lock()
_PROVIDER_CACHE_TTL = 5.0  # seconds

# =========================================================================
# Model list cache (avoids hitting the API on every /settings page load)
# =========================================================================
_model_list_cache: Dict[str, Any] = {}
_model_list_cache_lock = threading.Lock()
_MODEL_LIST_CACHE_TTL = 300.0  # 5 minutes

def _get_cached_setting(key: str) -> Optional[str]:
    """Read a setting from DB with a short TTL cache to avoid per-request queries."""
    with _provider_cache_lock:
        entry = _provider_cache.get(key)
        if entry and (time.monotonic() - entry['ts']) < _PROVIDER_CACHE_TTL:
            return entry['val']
    try:
        from database import Database
        db = Database()
        val = db.get_setting(key)
        with _provider_cache_lock:
            _provider_cache[key] = {'val': val, 'ts': time.monotonic()}
        return val
    except Exception:
        return None


def _clear_provider_cache():
    """Flush the provider settings cache (called on force_new)."""
    with _provider_cache_lock:
        _provider_cache.clear()


def _get_cached_model_list(provider_key: str) -> Optional[List['LLMModel']]:
    """Return cached model list if still fresh, else None."""
    with _model_list_cache_lock:
        entry = _model_list_cache.get(provider_key)
        if entry and (time.monotonic() - entry['ts']) < _MODEL_LIST_CACHE_TTL:
            return entry['models']
    return None


def _set_cached_model_list(provider_key: str, models: List['LLMModel']):
    """Store a model list in the cache."""
    with _model_list_cache_lock:
        _model_list_cache[provider_key] = {'models': models, 'ts': time.monotonic()}


def _clear_model_list_cache():
    """Flush the model list cache (called on provider change or manual refresh)."""
    with _model_list_cache_lock:
        _model_list_cache.clear()


def get_effective_provider() -> str:
    """Return the active LLM provider, checking DB first then env var."""
    db_val = _get_cached_setting('llm_provider')
    if db_val:
        return db_val.lower()
    return os.environ.get('LLM_PROVIDER', PROVIDER_ANTHROPIC).lower()


def model_matches_provider(model_id: str, provider: str) -> bool:
    """Check whether a model ID plausibly belongs to the given provider."""
    if provider == PROVIDER_OPENROUTER:
        return True  # OpenRouter routes to any model
    is_claude_model = 'claude' in model_id.lower()
    if provider == PROVIDER_ANTHROPIC:
        return is_claude_model
    return not is_claude_model


def get_effective_base_url() -> str:
    """Return the active OpenAI base URL, checking DB first then env var."""
    db_val = _get_cached_setting('openai_base_url')
    if db_val:
        return db_val
    return os.environ.get('OPENAI_BASE_URL', 'http://localhost:8000/v1')


def get_effective_openrouter_api_key() -> Optional[str]:
    """Return the OpenRouter API key, checking DB first then env var.

    Note: DB reset stores '' (empty string) which is intentionally falsy
    so we fall through to the env var.  Do not change to ``is not None``.
    """
    db_val = _get_cached_setting('openrouter_api_key')
    if db_val:
        return db_val
    return os.environ.get('OPENROUTER_API_KEY')


class LLMClient(ABC):
    """Abstract base class for LLM clients."""

    def __init__(self):
        self._usage_callback = None
        self._circuit_breaker: Optional[CircuitBreaker] = None

    def set_circuit_breaker(self, cb: CircuitBreaker):
        """Attach a circuit breaker for API call protection."""
        self._circuit_breaker = cb

    def set_usage_callback(self, callback):
        """Set a callback to be invoked with (model, usage_dict) after each LLM call."""
        self._usage_callback = callback

    def _check_circuit_breaker(self):
        """Check circuit breaker before API call. Raises CircuitBreakerOpen if open."""
        if self._circuit_breaker:
            self._circuit_breaker.check()

    def _record_circuit_breaker(self, success: bool):
        """Record success/failure on the circuit breaker after API call."""
        if self._circuit_breaker:
            if success:
                self._circuit_breaker.record_success()
            else:
                self._circuit_breaker.record_failure()

    def _warn_if_truncated(self, stop_indicator: str, max_tokens: int, model: str):
        """Log a warning if the LLM response was truncated due to max_tokens."""
        if stop_indicator in ('max_tokens', 'length'):
            logger.warning(f"LLM response truncated (hit max_tokens={max_tokens}, model={model})")

    def _notify_usage(self, response: 'LLMResponse'):
        """Notify the usage callback if set. Errors are logged but never propagated."""
        if self._usage_callback and response.usage:
            try:
                self._usage_callback(response.model, response.usage)
            except Exception as e:
                logger.warning(f"Token usage recording failed: {e}")

    def _log_messages(self, provider_label: str, system: str, messages: List[Dict],
                       model: str, temperature: float, max_tokens: int):
        """Log request details for debugging. Shared by all client implementations."""
        _log_content(f"{provider_label} system prompt", system)
        for i, msg in enumerate(messages):
            content_val = msg.get('content', '')
            if isinstance(content_val, list):
                content_str = ' '.join(
                    part.get('text', '') for part in content_val
                    if isinstance(part, dict) and part.get('type') == 'text'
                ) or str(content_val)
            else:
                content_str = str(content_val)
            _log_content(f"{provider_label} message[{i}] role={msg.get('role')}", content_str)
        io_logger.debug(f"{provider_label} request: model={model} temperature={temperature} max_tokens={max_tokens}")

    @abstractmethod
    def messages_create(
        self,
        model: str,
        max_tokens: int,
        system: str,
        messages: List[Dict],
        temperature: float = 0.0,
        timeout: float = 120.0,
        response_format: Optional[Dict[str, str]] = None
    ) -> LLMResponse:
        """Send a completion request (synchronous).

        Args:
            model: Model identifier
            max_tokens: Maximum tokens in response
            system: System prompt
            messages: List of message dicts with 'role' and 'content'
            temperature: Sampling temperature (0.0 = deterministic)
            timeout: Request timeout in seconds
            response_format: Optional format specification (e.g., {"type": "json_object"})
                           Used by OpenAI-compatible APIs to enforce JSON output

        Returns:
            LLMResponse with content, model, and usage info
        """
        pass

    @abstractmethod
    def list_models(self, bypass_cache: bool = False) -> List[LLMModel]:
        """List available models.

        Args:
            bypass_cache: If True, skip the TTL cache and fetch fresh data.

        Returns:
            List of LLMModel objects
        """
        pass

    @abstractmethod
    def get_provider_name(self) -> str:
        """Return the provider name for logging."""
        pass


class AnthropicClient(LLMClient):
    """Native Anthropic API client."""

    def __init__(self, api_key: Optional[str] = None):
        super().__init__()
        self.api_key = api_key or os.environ.get('ANTHROPIC_API_KEY')
        self._client = None

    def _ensure_client(self):
        """Lazy initialize the Anthropic client."""
        if self._client is None:
            if not self.api_key:
                raise ValueError("No Anthropic API key provided")
            from anthropic import Anthropic
            self._client = Anthropic(api_key=self.api_key)
            logger.info("Anthropic client initialized")

    def messages_create(
        self,
        model: str,
        max_tokens: int,
        system: str,
        messages: List[Dict],
        temperature: float = 0.0,
        timeout: float = 120.0,
        response_format: Optional[Dict[str, str]] = None
    ) -> LLMResponse:
        self._check_circuit_breaker()
        self._ensure_client()

        # Anthropic doesn't support response_format natively;
        # inject JSON instructions into the system prompt when requested
        effective_system = system
        if response_format and response_format.get('type') == 'json_object':
            json_instruction = (
                "\n\n<output_format>CRITICAL JSON REQUIREMENTS:\n"
                "1. Respond with ONLY valid JSON - no markdown, no ```json, no text\n"
                "2. Start directly with '[' or '{', end with ']' or '}'\n"
                "3. Use double quotes for strings, no trailing commas\n"
                "4. Use null for missing values (not None)\n"
                "Malformed JSON causes parsing failures.</output_format>"
            )
            if '<output_format>' not in system:
                effective_system = system + json_instruction
                logger.debug("Added JSON format instructions to system prompt")

        self._log_messages("Anthropic", effective_system, messages, model, temperature, max_tokens)

        try:
            response = self._client.messages.create(
                model=model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=effective_system,
                messages=messages,
                timeout=timeout
            )
        except Exception:
            self._record_circuit_breaker(success=False)
            raise

        self._record_circuit_breaker(success=True)

        content = (response.content[0].text or "") if response.content else ""

        self._warn_if_truncated(
            getattr(response, 'stop_reason', None), max_tokens, model
        )

        llm_response = LLMResponse(
            content=content,
            model=model,
            usage={
                'input_tokens': response.usage.input_tokens,
                'output_tokens': response.usage.output_tokens
            } if response.usage else None,
            raw_response=response
        )

        # Log response
        _log_content("Anthropic response", content)
        if llm_response.usage:
            io_logger.info(
                f"Anthropic response: model={llm_response.model}"
                f" in={llm_response.usage['input_tokens']}"
                f" out={llm_response.usage['output_tokens']}"
                f" len={len(content)}"
            )

        self._notify_usage(llm_response)
        return llm_response

    def list_models(self, bypass_cache: bool = False) -> List[LLMModel]:
        cached = None if bypass_cache else _get_cached_model_list(PROVIDER_ANTHROPIC)
        if cached is not None:
            return cached

        self._ensure_client()

        try:
            response = self._client.models.list()
            models = []
            for model in response.data:
                if model_matches_provider(model.id, PROVIDER_ANTHROPIC):
                    models.append(LLMModel(
                        id=model.id,
                        name=model.display_name if hasattr(model, 'display_name') else model.id,
                        created=str(model.created) if hasattr(model, 'created') else None
                    ))
            _set_cached_model_list(PROVIDER_ANTHROPIC, models)
            return models
        except Exception as e:
            logger.error(f"Could not fetch models from Anthropic API: {e}")
            return []

    def get_provider_name(self) -> str:
        return "anthropic"


class OpenAICompatibleClient(LLMClient):
    """OpenAI-compatible API client.

    Works with:
    - Claude Code OpenAI wrapper (uses Max subscription)
    - Ollama
    - Any OpenAI-compatible API
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        default_model: Optional[str] = None,
        extra_headers: Optional[Dict[str, str]] = None
    ):
        super().__init__()
        self.base_url = base_url or os.environ.get('OPENAI_BASE_URL', 'http://localhost:8000/v1')
        self.api_key = api_key or os.environ.get('OPENAI_API_KEY', 'not-needed')
        self.default_model = default_model or os.environ.get('OPENAI_MODEL', 'claude-sonnet-4-5-20250929')
        self.extra_headers = extra_headers or {}
        self._client = None
        # Cache which token parameter each model accepts: "max_completion_tokens" or "max_tokens"
        # Per-instance to avoid cross-contamination between clients with different base_urls
        self._token_param_cache: Dict[str, str] = {}

    def _ensure_client(self):
        """Lazy initialize the OpenAI client."""
        if self._client is None:
            from openai import OpenAI
            kwargs: Dict[str, Any] = {
                'base_url': self.base_url,
                'api_key': self.api_key,
            }
            if self.extra_headers:
                kwargs['default_headers'] = self.extra_headers
            self._client = OpenAI(**kwargs)
            logger.info(f"OpenAI-compatible client initialized (base_url: {self.base_url})")

    def _call_with_token_param_fallback(self, model, kwargs, token_param):
        """Call the API, falling back to the alternate token parameter on 400 errors."""
        from openai import BadRequestError
        try:
            return self._client.chat.completions.create(**kwargs)
        except BadRequestError as e:
            alt_param = "max_tokens" if token_param == "max_completion_tokens" else "max_completion_tokens"
            error_lower = str(e).lower()
            if token_param not in error_lower and "max_tokens" not in error_lower:
                raise
            logger.info(f"Model {model} rejected '{token_param}', retrying with '{alt_param}'")
            token_value = kwargs.pop(token_param)
            kwargs[alt_param] = token_value
            self._token_param_cache[model] = alt_param
            return self._client.chat.completions.create(**kwargs)

    def messages_create(
        self,
        model: str,
        max_tokens: int,
        system: str,
        messages: List[Dict],
        temperature: float = 0.0,
        timeout: float = 120.0,
        response_format: Optional[Dict[str, str]] = None
    ) -> LLMResponse:
        self._check_circuit_breaker()
        self._ensure_client()

        all_messages = [{"role": "system", "content": system}] + messages

        self._log_messages("OpenAI", system, messages, model, temperature, max_tokens)

        # Newer OpenAI models require max_completion_tokens instead of max_tokens.
        # Try cached param first, fallback on error.
        cached_param = self._token_param_cache.get(model)
        token_param = cached_param or "max_completion_tokens"

        kwargs = {
            "model": model,
            token_param: max_tokens,
            "temperature": temperature,
            "messages": all_messages,
            "timeout": timeout
        }

        if response_format:
            kwargs["response_format"] = response_format

        try:
            if cached_param is not None:
                response = self._client.chat.completions.create(**kwargs)
            else:
                response = self._call_with_token_param_fallback(model, kwargs, token_param)
        except Exception:
            self._record_circuit_breaker(success=False)
            raise

        self._record_circuit_breaker(success=True)

        # Log reasoning/chain-of-thought if present (e.g. qwen3 think mode)
        if response.choices:
            msg = response.choices[0].message
            reasoning = getattr(msg, 'reasoning', None) or getattr(msg, 'reasoning_content', None)
            if reasoning:
                logger.debug(f"LLM reasoning field present ({len(str(reasoning))} chars)")

        content = (response.choices[0].message.content or "") if response.choices else ""

        finish_reason = getattr(response.choices[0], 'finish_reason', None) if response.choices else None
        self._warn_if_truncated(finish_reason, max_tokens, model)

        llm_response = LLMResponse(
            content=content,
            model=model,
            usage={
                'input_tokens': response.usage.prompt_tokens,
                'output_tokens': response.usage.completion_tokens
            } if response.usage else None,
            raw_response=response
        )

        # Log response
        _log_content("OpenAI response", content)
        if llm_response.usage:
            io_logger.info(
                f"OpenAI response: model={llm_response.model}"
                f" in={llm_response.usage['input_tokens']}"
                f" out={llm_response.usage['output_tokens']}"
                f" len={len(content)}"
            )

        self._notify_usage(llm_response)
        return llm_response

    def list_models(self, bypass_cache: bool = False) -> List[LLMModel]:
        """List models from the OpenAI-compatible API.

        Returns all models reported by the endpoint without filtering.
        This ensures Ollama models (qwen3, mistral, phi4-mini, etc.) are
        visible alongside Claude/GPT models from other providers.
        """
        cache_key = f"openai:{self.base_url}"
        cached = None if bypass_cache else _get_cached_model_list(cache_key)
        if cached is not None:
            return cached

        self._ensure_client()

        try:
            response = self._client.models.list()
            models = []
            for model in response.data:
                model_id = model.id if hasattr(model, 'id') else str(model)
                models.append(LLMModel(
                    id=model_id,
                    name=model_id,
                    created=str(model.created) if hasattr(model, 'created') else None
                ))
            _set_cached_model_list(cache_key, models)
            return models
        except Exception as e:
            logger.error(f"Could not fetch models from OpenAI-compatible API: {e}")
            native = self._try_ollama_native_list()
            if native:
                _set_cached_model_list(cache_key, native)
                return native
            return []

    def get_provider_name(self) -> str:
        return f"openai-compatible ({self.base_url})"

    def verify_connection(self, timeout: float = 10.0) -> bool:
        """Verify the endpoint is reachable by fetching models.

        Args:
            timeout: Request timeout in seconds

        Returns:
            True if connection successful, False otherwise

        Raises:
            ConnectionError: If connection fails and raise_on_error=True
        """
        self._ensure_client()

        try:
            # Try to list models - this verifies the endpoint is reachable
            response = self._client.models.list(timeout=timeout)
            models = list(response.data) if response.data else []
            logger.info(f"LLM endpoint verified: {self.base_url} ({len(models)} models available)")
            return True
        except Exception as e:
            logger.warning(f"OpenAI-compatible model list failed: {self.base_url} - {e}")
            native = self._try_ollama_native_list()
            if native:
                logger.info(f"LLM endpoint verified via Ollama native API ({len(native)} models)")
                return True
            logger.error(f"LLM endpoint verification failed: {self.base_url} - {e}")
            return False

    def _try_ollama_native_list(self) -> List[LLMModel]:
        """Try Ollama's native /api/tags endpoint as a fallback for model listing.

        Strips /v1 from self.base_url to derive the Ollama root, then queries
        GET {root}/api/tags. Returns a list of LLMModel on success, empty list
        on any failure.
        """
        root = self.base_url.rstrip('/')
        if root.endswith('/v1'):
            root = root[:-3]

        url = f"{root}/api/tags"
        try:
            import requests
            resp = requests.get(url, timeout=10.0)
            resp.raise_for_status()
            data = resp.json()
            models = []
            for entry in data.get('models', []):
                name = entry.get('name', '')
                if name:
                    models.append(LLMModel(id=name, name=name))
            if models:
                logger.info(f"Ollama native /api/tags returned {len(models)} models")
            return models
        except Exception as e:
            logger.debug(f"Ollama native /api/tags fallback failed: {e}")
            return []


# =============================================================================
# Provider-aware timeout / retry helpers
# =============================================================================

def get_llm_timeout() -> float:
    """Return the LLM request timeout based on the configured provider.

    Non-Anthropic providers (except OpenRouter, which is a fast cloud API)
    get a longer timeout since inference may be on-device or routed through
    a wrapper and significantly slower than the direct Anthropic API.
    """
    provider = get_effective_provider()
    if provider in (PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER):
        return LLM_TIMEOUT_DEFAULT
    return LLM_TIMEOUT_LOCAL


def get_llm_max_retries() -> int:
    """Return the max retry count based on the configured provider.

    Non-Anthropic providers (except OpenRouter) use fewer retries since
    each attempt may be slower than the direct Anthropic API.
    """
    provider = get_effective_provider()
    if provider in (PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER):
        return LLM_RETRY_MAX_RETRIES
    return LLM_RETRY_MAX_RETRIES_LOCAL


# =============================================================================
# Factory function - this is the main entry point
# =============================================================================

_cached_client: Optional[LLMClient] = None
_client_lock = threading.Lock()

# Circuit breaker for LLM API calls (one per process, shared across threads)
_llm_circuit_breaker = CircuitBreaker("llm-api", failure_threshold=5, recovery_timeout=60)

# Per-episode token accumulator using thread-local storage.
# Each thread (background processor, HTTP handler) gets its own
# independent accumulator so concurrent callers cannot corrupt each other.
_episode_accumulator = threading.local()


def _get_accumulator_active() -> bool:
    """Return whether the current thread's accumulator is active."""
    return getattr(_episode_accumulator, 'active', False)


def start_episode_token_tracking():
    """Reset and activate the per-episode token accumulator for the current thread."""
    _episode_accumulator.active = True
    _episode_accumulator.input_tokens = 0
    _episode_accumulator.output_tokens = 0
    _episode_accumulator.cost = 0.0
    logger.info(f"Episode token tracking: ACTIVATED (thread={threading.current_thread().name})")


def get_episode_token_totals() -> Dict:
    """Return accumulated totals, deactivate, and reset the accumulator for the current thread."""
    totals = {
        'input_tokens': getattr(_episode_accumulator, 'input_tokens', 0),
        'output_tokens': getattr(_episode_accumulator, 'output_tokens', 0),
        'cost': getattr(_episode_accumulator, 'cost', 0.0),
    }
    logger.info(
        f"Episode token totals: in={totals['input_tokens']} out={totals['output_tokens']}"
        f" cost=${totals['cost']:.6f} (thread={threading.current_thread().name})"
    )
    _episode_accumulator.active = False
    _episode_accumulator.input_tokens = 0
    _episode_accumulator.output_tokens = 0
    _episode_accumulator.cost = 0.0
    return totals


def _record_token_usage(model: str, usage: Dict):
    """Module-level callback for recording token usage to the database."""
    input_tokens = usage.get('input_tokens', 0)
    output_tokens = usage.get('output_tokens', 0)
    cost = 0.0

    try:
        from database import Database
        db = Database()
        cost = db.record_token_usage(
            model_id=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    except Exception as e:
        logger.warning(f"Failed to record token usage to DB: {e}")

    accum_active = _get_accumulator_active()
    logger.info(
        f"Token callback: model={model} in={input_tokens} out={output_tokens}"
        f" cost=${cost:.6f} accum_active={accum_active}"
        f" (thread={threading.current_thread().name})"
    )
    if accum_active:
        _episode_accumulator.input_tokens += input_tokens
        _episode_accumulator.output_tokens += output_tokens
        _episode_accumulator.cost += cost


def get_llm_client(force_new: bool = False) -> LLMClient:
    """
    Factory function that returns the appropriate LLM client based on config.

    The client is cached for reuse. Use force_new=True to create a fresh client
    (also flushes the provider settings cache).

    Settings are read from the database first, falling back to environment
    variables:
        LLM_PROVIDER: "anthropic" (default) or "openai-compatible"

        For anthropic:
            ANTHROPIC_API_KEY: Your API key

        For openai-compatible:
            OPENAI_BASE_URL: API endpoint (default: http://localhost:8000/v1)
            OPENAI_API_KEY: API key if required
            OPENAI_MODEL: Default model to use

    Returns:
        LLMClient instance
    """
    global _cached_client

    if force_new:
        _clear_provider_cache()
        _clear_model_list_cache()

    with _client_lock:
        if _cached_client is not None and not force_new:
            return _cached_client

        provider = get_effective_provider()

        _cached_client = _build_client(provider)
        if _cached_client is None:
            logger.warning(f"Unknown LLM_PROVIDER '{provider}', defaulting to anthropic")
            _cached_client = AnthropicClient()

        _cached_client.set_usage_callback(_record_token_usage)
        _cached_client.set_circuit_breaker(_llm_circuit_breaker)
        logger.info(f"LLM client initialized: {_cached_client.get_provider_name()}")
        return _cached_client


def _build_client(provider: str) -> Optional[LLMClient]:
    """Build an LLM client for a given provider without caching."""
    if provider == PROVIDER_ANTHROPIC:
        return AnthropicClient()
    elif provider == PROVIDER_OPENROUTER:
        api_key = get_effective_openrouter_api_key() or 'not-needed'
        return OpenAICompatibleClient(
            base_url=OPENROUTER_BASE_URL,
            api_key=api_key,
            extra_headers={
                'HTTP-Referer': OPENROUTER_HTTP_REFERER,
                'X-Title': OPENROUTER_APP_TITLE,
            }
        )
    elif provider in PROVIDERS_NON_ANTHROPIC:
        base_url = get_effective_base_url()
        if provider == PROVIDER_OLLAMA and not base_url.rstrip('/').endswith('/v1'):
            base_url = base_url.rstrip('/') + '/v1'
            logger.info(f"Ollama provider: normalized base_url to {base_url}")
        return OpenAICompatibleClient(base_url=base_url)
    return None


def create_client_for_provider(provider: str) -> Optional[LLMClient]:
    """Create a non-cached LLM client for a specific provider.

    Used for previewing available models before saving provider settings.
    Unlike get_llm_client(), this does not touch the global cache and does
    not set a usage callback -- only suitable for list_models() calls.
    """
    try:
        client = _build_client(provider)
        if client is None:
            logger.warning(f"Unknown provider '{provider}' for preview client")
        return client
    except Exception as e:
        logger.error(f"Failed to create preview client for provider '{provider}': {e}")
        return None


def get_api_key() -> Optional[str]:
    """Get the API key for the current provider.

    Returns:
        API key string or None if not set.
        Non-anthropic providers default to "not-needed" since local
        endpoints like Ollama don't require authentication.
    """
    provider = get_effective_provider()

    if provider == PROVIDER_ANTHROPIC:
        return os.environ.get('ANTHROPIC_API_KEY')
    elif provider == PROVIDER_OPENROUTER:
        return get_effective_openrouter_api_key()
    else:
        return os.environ.get('OPENAI_API_KEY', os.environ.get('ANTHROPIC_API_KEY', 'not-needed'))


def _verify_endpoint(label: str) -> bool:
    """Verify that an LLM endpoint is reachable via verify_connection."""
    try:
        client = get_llm_client(force_new=True)
        actual_url = getattr(client, 'base_url', 'unknown')
        logger.info(f"Verifying LLM endpoint: {actual_url}")
        if hasattr(client, 'verify_connection'):
            if not client.verify_connection(timeout=10.0):
                logger.error(f"LLM endpoint unreachable: {actual_url}")
                logger.error("Ad detection and chapter generation will fail until this is resolved")
                return False
        logger.info(f"LLM provider: {label} (verified, endpoint: {actual_url})")
        return True
    except Exception as e:
        logger.error(f"{label} endpoint verification failed: {e}")
        return False


def verify_llm_connection() -> bool:
    """Verify the LLM endpoint is reachable at startup.

    For OpenRouter and openai-compatible providers (including Ollama),
    delegates to _verify_endpoint which tests endpoint connectivity.
    For Anthropic, just verifies the API key is set.

    Returns:
        True if verification passed, False otherwise
    """
    provider = get_effective_provider()

    if provider == PROVIDER_OPENROUTER:
        api_key = get_effective_openrouter_api_key()
        if not api_key:
            logger.warning("No OPENROUTER_API_KEY configured - ad detection and chapter generation will be disabled")
            return False
        return _verify_endpoint('openrouter')
    elif provider in PROVIDERS_NON_ANTHROPIC:
        return _verify_endpoint(provider)
    else:
        # For Anthropic, verify API key is present
        api_key = get_api_key()
        if not api_key:
            logger.warning("No LLM API key configured - ad detection and chapter generation will be disabled")
            return False
        logger.info(f"LLM provider: {provider} (API key configured)")
        return True


# =============================================================================
# Backward compatibility helpers
# =============================================================================

def is_retryable_error(error: Exception) -> bool:
    """Check if an error is retryable (transient).

    Works with both Anthropic and OpenAI error types.
    """
    # Anthropic errors
    if ANTHROPIC_ERRORS_AVAILABLE:
        from anthropic import APIConnectionError, RateLimitError, InternalServerError, APIError
        if isinstance(error, (APIConnectionError, RateLimitError, InternalServerError)):
            return True
        # Check for specific status codes in generic APIError
        if isinstance(error, APIError):
            status = getattr(error, 'status_code', None)
            if status in (429, 500, 502, 503, 529):
                return True
            return False  # Non-retryable Anthropic error -- don't fall to string matching

    # OpenAI errors
    try:
        from openai import APIConnectionError as OpenAIConnectionError
        from openai import RateLimitError as OpenAIRateLimitError
        from openai import InternalServerError as OpenAIInternalError
        from openai import APIError as OpenAIAPIError
        if isinstance(error, (OpenAIConnectionError, OpenAIRateLimitError, OpenAIInternalError)):
            return True
        if isinstance(error, OpenAIAPIError):
            status = getattr(error, 'status_code', None)
            if status in (429, 500, 502, 503, 529):
                return True
            return False  # Non-retryable OpenAI error
    except ImportError:
        pass

    # Generic network errors - check error message patterns
    error_str = str(error).lower()
    retryable_patterns = ['timeout', 'connection', 'temporarily', '429', '500', '502', '503', '504', '529']
    return any(pattern in error_str for pattern in retryable_patterns)


def is_llm_api_error(error: Exception) -> bool:
    """Check if error is any Anthropic or OpenAI API error type."""
    if ANTHROPIC_ERRORS_AVAILABLE:
        from anthropic import APIError
        if isinstance(error, APIError):
            return True
    try:
        from openai import APIError as OpenAIAPIError
        if isinstance(error, OpenAIAPIError):
            return True
    except ImportError:
        pass
    return False


def is_rate_limit_error(error: Exception) -> bool:
    """Check if an error is specifically a rate limit error.

    Used for special handling (longer backoff).
    """
    # Check Anthropic RateLimitError
    if ANTHROPIC_ERRORS_AVAILABLE:
        from anthropic import RateLimitError
        if isinstance(error, RateLimitError):
            return True

    # Check OpenAI RateLimitError
    try:
        from openai import RateLimitError as OpenAIRateLimitError
        if isinstance(error, OpenAIRateLimitError):
            return True
    except ImportError:
        pass

    # Check error message for rate limit indicators
    error_str = str(error).lower()
    return 'rate' in error_str and ('limit' in error_str or '429' in error_str)
