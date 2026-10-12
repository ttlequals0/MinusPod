"""Provider, failover, System One and whisper settings phases."""

import logging
import math
import os
import threading
from urllib.parse import urlsplit
from api import json_response, error_response
from config import (
    WHISPER_BACKENDS,
    WHISPER_COMPUTE_TYPES,
    PROVIDER_OPENAI_COMPATIBLE, PROVIDER_OLLAMA,
    SYSTEMONE_PROVIDERS,
    WHISPER_API_TIMEOUT_MIN, WHISPER_API_TIMEOUT_MAX,
    coerce_bool_setting,
)
from rate_limit_hold import clear_holds_for_provider_change
from pricing_fetcher import force_refresh_pricing
from whisper_pool import get_pool
from llm_client import (
    get_effective_provider,
    get_effective_base_url,
    get_llm_client,
    _JSON_FORMAT_SETTING_KEY,
    _JSON_SCHEMA_SETTING_KEY,
    invalidate_provider_cache,
    reset_schema_probe_memo,
)
from llm_route import (
    VALID_SLOTS, SAME_AS_DETECTION, SLOT_SECONDARY,
)
import failover
from utils.language import LANGUAGE_CODE_RE
from utils.url import (
    BASE_URL_USERINFO_ERROR, SSRFError, url_has_userinfo, validate_base_url,
)
from utils.constants import ALLOWED_URL_SCHEMES
from utils.secret_writes import SecretWriteRejected, set_or_clear_secret
from api.settings import common

logger = logging.getLogger('podcast.api')


VALID_FAILOVER_LLM_PROVIDERS = tuple(
    provider for provider in common.VALID_LLM_PROVIDERS if provider not in SYSTEMONE_PROVIDERS)


PRICING_SOURCE_MODES = ('auto', 'litellm', 'free')


def _mark_whisper_for_reload():
    """Next transcription reloads the Whisper model."""
    try:
        from transcriber.local import WhisperModelSingleton
        from parakeet_transcriber import ParakeetModelSingleton
        WhisperModelSingleton.mark_for_reload()
        ParakeetModelSingleton.mark_for_reload()
    except Exception as e:
        logger.warning(f"Could not mark Whisper model for reload: {e}")


def _refresh_whisper_pool():
    """Pick up the committed pool/backend configuration."""
    get_pool().refresh(force=True)


def _provider_account_identity(db, slot):
    if slot == SLOT_SECONDARY:
        provider = db.get_setting('secondary_provider') or ''
        base_url = db.get_setting('secondary_provider_base_url')
        base_url = base_url if base_url is not None else ''
    else:
        provider = db.get_setting('llm_provider') or get_effective_provider()
        base_url = db.get_setting('openai_base_url')
        base_url = base_url if base_url is not None else get_effective_base_url()
    if provider not in (PROVIDER_OLLAMA, PROVIDER_OPENAI_COMPATIBLE):
        base_url = ''
    return slot, provider, base_url


def _failover_llm_identity(db):
    provider = db.get_setting('failover_llm_provider') or ''
    base_url = db.get_setting('failover_llm_base_url') or ''
    if provider not in (PROVIDER_OLLAMA, PROVIDER_OPENAI_COMPATIBLE):
        base_url = ''
    return provider, base_url


def _apply_provider_routing_fields(db, data):
    """Persist per-phase LLM provider slot overrides (see llm_route.py).

    Each stage picks a SLOT (primary/secondary), not a provider type. An
    empty value clears the override so the phase falls back to its default
    routing: detection falls back to primary, verification/chapters fall
    back to detection's resolved slot (same_as_detection is also accepted
    explicitly, for symmetry with reviewProvider's same_as_pass).
    """
    values, issue = _provider_routing_values(data)
    if issue is not None:
        return error_response(*issue)
    for db_key, value in values:
        if value is None:
            db.clear_setting(db_key)
            logger.info(f"Cleared {db_key} (falls back to default routing)")
        else:
            db.set_setting(db_key, value, is_default=False)
            logger.info(f"Updated {db_key} to: {value}")
    return None


def _provider_routing_values(data):
    values = []
    for payload_key, db_key, valid in (
        ('detectionProvider', 'detection_provider', VALID_SLOTS),
        ('verificationProvider', 'verification_provider',
         VALID_SLOTS + (SAME_AS_DETECTION,)),
        ('chaptersProvider', 'chapters_provider', VALID_SLOTS + (SAME_AS_DETECTION,)),
    ):
        if payload_key not in data:
            continue
        value = data[payload_key]
        if not value:
            values.append((db_key, None))
        elif value in valid:
            values.append((db_key, value))
        else:
            return None, (f'{payload_key} must be one of: {", ".join(valid)}', 400)
    return values, None


def _validate_provider_routing_payload(data):
    _values, issue = _provider_routing_values(data)
    return issue


def _apply_provider_rate_limit_fields(db, data):
    """Persist the manual per-provider request-rate caps (#747).

    Validates every field before writing any, so a 400 never leaves part of
    the payload persisted. 0 means unlimited.
    """
    writes, issue = _provider_rate_limit_writes(data)
    if issue is not None:
        return error_response(*issue)
    for db_key, value in writes:
        db.set_setting(db_key, str(value), is_default=False)
        logger.info(f"Updated {db_key} to: {value}")


def _provider_rate_limit_writes(data):
    fields = (
        ('providerRequestsPerMin', 'provider_requests_per_min'),
        ('providerRequestsPerDay', 'provider_requests_per_day'),
        ('secondaryProviderRequestsPerMin', 'secondary_provider_requests_per_min'),
        ('secondaryProviderRequestsPerDay', 'secondary_provider_requests_per_day'),
        ('providerTokensPerMin', 'provider_tokens_per_min'),
        ('secondaryProviderTokensPerMin', 'secondary_provider_tokens_per_min'),
    )
    writes = []
    for payload_key, db_key in fields:
        if payload_key not in data:
            continue
        try:
            n = int(data[payload_key])
        except (TypeError, ValueError):
            return None, (f'{payload_key} must be an integer', 400)
        if n < 0:
            return None, (f'{payload_key} must be at least 0', 400)
        writes.append((db_key, n))
    return writes, None


def _validate_provider_rate_limit_payload(data):
    _writes, issue = _provider_rate_limit_writes(data)
    return issue


def _clear_format_probes(db) -> None:
    """Forget every response_format probe answer, stored and in-process."""
    db.set_setting(_JSON_FORMAT_SETTING_KEY, '', is_default=True)
    db.clear_setting(_JSON_SCHEMA_SETTING_KEY)
    common.after_commit(reset_schema_probe_memo)
    common.after_commit(invalidate_provider_cache)


def _base_url_error(value, label):
    """Return a structured error when a base URL fails endpoint security."""
    if url_has_userinfo(value):
        return BASE_URL_USERINFO_ERROR, 400
    try:
        validate_base_url(value)
    except SSRFError as e:
        return f'Invalid {label}: {e}', 400
    return None


def _base_url_syntax_error(value, label, *, allow_blank=False):
    if not isinstance(value, str):
        return f'{label} must be a string', 400
    value = value.strip()
    if not value:
        if allow_blank:
            return None
        return f'Invalid {label}: Empty URL', 400
    if url_has_userinfo(value):
        return BASE_URL_USERINFO_ERROR, 400
    try:
        parts = urlsplit(value)
        _ = parts.port
    except ValueError as exc:
        return f'Invalid {label}: {exc}', 400
    scheme = (parts.scheme or '').lower()
    if scheme not in ALLOWED_URL_SCHEMES:
        return f'Invalid {label}: Blocked URL scheme: {scheme!r}', 400
    if not parts.hostname:
        return f'Invalid {label}: Missing hostname in URL', 400
    return None


def _validate_provider_payload(data):
    """Validate provider value types, enums, and URL syntax without I/O."""
    if 'llmProvider' in data and data['llmProvider'] not in common.VALID_LLM_PROVIDERS:
        return f'llmProvider must be one of: {", ".join(common.VALID_LLM_PROVIDERS)}', 400
    if 'pricingSourceMode' in data and data['pricingSourceMode'] not in PRICING_SOURCE_MODES:
        return f'pricingSourceMode must be one of: {", ".join(PRICING_SOURCE_MODES)}', 400
    if 'openrouterApiKey' in data:
        if data['openrouterApiKey'] is not None and not isinstance(data['openrouterApiKey'], str):
            return 'openrouterApiKey must be a string or null', 400
        key = (data['openrouterApiKey'] or '').strip()
        if key and not key.startswith('sk-or-'):
            return 'OpenRouter API key must start with sk-or-', 400
    for key in ('typesafeApiKey', 'systemoneApiKey'):
        if key in data and data[key] is not None and not isinstance(data[key], str):
            return f'{key} must be a string or null', 400
    if 'openaiBaseUrl' in data:
        error = _base_url_syntax_error(data['openaiBaseUrl'], 'base URL')
        if error is not None:
            return error
    if data.get('secondaryProvider') and data['secondaryProvider'] not in common.VALID_LLM_PROVIDERS:
        return f'secondaryProvider must be one of: {", ".join(common.VALID_LLM_PROVIDERS)}', 400
    if 'secondaryProviderBaseUrl' in data:
        value = data['secondaryProviderBaseUrl']
        if not isinstance(value, str):
            return 'secondaryProviderBaseUrl must be a string', 400
        if value.strip():
            error = _base_url_syntax_error(
                value, 'secondary provider base URL', allow_blank=True)
            if error is not None:
                return error
    if 'systemoneBaseUrl' in data:
        error = _base_url_syntax_error(
            data['systemoneBaseUrl'], 'System One base URL', allow_blank=True)
        if error is not None:
            return error
    if 'whisperApiBaseUrl' in data and data['whisperApiBaseUrl'] is not None:
        error = _base_url_syntax_error(
            data['whisperApiBaseUrl'], 'whisper API base URL', allow_blank=True)
        if error is not None:
            return error
    if data.get('failoverLlmProvider') and data['failoverLlmProvider'] not in VALID_FAILOVER_LLM_PROVIDERS:
        return f'failoverLlmProvider must be one of: {", ".join(VALID_FAILOVER_LLM_PROVIDERS)}', 400
    if 'failoverLlmBaseUrl' in data:
        value = data['failoverLlmBaseUrl']
        if not isinstance(value, str):
            return 'failoverLlmBaseUrl must be a string', 400
        if value.strip():
            error = _base_url_syntax_error(value, 'failover LLM base URL', allow_blank=True)
            if error is not None:
                return error
    if ('failoverWhisperApiBaseUrl' in data
            and data['failoverWhisperApiBaseUrl'] is not None):
        error = _base_url_syntax_error(
            data['failoverWhisperApiBaseUrl'], 'failover whisper API base URL',
            allow_blank=True)
        if error is not None:
            return error
    return None


def validate_provider_endpoint_security(data):
    """Resolve configured provider endpoints before acquiring the write lock."""
    issue = _validate_provider_payload(data)
    if issue is not None:
        return issue
    for key, label, allow_blank in (
        ('openaiBaseUrl', 'base URL', False),
        ('secondaryProviderBaseUrl', 'secondary provider base URL', True),
        ('systemoneBaseUrl', 'System One base URL', True),
        ('whisperApiBaseUrl', 'whisper API base URL', True),
        ('failoverLlmBaseUrl', 'failover LLM base URL', True),
        ('failoverWhisperApiBaseUrl', 'failover whisper API base URL', True),
    ):
        if key not in data:
            continue
        value = data[key]
        if allow_blank and (value is None
                            or isinstance(value, str) and not value.strip()):
            continue
        issue = _base_url_error(value, label)
        if issue is not None:
            return issue
    return None


def _native_timeout_field(db, data, key):
    provider_key = {'providerATimeoutSeconds': ('llmProvider', 'llm_provider'),
                    'providerBTimeoutSeconds': ('secondaryProvider', 'secondary_provider')}.get(key)
    if provider_key is None:
        return False
    payload_key, setting_key = provider_key
    provider = data.get(payload_key, db.get_setting(setting_key) if db else None)
    if provider is None and setting_key == 'llm_provider':
        provider = os.environ.get('LLM_PROVIDER')
    return provider in SYSTEMONE_PROVIDERS


def _validate_failover_settings_payload(data, db=None):
    for key in ('failoverLlmEnabled', 'failoverWhisperEnabled'):
        if key in data and not isinstance(data[key], bool):
            return f'{key} must be a boolean', 400

    optional_integers = (
        'failoverLlmTimeoutSeconds', 'failoverLlmMaxRetries',
        'failoverWhisperMaxAttempts',
        'providerATimeoutSeconds', 'providerAMaxRetries',
        'providerBTimeoutSeconds', 'providerBMaxRetries',
    )
    for key in optional_integers:
        if key not in data or data[key] is None or data[key] == '':
            continue
        if _native_timeout_field(db, data, key):
            value = data[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                return f'{key} must be a finite number greater than zero or blank', 400
        elif type(data[key]) is not int:
            return f'{key} must be an integer or blank', 400

    for key in ('failoverWhisperApiTimeoutSeconds',
                'failoverProbeIntervalMinutes', 'failoverRecoveryProbes',
                'whisperApiTimeoutSeconds', 'whisperMaxAttempts'):
        if key in data and type(data[key]) is not int:
            return f'{key} must be an integer', 400

    string_fields = (
        'failoverLlmBaseUrl', 'failoverLlmDetectionModel', 'failoverLlmReviewModel',
        'failoverLlmVerificationModel', 'failoverLlmChaptersModel',
        'failoverWhisperBackend', 'failoverWhisperModel', 'failoverWhisperApiModel',
        'failoverWhisperLanguage',
    )
    for key in string_fields:
        if key in data and not isinstance(data[key], str):
            return f'{key} must be a string', 400

    nullable_strings = (
        'failoverLlmProvider', 'failoverLlmApiKey', 'failoverWhisperApiBaseUrl',
        'failoverWhisperApiKey',
    )
    for key in nullable_strings:
        if key in data and data[key] is not None and not isinstance(data[key], str):
            return f'{key} must be a string or null', 400
    for key in ('failoverLlmDetectionModel', 'failoverLlmReviewModel',
                'failoverLlmVerificationModel', 'failoverLlmChaptersModel'):
        if key in data and len(data[key]) > 200:
            return f'{key} must be a string of at most 200 characters', 400
    if ('failoverWhisperBackend' in data
            and data['failoverWhisperBackend'] not in WHISPER_BACKENDS):
        return (
            'failoverWhisperBackend must be one of: '
            f'{", ".join(WHISPER_BACKENDS)}', 400)
    if ('failoverWhisperModel' in data and len(data['failoverWhisperModel']) > 200):
        return 'failoverWhisperModel must be at most 200 characters', 400
    if 'failoverWhisperApiModel' in data:
        model = data['failoverWhisperApiModel'].strip()
        if not model or len(model) > 200:
            return 'failoverWhisperApiModel must be a non-empty string (max 200 chars)', 400
    if 'failoverWhisperApiTimeoutSeconds' in data:
        timeout = data['failoverWhisperApiTimeoutSeconds']
        if not WHISPER_API_TIMEOUT_MIN <= timeout <= WHISPER_API_TIMEOUT_MAX:
            return (
                'failoverWhisperApiTimeoutSeconds must be between '
                f'{WHISPER_API_TIMEOUT_MIN} and {WHISPER_API_TIMEOUT_MAX}', 400)
    if 'failoverWhisperLanguage' in data:
        language = data['failoverWhisperLanguage'].strip().lower()
        if language and language != 'auto' and not LANGUAGE_CODE_RE.match(language):
            return "failoverWhisperLanguage must be '', 'auto', or a 2-3 letter language code", 400
    for key, low, high in (
        ('failoverLlmTimeoutSeconds', 10, 3600),
        ('failoverLlmMaxRetries', 0, 10),
        ('failoverWhisperMaxAttempts', 1, 10),
        ('providerATimeoutSeconds', 10, 3600),
        ('providerAMaxRetries', 0, 10),
        ('providerBTimeoutSeconds', 10, 3600),
        ('providerBMaxRetries', 0, 10),
        ('failoverProbeIntervalMinutes', 1, 60),
        ('failoverRecoveryProbes', 1, 10),
        ('whisperApiTimeoutSeconds', WHISPER_API_TIMEOUT_MIN, WHISPER_API_TIMEOUT_MAX),
        ('whisperMaxAttempts', 1, 10),
    ):
        if (key in data and data[key] is not None and data[key] != ''
                and not _native_timeout_field(db, data, key)
                and not low <= data[key] <= high):
            return f'{key} must be between {low} and {high}', 400
    if 'whisperBackend' in data and data['whisperBackend'] not in WHISPER_BACKENDS:
        return (
            f'whisperBackend must be one of: {", ".join(WHISPER_BACKENDS)}', 400)
    if 'whisperApiModel' in data:
        model = str(data['whisperApiModel']).strip()
        if not model or len(model) > 200:
            return 'whisperApiModel must be a non-empty string (max 200 chars)', 400
    if 'whisperLanguage' in data:
        language = str(data['whisperLanguage']).strip().lower()
        if language and language != 'auto' and not LANGUAGE_CODE_RE.match(language):
            return "whisperLanguage must be 'auto' or a 2-3 letter language code (e.g. 'en', 'fi', 'pt')", 400
    if ('whisperComputeType' in data
            and str(data['whisperComputeType']).strip() not in WHISPER_COMPUTE_TYPES):
        return f'whisperComputeType must be one of: {", ".join(WHISPER_COMPUTE_TYPES)}', 400
    return None


def _apply_provider_fields(db, data):
    """Persist provider changes, then refresh runtime state and affected rate-limit holds."""
    provider_changed = False
    # Narrower than provider_changed: pricing mode is not a new account.
    credentials_changed = False
    # Resolved before any write: a hold belongs to the provider that was
    # configured when the 429 landed, not to the one being saved.
    affected_providers = {db.get_setting('llm_provider') or get_effective_provider()}
    # Field values were checked by _validate_provider_payload before any
    # applier ran, so this phase only writes.
    if 'llmProvider' in data:
        db.set_setting('llm_provider', data['llmProvider'], is_default=False)
        logger.info(f"Updated LLM provider to: {data['llmProvider']}")
        affected_providers.add(data['llmProvider'])
        provider_changed = True
        credentials_changed = True

    if 'openaiBaseUrl' in data:
        db.set_setting('openai_base_url', data['openaiBaseUrl'], is_default=False)
        logger.info(f"Updated OpenAI base URL to: {data['openaiBaseUrl']}")
        provider_changed = True
        credentials_changed = True

    if 'pricingSourceMode' in data:
        db.set_setting('pricing_source_mode', data['pricingSourceMode'], is_default=False)
        logger.info(f"Updated pricing source mode to: {data['pricingSourceMode']}")
        provider_changed = True

    if 'openrouterApiKey' in data:
        try:
            set_or_clear_secret(db, 'openrouter_api_key',
                                (data['openrouterApiKey'] or '').strip())
        except SecretWriteRejected:
            return error_response('provider_crypto_unavailable', 409)
        logger.info("Updated OpenRouter API key")
        provider_changed = True
        credentials_changed = True

    if credentials_changed:
        common.after_commit(
            lambda: clear_holds_for_provider_change(
                db, 'LLM provider settings changed', affected_providers),
            bucket='holds')

    if provider_changed:
        # Clear the cached probe answers so the new endpoint gets re-probed:
        # a stored false against a model name the new endpoint also serves
        # would otherwise pin it to the fallback format forever.
        _clear_format_probes(db)
        common.after_commit(_probe_after_provider_change)
    # The TTL cache backing get_effective_base_url / get_effective_provider
    # lags writes by up to 5s. Without this invalidation, the GET /settings
    # response that fires right after this PUT returns the pre-write value,
    # the UI re-hydrates state to the stale value, hasChanges flips back to
    # false, and the Save Changes button vanishes (issue #234).
    common.after_commit(invalidate_provider_cache)
    return None


def _probe_after_provider_change():
    """Refresh endpoint-dependent probes and pricing after provider changes."""
    client = get_llm_client(force_new=True)
    if hasattr(client, 'probe_json_format_support'):
        client.probe_json_format_support()
    threading.Thread(target=force_refresh_pricing, daemon=True).start()


def _apply_secondary_provider_fields(db, data):
    """Persist the optional secondary provider: a second full provider
    config that stage settings can route to via the 'secondary' slot
    instead of the primary llmProvider config.

    Any change here can affect a request already in flight against the
    secondary slot, so it invalidates the provider cache and lifts a
    matching rate-limit hold the same way a primary provider change does.
    """
    changed = False
    # Resolved before the write below, so switching the secondary type lifts
    # the outgoing account's hold as well as the incoming one's.
    affected_providers = {db.get_setting('secondary_provider')}

    if 'secondaryProviderEnabled' in data:
        value = 'true' if bool(data['secondaryProviderEnabled']) else 'false'
        db.set_setting('secondary_provider_enabled', value, is_default=False)
        logger.info(f"Updated secondary_provider_enabled to: {value}")
        if value == 'false':
            common.after_commit(lambda: failover.cancel(failover.TARGET_LLM_SECONDARY, source='manual'))
        changed = True

    if 'secondaryProvider' in data:
        value = data['secondaryProvider']
        if not value:
            db.clear_setting('secondary_provider')
            logger.info("Cleared secondary_provider")
        else:
            db.set_setting('secondary_provider', value, is_default=False)
            logger.info(f"Updated secondary_provider to: {value}")
            affected_providers.add(value)
        changed = True

    if 'secondaryProviderBaseUrl' in data:
        value = data['secondaryProviderBaseUrl']
        if not value.strip():
            # Empty clears the override back to the registry default,
            # matching the per-phase provider routing fields above.
            db.clear_setting('secondary_provider_base_url')
            logger.info("Cleared secondary provider base URL")
        else:
            db.set_setting('secondary_provider_base_url', value, is_default=False)
            logger.info("Updated secondary provider base URL")
        changed = True

    if 'secondaryProviderApiKey' in data:
        try:
            set_or_clear_secret(db, 'secondary_provider_api_key', data['secondaryProviderApiKey'])
        except SecretWriteRejected:
            return error_response('provider_crypto_unavailable', 409)
        logger.info("Updated secondary provider API key")
        changed = True

    if changed:
        common.after_commit(invalidate_provider_cache)
        # No secondary type configured: nothing to lift, and a null provider
        # would target the unscoped blanket marker, which may be primary's.
        known = [p for p in affected_providers if p]
        common.after_commit(
            lambda: clear_holds_for_provider_change(
                db, 'secondary provider settings changed', known,
                credential_slot='secondary'),
            bucket='holds')
    return None


# Blank overrides clear the row to its inherited value.
_INT_OR_BLANK_FIELDS = {
    'failoverWhisperMaxAttempts': ('failover_whisper_max_attempts', 1, 10),
    'failoverLlmTimeoutSeconds': ('failover_llm_timeout_seconds', 10, 3600),
    'failoverLlmMaxRetries': ('failover_llm_max_retries', 0, 10),
    'providerATimeoutSeconds': ('llm_timeout_seconds', 10, 3600),
    'providerAMaxRetries': ('llm_max_retries', 0, 10),
    'providerBTimeoutSeconds': ('secondary_llm_timeout_seconds', 10, 3600),
    'providerBMaxRetries': ('secondary_llm_max_retries', 0, 10),
}


def _apply_int_or_blank(db, data, payload_key):
    """Write an optional int; None or '' clears the row. Returns an error response or None."""
    db_key, lo, hi = _INT_OR_BLANK_FIELDS[payload_key]
    value = data[payload_key]
    if value is None or value == '':
        db.clear_setting(db_key)
        return None
    if _native_timeout_field(db, data, payload_key):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            return error_response(f'{payload_key} must be a finite number greater than zero or blank', 400)
        db.set_setting(db_key, str(value), is_default=False)
        return None
    if type(value) is not int:
        return error_response(f'{payload_key} must be an integer or blank', 400)
    n = value
    if not lo <= n <= hi:
        return error_response(f'{payload_key} must be between {lo} and {hi}', 400)
    db.set_setting(db_key, str(n), is_default=False)
    return None


def _apply_provider_timeout_fields(db, data):
    """Persist the per-slot Provider A / Provider B timeout and retry overrides (#806)."""
    for payload_key in ('providerATimeoutSeconds', 'providerAMaxRetries',
                        'providerBTimeoutSeconds', 'providerBMaxRetries'):
        if payload_key in data:
            err = _apply_int_or_blank(db, data, payload_key)
            if err is not None:
                return err
    return None


def _apply_failover_llm_fields(db, data):
    """Persist LLM failover settings and clear active state when disabled."""
    changed = False
    if 'failoverLlmEnabled' in data:
        enabled = data['failoverLlmEnabled']
        db.set_setting('failover_llm_enabled', 'true' if enabled else 'false', is_default=False)
        if not enabled:
            for target in (failover.TARGET_LLM_PRIMARY, failover.TARGET_LLM_SECONDARY):
                common.after_commit(lambda t=target: failover.cancel(t, source='manual'))
        changed = True
    if 'failoverLlmProvider' in data:
        value = data['failoverLlmProvider']
        if value:
            db.set_setting('failover_llm_provider', value, is_default=False)
        else:
            db.clear_setting('failover_llm_provider')
        changed = True
    if 'failoverLlmBaseUrl' in data:
        value = (data['failoverLlmBaseUrl'] or '').strip()
        if value:
            db.set_setting('failover_llm_base_url', value, is_default=False)
        else:
            db.clear_setting('failover_llm_base_url')
        changed = True
    for payload_key, db_key in (
            ('failoverLlmDetectionModel', 'failover_llm_detection_model'),
            ('failoverLlmReviewModel', 'failover_llm_review_model'),
            ('failoverLlmVerificationModel', 'failover_llm_verification_model'),
            ('failoverLlmChaptersModel', 'failover_llm_chapters_model')):
        if payload_key in data:
            value = data[payload_key]
            if not isinstance(value, str) or len(value) > 200:
                return error_response(f'{payload_key} must be a string of at most 200 characters', 400)
            db.set_setting(db_key, value.strip(), is_default=False)
            changed = True
    for payload_key in ('failoverLlmTimeoutSeconds', 'failoverLlmMaxRetries'):
        if payload_key in data:
            err = _apply_int_or_blank(db, data, payload_key)
            if err is not None:
                return err
            changed = True
    if 'failoverLlmApiKey' in data:
        try:
            set_or_clear_secret(db, 'failover_llm_api_key', data['failoverLlmApiKey'])
        except SecretWriteRejected:
            return error_response('provider_crypto_unavailable', 409)
        changed = True
    if changed:
        common.after_commit(invalidate_provider_cache)
    return None


def _apply_failover_whisper_fields(db, data):
    """Persist transcriber failover settings and clear active state when disabled."""
    changed = False
    if 'failoverWhisperMaxAttempts' in data:
        err = _apply_int_or_blank(db, data, 'failoverWhisperMaxAttempts')
        if err is not None:
            return err
        changed = True
    if 'failoverWhisperEnabled' in data:
        enabled = data['failoverWhisperEnabled']
        db.set_setting('failover_whisper_enabled', 'true' if enabled else 'false', is_default=False)
        if not enabled:
            common.after_commit(lambda: failover.cancel(failover.TARGET_WHISPER, source='manual'))
        changed = True
    if 'failoverWhisperBackend' in data:
        if data['failoverWhisperBackend'] not in WHISPER_BACKENDS:
            return error_response(
                f'failoverWhisperBackend must be one of: {", ".join(WHISPER_BACKENDS)}', 400)
        db.set_setting('failover_whisper_backend', data['failoverWhisperBackend'], is_default=False)
        changed = True
    if 'failoverWhisperModel' in data:
        value = data['failoverWhisperModel']
        if len(value) > 200:
            return error_response('failoverWhisperModel must be at most 200 characters', 400)
        db.set_setting('failover_whisper_model', value.strip(), is_default=False)
        changed = True
    if 'failoverWhisperApiBaseUrl' in data:
        # Checked by _validate_provider_payload before the transaction opens.
        value = (data['failoverWhisperApiBaseUrl'] or '').strip()
        db.set_setting('failover_whisper_api_base_url', value, is_default=False)
        changed = True
    if 'failoverWhisperApiModel' in data:
        model_val = data['failoverWhisperApiModel'].strip()
        if not model_val or len(model_val) > 200:
            return error_response(
                'failoverWhisperApiModel must be a non-empty string (max 200 chars)', 400)
        db.set_setting('failover_whisper_api_model', model_val, is_default=False)
        changed = True
    if 'failoverWhisperApiTimeoutSeconds' in data:
        value = data['failoverWhisperApiTimeoutSeconds']
        if type(value) is not int:
            return error_response('failoverWhisperApiTimeoutSeconds must be an integer', 400)
        if not 30 <= value <= 3600:
            return error_response(
                'failoverWhisperApiTimeoutSeconds must be between 30 and 3600', 400)
        db.set_setting('failover_whisper_api_timeout_seconds', str(value), is_default=False)
        changed = True
    if 'failoverWhisperLanguage' in data:
        lang_val = data['failoverWhisperLanguage'].strip().lower()
        if lang_val and lang_val != 'auto' and not LANGUAGE_CODE_RE.match(lang_val):
            return error_response(
                "failoverWhisperLanguage must be '', 'auto', or a 2-3 letter language code", 400)
        db.set_setting('failover_whisper_language', lang_val, is_default=False)
        changed = True
    if 'failoverWhisperApiKey' in data:
        try:
            set_or_clear_secret(db, 'failover_whisper_api_key', data['failoverWhisperApiKey'])
        except SecretWriteRejected:
            return error_response('provider_crypto_unavailable', 409)
        changed = True
    if changed:
        common.after_commit(_refresh_whisper_pool)
    return None


def _apply_failover_policy_fields(db, data):
    """Persist failover probe and recovery settings."""
    for payload_key, db_key, lo, hi in (
            ('failoverProbeIntervalMinutes', 'failover_probe_interval_minutes', 1, 60),
            ('failoverRecoveryProbes', 'failover_recovery_probes', 1, 10)):
        if payload_key not in data:
            continue
        n = data[payload_key]
        if type(n) is not int:
            return error_response(f'{payload_key} must be an integer', 400)
        if not lo <= n <= hi:
            return error_response(f'{payload_key} must be between {lo} and {hi}', 400)
        db.set_setting(db_key, str(n), is_default=False)
    return None


def _apply_whisper_fields(db, data):
    """Persist whisper backend selection, API endpoint, key, model, language, compute type."""
    if 'whisperBackend' in data:
        if data['whisperBackend'] not in WHISPER_BACKENDS:
            return json_response(
                {'error': f'whisperBackend must be one of: {", ".join(WHISPER_BACKENDS)}'}, 400
            )
        db.set_setting('whisper_backend', data['whisperBackend'], is_default=False)
        logger.info(f"Updated whisper backend to: {data['whisperBackend']}")
        common.after_commit(_refresh_whisper_pool)

    if 'whisperApiBaseUrl' in data:
        # Checked by _validate_provider_payload before the transaction opens.
        db.set_setting('whisper_api_base_url', data['whisperApiBaseUrl'], is_default=False)
        logger.info(f"Updated whisper API base URL to: {data['whisperApiBaseUrl']}")

    if 'whisperApiKey' in data:
        try:
            set_or_clear_secret(db, 'whisper_api_key', data['whisperApiKey'])
        except SecretWriteRejected:
            return error_response('provider_crypto_unavailable', 409)
        logger.info("Updated whisper API key")

    if 'whisperApiModel' in data:
        model_val = str(data['whisperApiModel']).strip()
        if not model_val or len(model_val) > 200:
            return json_response({'error': 'whisperApiModel must be a non-empty string (max 200 chars)'}, 400)
        db.set_setting('whisper_api_model', model_val, is_default=False)
        logger.info(f"Updated whisper API model to: {model_val}")

    if 'whisperLanguage' in data:
        lang_val = str(data['whisperLanguage']).strip().lower()
        # Empty string collapses to default ('en'); 'auto' is allowed; otherwise
        # require a bare 2-3 letter language code (faster-whisper rejects subtags).
        if lang_val and lang_val != 'auto' and not LANGUAGE_CODE_RE.match(lang_val):
            return json_response({'error': "whisperLanguage must be 'auto' or a 2-3 letter language code (e.g. 'en', 'fi', 'pt')"}, 400)
        db.set_setting('whisper_language', lang_val or 'en', is_default=False)
        logger.info(f"Updated whisper language to: {lang_val or 'en'}")

    if 'whisperComputeType' in data:
        ct_val = str(data['whisperComputeType']).strip()
        if ct_val not in WHISPER_COMPUTE_TYPES:
            return json_response(
                {'error': f'whisperComputeType must be one of: {", ".join(WHISPER_COMPUTE_TYPES)}'}, 400
            )
        db.set_setting('whisper_compute_type', ct_val, is_default=False)
        common.after_commit(_mark_whisper_for_reload)
        logger.info(f"Updated whisper compute type to: {ct_val}")

    if 'skipFlacCompression' in data:
        enabled = coerce_bool_setting(data['skipFlacCompression'])
        db.set_setting('skip_flac_compression', 'true' if enabled else 'false', is_default=False)
        logger.info(f"Updated skip_flac_compression to: {enabled}")

    if 'whisperMaxAttempts' in data:
        n = data['whisperMaxAttempts']
        if type(n) is not int:
            return error_response('whisperMaxAttempts must be an integer', 400)
        if not 1 <= n <= 10:
            return error_response('whisperMaxAttempts must be between 1 and 10', 400)
        db.set_setting('whisper_max_attempts', str(n), is_default=False)
        logger.info(f"Updated whisper_max_attempts to: {n}")

    return None


def _effective_provider_after_update(data, db):
    """Return the provider the user is settling on, considering an inline change."""
    candidate = data.get('llmProvider')
    if candidate:
        return candidate.lower()
    stored = db.get_setting('llm_provider')
    if stored:
        return stored.lower()
    return os.environ.get('LLM_PROVIDER', 'anthropic').lower()
