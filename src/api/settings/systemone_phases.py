"""System One settings phases: phase-enable guards, tunables and connection fields."""

import json
from config import (
    PROVIDER_SYSTEMONE_COMPATIBLE, PROVIDER_TYPESAFE, SYSTEMONE_TUNABLE_DEFAULTS,
    coerce_bool_setting,
)
from api import error_response
from systemone.tuning import (
    SystemOneSettingsError, merge_profile, profile_from_snapshot, profile_key,
)
from rate_limit_hold import clear_holds_for_provider_change
from llm_client import get_effective_provider_from_snapshot, invalidate_provider_cache
from llm_route import SLOT_PRIMARY, SLOT_SECONDARY, resolved_stage_slot
from llm_capabilities import systemone_supported_phases
from utils.secret_writes import SecretWriteRejected, set_or_clear_secret
from api.settings import common


def _validate_systemone_phase_enables(db, data, prospective_feed_modes=None):
    projected = {key: entry.get('value') for key, entry in db.get_all_settings().items()}
    field_map = {
        'llmProvider': 'llm_provider',
        'secondaryProviderEnabled': 'secondary_provider_enabled',
        'secondaryProvider': 'secondary_provider',
        'detectionProvider': 'detection_provider',
        'verificationProvider': 'verification_provider',
        'chaptersProvider': 'chapters_provider',
        'reviewProvider': 'review_provider',
        'patternCleanupProvider': 'pattern_cleanup_provider',
        'claudeModel': 'claude_model',
        'verificationModel': 'verification_model',
        'chaptersModel': 'chapters_model',
        'patternCleanupModel': 'pattern_cleanup_model',
        'failoverLlmEnabled': 'failover_llm_enabled',
        'failoverLlmProvider': 'failover_llm_provider',
        'failoverLlmDetectionModel': 'failover_llm_detection_model',
        'failoverLlmVerificationModel': 'failover_llm_verification_model',
        'failoverLlmReviewModel': 'failover_llm_review_model',
        'failoverLlmChaptersModel': 'failover_llm_chapters_model',
        'chaptersEnabled': 'chapters_enabled',
        'chaptersMode': 'chapters_mode',
        'patternCleanupEnabled': 'pattern_cleanup_enabled',
    }
    for payload_key, db_key in field_map.items():
        if payload_key not in data:
            continue
        value = data[payload_key]
        if payload_key in ('secondaryProviderEnabled', 'failoverLlmEnabled',
                           'chaptersEnabled', 'patternCleanupEnabled'):
            value = 'true' if value else 'false'
        projected[db_key] = value

    projected['llm_provider'] = get_effective_provider_from_snapshot(projected)

    class ProjectedSettings:
        def get_setting(self, key):
            return projected.get(key)

    view = ProjectedSettings()
    chapters_enabled = coerce_bool_setting(projected.get('chapters_enabled'))
    global_mode = projected.get('chapters_mode') or 'auto'
    has_active_feed_mode = global_mode != 'off'
    if chapters_enabled and global_mode == 'off':
        current_modes = {row.get('slug'): row.get('chapters_mode')
                         for row in db.get_all_podcasts()}
        current_modes.update(prospective_feed_modes or {})
        has_active_feed_mode = any(mode in ('auto', 'generate') for mode in current_modes.values())
    if chapters_enabled and has_active_feed_mode:
        slot = resolved_stage_slot(view, 'chapters')
        provider_key = (projected.get('secondary_provider') if slot == SLOT_SECONDARY
                        else projected.get('llm_provider'))
        model = projected.get('chapters_model')
        if model is None:
            model = projected.get('claude_model')
        supported = systemone_supported_phases(provider_key or '', model)
        if supported is not None and 'chapters' not in supported:
            return (f"chapters is unsupported for effective provider {provider_key!r} and "
                    f"model {model!r}; choose a supported chat provider and model", 400)

    if coerce_bool_setting(projected.get('pattern_cleanup_enabled')):
        slot = resolved_stage_slot(view, 'pattern_cleanup')
        provider_key = (projected.get('secondary_provider') if slot == SLOT_SECONDARY
                        else projected.get('llm_provider'))
        model = projected.get('pattern_cleanup_model')
        if model is None:
            model = projected.get('claude_model')
        supported = systemone_supported_phases(provider_key or '', model)
        if supported is not None and 'pattern_cleanup' not in supported:
            return (f"pattern_cleanup is unsupported for effective provider {provider_key!r} and "
                    f"model {model!r}; choose a supported chat provider and model", 400)

    if coerce_bool_setting(projected.get('failover_llm_enabled')):
        provider = projected.get('failover_llm_provider') or ''
        if systemone_supported_phases(provider, None) is not None:
            return (f"failover LLM is chat-only and cannot use effective provider {provider!r}", 400)
        for model_key in (
            'failover_llm_detection_model', 'failover_llm_verification_model',
            'failover_llm_review_model', 'failover_llm_chapters_model',
        ):
            model = projected.get(model_key)
            if model is not None and systemone_supported_phases(provider, model) is not None:
                return (f"failover LLM is chat-only and cannot use effective provider {provider!r} "
                        f"with model {model!r}", 400)
    return None


def _systemone_tunable_values(db, data):
    patch = data.get('systemOneTunables')
    if patch is None:
        return {}, None
    if not isinstance(patch, dict):
        return None, ('systemOneTunables must be an object', 400)
    if set(patch) - {'primary', 'secondary'}:
        return None, ('systemOneTunables contains an unsupported slot', 400)
    snapshot = db.get_all_settings()
    values = {}
    try:
        for slot, providers in patch.items():
            if not isinstance(providers, dict):
                return None, (f'systemOneTunables.{slot} must be an object', 400)
            if set(providers) - {'typesafe', 'systemone-compatible'}:
                return None, (f'systemOneTunables.{slot} contains an unsupported provider', 400)
            for provider, profile_patch in providers.items():
                key = profile_key(slot, provider)
                current = profile_from_snapshot(snapshot, slot, provider)
                defaults = SYSTEMONE_TUNABLE_DEFAULTS[provider]
                if profile_patch is None:
                    merged = dict(defaults)
                    is_default = True
                else:
                    merged = merge_profile(defaults, current, profile_patch)
                    is_default = merged == defaults
                values[key] = (merged, is_default)
    except SystemOneSettingsError as exc:
        return None, (str(exc), 400)
    return values, None


def _validate_systemone_tunables_payload(db, data):
    _values, issue = _systemone_tunable_values(db, data)
    return issue


def _apply_systemone_tunables(db, data):
    values, issue = _systemone_tunable_values(db, data)
    if issue is not None:
        return error_response(*issue)
    for key, (profile, is_default) in values.items():
        db.set_setting(
            key, json.dumps(profile, separators=(',', ':'), sort_keys=True),
            is_default=is_default)
    return None


def _apply_systemone_connection_fields(db, data):
    changed = False
    changed_providers = set()
    for payload_key, setting_key in (
        ('typesafeApiKey', 'typesafe_api_key'),
        ('systemoneApiKey', 'systemone_api_key'),
    ):
        if payload_key not in data:
            continue
        try:
            set_or_clear_secret(db, setting_key, data[payload_key])
        except SecretWriteRejected:
            return error_response('provider_crypto_unavailable', 409)
        changed = True
        changed_providers.add(
            PROVIDER_TYPESAFE if payload_key == 'typesafeApiKey'
            else PROVIDER_SYSTEMONE_COMPATIBLE)
    if 'systemoneBaseUrl' in data:
        value = (data['systemoneBaseUrl'] or '').strip()
        if value:
            db.set_setting('systemone_base_url', value, is_default=False)
        else:
            db.clear_setting('systemone_base_url')
        changed = True
        changed_providers.add(PROVIDER_SYSTEMONE_COMPATIBLE)
    if changed:
        common.after_commit(invalidate_provider_cache)
        common.after_commit(
            lambda: clear_holds_for_provider_change(
                db, 'System One provider settings changed', changed_providers,
                credential_slot=SLOT_PRIMARY),
            bucket='holds')
    return None
