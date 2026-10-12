"""Provider budget, model, whisper and prompt settings routes."""

import logging
from decimal import Decimal, DecimalException, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from flask import request
from api import (
    api, log_request, json_response, error_response,
    get_database, _enrich_models_with_pricing, limiter,
)
from config import (
    OPENROUTER_ROUTER_ALIASES,
    PROVIDER_OPENROUTER, SYSTEMONE_PROVIDERS,
    PROVIDERS_NON_ANTHROPIC, DEFAULT_OPENAI_BASE_URL,
)
from ad_detector import AdDetector
from database.settings import SETTINGS_REGISTRY
from transcriber.settings import _get_chunk_settings
from transcriber.remote import probe_whisper_health
from whisper_pool import get_pool
from llm_client import (
    get_effective_provider, get_llm_client, create_client_for_provider, get_client_for_provider,
)
from llm_route import (
    ALL_CREDENTIAL_SLOTS, SLOT_FAILOVER, SLOT_PRIMARY, SLOT_SECONDARY,
)
import failover
from utils.time import utc_now_iso
from fx_rates import FxRateError, get_currencies, get_usd_rate
from api.settings import common

logger = logging.getLogger('podcast.api')


@api.route('/settings/provider-budget', methods=['GET'])
@log_request
def get_provider_budget():
    return json_response(_provider_budget_payload())


def _provider_budget_payload():
    db = get_database()
    provider = db.get_setting('llm_provider') or 'anthropic'
    currency = (db.get_setting('provider_budget_display_currency') or 'USD').upper()
    rate = db.get_setting('provider_budget_fx_rate') or '1'
    try:
        local_per_usd = Decimal(rate)
        if not local_per_usd.is_finite() or local_per_usd <= 0:
            raise InvalidOperation
    except InvalidOperation:
        local_per_usd = Decimal('1')
        rate = '1'
    return {
        'enabled': db.get_setting_bool('provider_budget_enabled', False),
        'dailyLimitMicrousd': db.get_setting_int(
            'provider_budget_daily_limit_microusd', 0
        ),
        'maxReservations': db.get_setting_int(
            'provider_budget_max_reservations', 1
        ),
        'unknownCost': db.get_setting('provider_budget_unknown_cost') or 'deny',
        'unknownReserveMicrousd': db.get_setting_int(
            'provider_budget_unknown_reserve_microusd', 0
        ),
        'displayCurrency': currency,
        'dailyLimit': _microusd_to_local(
            db.get_setting_int('provider_budget_daily_limit_microusd', 0), local_per_usd),
        'unknownReserve': _microusd_to_local(
            db.get_setting_int('provider_budget_unknown_reserve_microusd', 0), local_per_usd),
        'fxRate': {
            'localPerUsd': rate,
            'source': 'Identity' if currency == 'USD' else 'Frankfurter',
            'sourceDate': db.get_setting('provider_budget_fx_source_date') or None,
            'fetchedAt': db.get_setting('provider_budget_fx_fetched_at') or None,
        },
        'status': db.provider_budget_status(provider),
    }


@api.route('/settings/provider-budget/currencies', methods=['GET'])
@log_request
def get_provider_budget_currencies():
    try:
        return json_response({'currencies': get_currencies()})
    except FxRateError as exc:
        return error_response(str(exc), 503)


@api.route('/settings/provider-budget/rate/<currency>', methods=['GET'])
@log_request
def get_provider_budget_rate(currency):
    try:
        rate = get_usd_rate(currency)
    except FxRateError as exc:
        return error_response(str(exc), 503)
    try:
        from_currency = request.args.get('from')
        if from_currency:
            from_rate = _preview_rate(request.args.get('fromRate', ''))
            daily_limit = _convert_local_amount(
                request.args.get('dailyLimit', ''), from_rate, rate.local_per_usd)
            unknown_reserve = _convert_local_amount(
                request.args.get('unknownReserve', ''), from_rate, rate.local_per_usd)
        else:
            db = get_database()
            daily_limit = _microusd_to_local(
                db.get_setting_int('provider_budget_daily_limit_microusd', 0), rate.local_per_usd)
            unknown_reserve = _microusd_to_local(
                db.get_setting_int('provider_budget_unknown_reserve_microusd', 0), rate.local_per_usd)
        return json_response({
            'currency': rate.currency,
            'localPerUsd': str(rate.local_per_usd),
            'source': rate.source,
            'sourceDate': rate.source_date,
            'dailyLimit': daily_limit,
            'unknownReserve': unknown_reserve,
        })
    except FxRateError as exc:
        return error_response(str(exc), 400)


def _local_amount_to_microusd(value, rate: Decimal, rounding) -> int:
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError('Currency amounts must be decimal strings')
    try:
        amount = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError('Currency amount must be a valid decimal') from exc
    if (not amount.is_finite() or amount < 0
            or (not amount.is_zero() and not -18 <= amount.adjusted() <= 18)):
        raise ValueError('Currency amount must be a non-negative finite decimal')
    try:
        converted = (amount * Decimal(1_000_000) / rate).to_integral_value(rounding=rounding)
    except (DecimalException, OverflowError) as exc:
        raise ValueError('Currency amount is too large') from exc
    result = int(converted)
    if amount > 0 and result == 0:
        raise ValueError('Currency amount is too small to enforce')
    if result > 1_000_000_000_000:
        raise ValueError('Currency amount is too large')
    return result


def _microusd_to_local(microusd: int, rate: Decimal) -> str:
    return format(Decimal(microusd) * rate / Decimal(1_000_000), 'f')


def _convert_local_amount(value: str, from_rate: Decimal, to_rate: Decimal) -> str:
    if not isinstance(value, str) or len(value) > 64:
        raise FxRateError('Currency amount is invalid')
    try:
        amount = Decimal(value)
        if (not amount.is_finite() or amount < 0
                or (not amount.is_zero() and not -18 <= amount.adjusted() <= 18)):
            raise ValueError
        return format(amount * to_rate / from_rate, 'f')
    except (DecimalException, ValueError, OverflowError) as exc:
        raise FxRateError('Currency amount is invalid') from exc


def _preview_rate(value: str) -> Decimal:
    if not isinstance(value, str) or len(value) > 64:
        raise FxRateError('Currency rate is invalid')
    try:
        rate = Decimal(value)
        if not rate.is_finite() or rate <= 0 or not -18 <= rate.adjusted() <= 18:
            raise ValueError
        return rate
    except (DecimalException, ValueError, OverflowError) as exc:
        raise FxRateError('Currency rate is invalid') from exc


@api.route('/settings/provider-budget', methods=['PUT'])
@limiter.limit('10 per hour')
@log_request
def update_provider_budget():
    data = request.get_json(silent=True) or {}
    enabled = data.get('enabled')
    limit = data.get('dailyLimitMicrousd')
    maximum = data.get('maxReservations')
    action = data.get('unknownCost')
    reserve = data.get('unknownReserveMicrousd')
    currency = data.get('displayCurrency')
    if not isinstance(enabled, bool):
        return error_response('enabled must be a boolean', 400)
    if currency is None and (not isinstance(limit, int) or limit < 0):
        return error_response('dailyLimitMicrousd must be a non-negative integer', 400)
    if not isinstance(maximum, int) or not 1 <= maximum <= 64:
        return error_response('maxReservations must be between 1 and 64', 400)
    if action not in ('deny', 'allow', 'reserve'):
        return error_response('unknownCost must be deny, allow, or reserve', 400)
    if currency is None and (not isinstance(reserve, int) or reserve < 0):
        return error_response('unknownReserveMicrousd must be a non-negative integer', 400)
    if currency is None and enabled and action == 'reserve' and reserve == 0:
        return error_response('unknownReserveMicrousd must be positive for reserve', 400)
    if currency is not None:
        if not isinstance(currency, str) or len(currency) != 3 or not currency.isascii() or not currency.isalpha():
            return error_response('displayCurrency must be an ISO currency code', 400)
        daily_local = data.get('dailyLimit')
        reserve_local = data.get('unknownReserve')
        if not isinstance(daily_local, str) or not isinstance(reserve_local, str):
            return error_response('dailyLimit and unknownReserve must be decimal strings', 400)
        try:
            fx_rate = get_usd_rate(currency)
            limit = _local_amount_to_microusd(
                daily_local, fx_rate.local_per_usd, ROUND_FLOOR)
            reserve = _local_amount_to_microusd(
                reserve_local, fx_rate.local_per_usd, ROUND_CEILING)
        except FxRateError as exc:
            return error_response(str(exc), 503)
        except ValueError as exc:
            return error_response(str(exc), 400)
        if enabled and action == 'reserve' and reserve == 0:
            return error_response('unknownReserve must be positive for reserve', 400)
    db = get_database()
    values = {
        'provider_budget_enabled': str(enabled).lower(),
        'provider_budget_daily_limit_microusd': str(limit),
        'provider_budget_max_reservations': str(maximum),
        'provider_budget_unknown_cost': action,
        'provider_budget_unknown_reserve_microusd': str(reserve),
    }
    if currency is not None:
        values.update({
            'provider_budget_display_currency': fx_rate.currency,
            'provider_budget_fx_rate': str(fx_rate.local_per_usd),
            'provider_budget_fx_source_date': fx_rate.source_date,
            'provider_budget_fx_fetched_at': utc_now_iso(),
        })
    with db.transaction(immediate=True) as conn:
        for key, value in values.items():
            if value is None:
                conn.execute("DELETE FROM settings WHERE key = ?", (key,))
            else:
                db._upsert_setting(conn, key, value, is_default=False)
    return json_response(_provider_budget_payload())


@api.route('/settings/prompts/reset', methods=['POST'])
@log_request
def reset_prompts_only():
    """Reset only the prompts to defaults (not models or other settings)."""
    db = get_database()

    db.reset_setting('system_prompt')
    db.reset_setting('verification_prompt')
    db.reset_setting('review_prompt')
    db.reset_setting('resurrect_prompt')
    db.reset_setting('chapter_prompt')
    db.reset_setting('pattern_cleanup_prompt')

    # Clear per-pass overrides too (empty is the no-override default state).
    for key in ('system_prompt_override', 'verification_prompt_override',
                'review_prompt_override', 'resurrect_prompt_override',
                'chapter_prompt_override'):
        db.set_setting(key, '', is_default=True)

    logger.info("Reset prompts to defaults")
    return json_response({'message': 'Prompts reset to defaults'})


@api.route('/settings/prompts/<name>/reset', methods=['POST'])
@log_request
def reset_single_prompt(name):
    """Reset one prompt (and its override) to default; per-key twin of reset_prompts_only."""
    from database import (
        DEFAULT_SYSTEM_PROMPT, DEFAULT_VERIFICATION_PROMPT,
        DEFAULT_REVIEW_PROMPT, DEFAULT_RESURRECT_PROMPT,
        DEFAULT_CHAPTER_PROMPT, DEFAULT_PATTERN_CLEANUP_PROMPT,
    )
    defaults = {
        'system': DEFAULT_SYSTEM_PROMPT,
        'verification': DEFAULT_VERIFICATION_PROMPT,
        'review': DEFAULT_REVIEW_PROMPT,
        'resurrect': DEFAULT_RESURRECT_PROMPT,
        'chapter': DEFAULT_CHAPTER_PROMPT,
        'pattern_cleanup': DEFAULT_PATTERN_CLEANUP_PROMPT,
    }
    if name not in defaults:
        return error_response('unknown prompt name', 404)

    db = get_database()
    prompt_key = f'{name}_prompt'
    db.reset_setting(prompt_key)
    if f'{name}_prompt_override' in SETTINGS_REGISTRY:
        db.set_setting(f'{name}_prompt_override', '', is_default=True)
    logger.info(f"Reset {prompt_key} to default")

    settings = common._settings_view(db.get_all_settings())
    value = common._setting_value(settings, prompt_key, defaults[name]) or defaults[name]
    return json_response({
        'value': value,
        'isDefault': common._setting_is_default(settings, prompt_key),
    })


def _ensure_openrouter_aliases_present(models: list) -> None:
    """Prepend OpenRouter router aliases (openrouter/free, openrouter/auto) that
    aren't already in the list. These are valid model IDs but are not returned by
    /api/v1/models, so they'd otherwise never appear in the dropdown.
    """
    existing_ids = {m.get('id') for m in models}
    missing = [
        {'id': alias_id, 'name': alias_name, 'created': None}
        for alias_id, alias_name in OPENROUTER_ROUTER_ALIASES
        if alias_id not in existing_ids
    ]
    models[:0] = missing


def _current_provider_models():
    """Current provider's model list, with OpenRouter aliases and pricing."""
    models = AdDetector().get_available_models()
    if get_effective_provider() == PROVIDER_OPENROUTER:
        _ensure_openrouter_aliases_present(models)
    _enrich_models_with_pricing(models)
    return models


def _models_from_client(client, provider: str) -> list:
    """Catalog rows for a built client, with OpenRouter aliases and pricing.

    An unreachable or unauthenticated provider yields an empty list rather
    than an error: the UI previews providers before their key is saved.
    """
    models = []
    if client:
        try:
            models = [
                {'id': m.id, 'name': m.name, 'created': m.created}
                for m in client.list_models()
            ]
        except ValueError as e:
            logger.info(f"Provider '{provider}' preview unavailable: {e}")
        except Exception as e:
            logger.error(f"Failed to list models for provider '{provider}': {e}")
    if provider == PROVIDER_OPENROUTER:
        _ensure_openrouter_aliases_present(models)
    _enrich_models_with_pricing(models)
    return models


def _secondary_slot_base_url(db, provider: str) -> str | None:
    """Non-secret endpoint for a preview client built against the secondary
    slot. Only configurable-endpoint types have one; anthropic/openrouter
    use their fixed public URL regardless of slot."""
    if provider in PROVIDERS_NON_ANTHROPIC:
        return db.get_setting('secondary_provider_base_url') or DEFAULT_OPENAI_BASE_URL
    return None


def _failover_slot_base_url(provider: str) -> str | None:
    """Non-secret endpoint for a preview client built against the LLM
    failover slot (#806). Only configurable-endpoint types have one;
    anthropic/openrouter use their fixed public URL regardless of slot."""
    if provider in PROVIDERS_NON_ANTHROPIC:
        return failover.failover_llm_config()['base_url']
    return None


@api.route('/settings/models', methods=['GET'])
@log_request
def get_available_models():
    """Get list of available models for the current or requested provider.

    Accepts optional ?provider= query param to preview models for a different
    provider before saving settings, and ?slot=secondary to preview them
    using the secondary provider slot's own credentials and base URL instead
    of the primary slot's (the default).
    """
    provider_override = request.args.get('provider')
    slot = request.args.get('slot', SLOT_PRIMARY)
    slot = common.SLOT_ALIASES.get(slot, slot)
    if slot not in ALL_CREDENTIAL_SLOTS:
        return error_response(f'slot must be one of: {", ".join(ALL_CREDENTIAL_SLOTS)}', 400)

    if provider_override:
        if provider_override not in common.VALID_LLM_PROVIDERS:
            return error_response(
                f'provider must be one of: {", ".join(common.VALID_LLM_PROVIDERS)}', 400
            )
        if slot == SLOT_FAILOVER and provider_override in SYSTEMONE_PROVIDERS:
            return error_response('Failover LLM is chat-only', 400)
        if slot == SLOT_SECONDARY:
            db = get_database()
            client = create_client_for_provider(
                provider_override, credential_slot=SLOT_SECONDARY,
                base_url=_secondary_slot_base_url(db, provider_override))
        elif slot == SLOT_FAILOVER:
            client = create_client_for_provider(
                provider_override, credential_slot=SLOT_FAILOVER,
                base_url=_failover_slot_base_url(provider_override))
        else:
            client = create_client_for_provider(provider_override)
        models = _models_from_client(client, provider_override)
    else:
        models = _current_provider_models()

    return json_response({'models': models})


@api.route('/settings/models/refresh', methods=['POST'])
@log_request
def refresh_models():
    """Force refresh the model list for one credential slot.

    Optional body {"slot": "primary"|"secondary"}; primary by default, so a
    bodyless call behaves as before. ``force_new=True`` rebuilds that slot's
    client and clears ``_model_list_cache`` in llm_client, so the next
    ``list_models()`` call repopulates from upstream.
    """
    data = request.get_json(silent=True)
    slot = data.get('slot', SLOT_PRIMARY) if isinstance(data, dict) else SLOT_PRIMARY
    slot = common.SLOT_ALIASES.get(slot, slot)
    if slot not in ALL_CREDENTIAL_SLOTS:
        return error_response(f'slot must be one of: {", ".join(ALL_CREDENTIAL_SLOTS)}', 400)

    if slot == SLOT_SECONDARY:
        db = get_database()
        provider = db.get_setting('secondary_provider')
        if not provider:
            return error_response(
                'No secondary provider configured; set secondaryProvider first', 400)
        client = get_client_for_provider(
            provider, base_url=_secondary_slot_base_url(db, provider),
            credential_slot=SLOT_SECONDARY, force_new=True)
        models = _models_from_client(client, provider)
    elif slot == SLOT_FAILOVER:
        provider = failover.failover_llm_config()['provider']
        if not provider:
            return error_response(
                'No failover provider configured; set failoverLlmProvider first', 400)
        client = get_client_for_provider(
            provider, base_url=_failover_slot_base_url(provider),
            credential_slot=SLOT_FAILOVER, force_new=True)
        models = _models_from_client(client, provider)
    else:
        get_llm_client(force_new=True)
        models = _current_provider_models()

    logger.info(f"Refreshed {slot} model list: {len(models)} models available")
    return json_response({'models': models, 'count': len(models)})


@api.route('/settings/whisper-models', methods=['GET'])
@log_request
def get_whisper_models():
    """Get list of available Whisper models with resource requirements."""
    models = [
        {
            'id': 'tiny',
            'name': 'Tiny',
            'vram': '~1GB',
            'speed': '~1 min/60min',
            'quality': 'Basic'
        },
        {
            'id': 'base',
            'name': 'Base',
            'vram': '~1GB',
            'speed': '~1.5 min/60min',
            'quality': 'Good'
        },
        {
            'id': 'small',
            'name': 'Small (Default)',
            'vram': '~2GB',
            'speed': '~2-3 min/60min',
            'quality': 'Better'
        },
        {
            'id': 'medium',
            'name': 'Medium',
            'vram': '~4GB',
            'speed': '~4-5 min/60min',
            'quality': '~15% better than Small'
        },
        {
            'id': 'large-v3',
            'name': 'Large v3',
            'vram': '~5-6GB',
            'speed': '~6-8 min/60min',
            'quality': '~25% better than Small'
        }
    ]
    return json_response({'models': models})


@api.route('/settings/whisper/capacity', methods=['GET'])
@log_request
def get_whisper_capacity():
    """Resolved Whisper pool: what is configured, what is active, what is in flight.

    Refreshes only this worker's pool; the leader's dispatcher refreshes its
    own pool every pass (5s settings TTL), so no cross-process signal needed.
    """
    pool = get_pool()
    pool.refresh(force=True)
    snap = pool.snapshot()
    configured = _get_chunk_settings()['concurrent_chunks']
    effective = pool.chunk_workers(configured)
    worst = snap['maxEpisodes']['effective'] * configured
    # Probe only while the pool is active (enabled and on the API backend),
    # so disabling it or switching to local stops polling a stale URL.
    health = probe_whisper_health() if snap['active'] else {'available': False}
    snap.update({
        'chunkWorkers': {'configured': configured, 'effective': effective},
        'worstCaseInFlight': worst,
        'exceedsCapacity': bool(snap['active'] and worst > snap['capacity']),
        'health': health,
    })
    return json_response(snap)
