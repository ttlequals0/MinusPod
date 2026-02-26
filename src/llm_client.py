"""
LLM Client Abstraction for MinusPod

Supports multiple backends:
- anthropic: Direct Anthropic API (default, uses API credits)
- openai-compatible: OpenAI-compatible APIs (Claude Code wrapper, Ollama, etc.)

Configuration via environment variables:
    LLM_PROVIDER: "anthropic" (default) or "openai-compatible"

    For anthropic:
        ANTHROPIC_API_KEY: Your API key

    For openai-compatible:
        OPENAI_BASE_URL: API endpoint (default: http://localhost:8000/v1)
        OPENAI_API_KEY: API key if required (default: "not-needed")
"""

import logging
import os
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Dict, Optional, Any

logger = logging.getLogger(__name__)

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


# Known Claude models used as fallback when the API model list is unavailable.
# Defined once here to avoid duplication across client implementations.
FALLBACK_MODELS = [
    LLMModel(id='claude-opus-4-6', name='Claude Opus 4.6'),
    LLMModel(id='claude-sonnet-4-5-20250929', name='Claude Sonnet 4.5'),
    LLMModel(id='claude-haiku-4-5-20251001', name='Claude Haiku 4.5'),
    LLMModel(id='claude-opus-4-5-20251101', name='Claude Opus 4.5'),
    LLMModel(id='claude-opus-4-1-20250805', name='Claude Opus 4.1'),
    LLMModel(id='claude-sonnet-4-20250514', name='Claude Sonnet 4'),
    LLMModel(id='claude-opus-4-20250514', name='Claude Opus 4'),
]


class LLMClient(ABC):
    """Abstract base class for LLM clients."""

    def __init__(self):
        self._usage_callback = None

    def set_usage_callback(self, callback):
        """Set a callback to be invoked with (model, usage_dict) after each LLM call."""
        self._usage_callback = callback

    def _notify_usage(self, response: 'LLMResponse'):
        """Notify the usage callback if set. Errors are logged but never propagated."""
        if self._usage_callback and response.usage:
            try:
                self._usage_callback(response.model, response.usage)
            except Exception as e:
                logger.warning(f"Token usage recording failed: {e}")

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
    def list_models(self) -> List[LLMModel]:
        """List available models.

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
        self._ensure_client()

        # Anthropic API doesn't support response_format parameter natively,
        # so we add explicit JSON instructions to the system prompt when requested
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
            # Only add if not already present
            if '<output_format>' not in system:
                effective_system = system + json_instruction
                logger.debug("Added JSON format instructions to system prompt")

        response = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=effective_system,
            messages=messages,
            timeout=timeout
        )

        content = response.content[0].text if response.content else ""

        llm_response = LLMResponse(
            content=content,
            model=response.model,
            usage={
                'input_tokens': response.usage.input_tokens,
                'output_tokens': response.usage.output_tokens
            } if response.usage else None,
            raw_response=response
        )
        self._notify_usage(llm_response)
        return llm_response

    def list_models(self) -> List[LLMModel]:
        self._ensure_client()

        try:
            response = self._client.models.list()
            models = []
            for model in response.data:
                if 'claude' in model.id.lower():
                    models.append(LLMModel(
                        id=model.id,
                        name=model.display_name if hasattr(model, 'display_name') else model.id,
                        created=str(model.created) if hasattr(model, 'created') else None
                    ))
            return models
        except Exception as e:
            logger.warning(f"Could not fetch models from Anthropic API: {e}")
            return self._get_fallback_models()

    def _get_fallback_models(self) -> List[LLMModel]:
        """Return known models as fallback."""
        return list(FALLBACK_MODELS)

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
        default_model: Optional[str] = None
    ):
        super().__init__()
        self.base_url = base_url or os.environ.get('OPENAI_BASE_URL', 'http://localhost:8000/v1')
        self.api_key = api_key or os.environ.get('OPENAI_API_KEY', 'not-needed')
        self.default_model = default_model or os.environ.get('OPENAI_MODEL', 'claude-sonnet-4-5-20250929')
        self._client = None

    def _ensure_client(self):
        """Lazy initialize the OpenAI client."""
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(
                base_url=self.base_url,
                api_key=self.api_key
            )
            logger.info(f"OpenAI-compatible client initialized (base_url: {self.base_url})")

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
        self._ensure_client()

        # OpenAI format uses system message in messages array
        all_messages = [{"role": "system", "content": system}] + messages

        # Build request kwargs
        kwargs = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": all_messages,
            "timeout": timeout
        }

        # Pass response_format if provided (triggers JSON mode in wrapper)
        if response_format:
            kwargs["response_format"] = response_format

        response = self._client.chat.completions.create(**kwargs)

        content = response.choices[0].message.content if response.choices else ""

        llm_response = LLMResponse(
            content=content,
            model=response.model,
            usage={
                'input_tokens': response.usage.prompt_tokens,
                'output_tokens': response.usage.completion_tokens
            } if response.usage else None,
            raw_response=response
        )
        self._notify_usage(llm_response)
        return llm_response

    def list_models(self) -> List[LLMModel]:
        """List models from the OpenAI-compatible API."""
        self._ensure_client()

        try:
            response = self._client.models.list()
            models = []
            for model in response.data:
                # Filter to Claude models if using Claude Code wrapper
                model_id = model.id if hasattr(model, 'id') else str(model)
                if 'claude' in model_id.lower() or 'gpt' in model_id.lower() or 'llama' in model_id.lower():
                    models.append(LLMModel(
                        id=model_id,
                        name=model_id,
                        created=str(model.created) if hasattr(model, 'created') else None
                    ))
            return models if models else self._get_fallback_models()
        except Exception as e:
            logger.warning(f"Could not fetch models from OpenAI-compatible API: {e}")
            return self._get_fallback_models()

    def _get_fallback_models(self) -> List[LLMModel]:
        """Return fallback models."""
        return list(FALLBACK_MODELS)

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
            logger.error(f"LLM endpoint verification failed: {self.base_url} - {e}")
            return False


# =============================================================================
# Factory function - this is the main entry point
# =============================================================================

_cached_client: Optional[LLMClient] = None

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

    The client is cached for reuse. Use force_new=True to create a fresh client.

    Environment variables:
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

    if _cached_client is not None and not force_new:
        return _cached_client

    provider = os.environ.get('LLM_PROVIDER', 'anthropic').lower()

    if provider == 'anthropic':
        _cached_client = AnthropicClient()
    elif provider in ('openai-compatible', 'openai', 'wrapper', 'ollama'):
        _cached_client = OpenAICompatibleClient()
    else:
        logger.warning(f"Unknown LLM_PROVIDER '{provider}', defaulting to anthropic")
        _cached_client = AnthropicClient()

    _cached_client.set_usage_callback(_record_token_usage)
    logger.info(f"LLM client initialized: {_cached_client.get_provider_name()}")
    return _cached_client


def get_api_key() -> Optional[str]:
    """Get the API key for the current provider.

    Returns:
        API key string or None if not set
    """
    provider = os.environ.get('LLM_PROVIDER', 'anthropic').lower()

    if provider == 'anthropic':
        return os.environ.get('ANTHROPIC_API_KEY')
    else:
        return os.environ.get('OPENAI_API_KEY', os.environ.get('ANTHROPIC_API_KEY'))


def verify_llm_connection() -> bool:
    """Verify the LLM endpoint is reachable at startup.

    For openai-compatible providers, this makes a test request to verify
    the endpoint is accessible. For Anthropic, this just verifies the
    API key is set.

    Returns:
        True if verification passed, False otherwise
    """
    provider = os.environ.get('LLM_PROVIDER', 'anthropic').lower()
    api_key = get_api_key()

    if not api_key:
        logger.warning("No LLM API key configured - ad detection and chapter generation will be disabled")
        return False

    if provider in ('openai-compatible', 'openai', 'wrapper', 'ollama'):
        base_url = os.environ.get('OPENAI_BASE_URL', 'http://localhost:8000/v1')
        logger.info(f"Verifying LLM endpoint: {base_url}")

        try:
            client = get_llm_client(force_new=True)
            if hasattr(client, 'verify_connection'):
                if not client.verify_connection(timeout=10.0):
                    logger.error(f"LLM endpoint unreachable: {base_url}")
                    logger.error("Ad detection and chapter generation will fail until this is resolved")
                    return False
            return True
        except Exception as e:
            logger.error(f"LLM endpoint verification failed: {e}")
            return False
    else:
        # For Anthropic, just verify API key is present
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
