"""Ad detection settings routes: /settings/ad-detection and its phase helpers."""

import json
import logging
import math
import os
import sqlite3
import threading
from urllib.parse import urlsplit
from collections.abc import Mapping
from flask import request
from api import (
    api, log_request, json_response, error_response,
    get_database,
)
from config import (
    WHISPER_BACKENDS,
    SYSTEMONE_TUNABLE_DEFAULTS,
    WHISPER_COMPUTE_TYPES,
    PROVIDER_OPENAI_COMPATIBLE, PROVIDER_OLLAMA,
    SYSTEMONE_PROVIDERS,
    PROVIDER_TYPESAFE, PROVIDER_SYSTEMONE_COMPATIBLE,
    ALLOWED_AUDIO_BITRATES, ALLOWED_AUDIO_ENCODER_COMPRESSION_LEVELS, AD_DETECTION_PARALLEL_WINDOWS_MIN,
    AD_DETECTION_PARALLEL_WINDOWS_MAX,
    AD_REVIEWER_PARALLEL_ADS_MIN,
    AD_REVIEWER_PARALLEL_ADS_MAX,
    WHISPER_API_TIMEOUT_MIN, WHISPER_API_TIMEOUT_MAX,
    WHISPER_POOL_MAX_REQUESTS_RANGE, WHISPER_POOL_MAX_EPISODES_RANGE,
    coerce_bool_setting,
    apply_ad_chapter_compat, MAX_AD_DURATION, MAX_AD_DURATION_CONFIRMED,
    MAX_ARTWORK_BYTES_MIN, MAX_ARTWORK_BYTES_MAX, MAX_RSS_BYTES_MIN,
    MAX_AUDIO_DOWNLOAD_MB_MIN,
    PODCAST_SEARCH_PROVIDERS,
    SEGMENT_CATEGORIES, SEGMENT_ACTIONS,
    LOW_AD_YIELD_ACTIONS,
    EPISODE_LOG_LEVELS,
    EPISODE_LOG_RETENTION_DAYS_MIN, EPISODE_LOG_RETENTION_DAYS_MAX,
    USER_AGENT_MAX_LENGTH, validate_user_agent,
    resolve_segment_category_actions_map,
    valid_ad_chapter_title_format,
)
from systemone.tuning import (
    SystemOneSettingsError, merge_profile, profile_from_snapshot,
    profile_key,
)
from artwork_watermark import BADGE_POSITIONS
from audio_processor import NORMALIZE_PRESETS
from database.settings import (
    AD_RESET_SETTING_KEYS, SETTINGS_REGISTRY,
    registry_get_default,
)
from user_agent import invalidate_cache as invalidate_user_agent_cache
from rate_limit_hold import clear_holds_for_provider_change
from pricing_fetcher import force_refresh_pricing
from whisper_pool import get_pool
from llm_client import (
    get_effective_provider, get_effective_provider_from_snapshot, get_effective_base_url, get_llm_client, _JSON_FORMAT_SETTING_KEY, _JSON_SCHEMA_SETTING_KEY,
    invalidate_provider_cache, reset_schema_probe_memo,
)
from llm_route import (
    VALID_SLOTS, SAME_AS_DETECTION, SAME_AS_PASS,
    SLOT_PRIMARY, SLOT_SECONDARY, resolved_stage_slot,
)
from llm_capabilities import systemone_supported_phases
import failover
from tools.reviewer_calibration import calibration_revision, trigger_reviewer_calibration
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

# Post-commit ordering contract: caches are invalidated before holds lift, so
# a resumed queue reads the configuration the save just committed.
_POST_COMMIT_BUCKETS = ('side_effects', 'holds')
_deferred = threading.local()


def _after_commit(fn, bucket: str = 'side_effects'):
    """Hold a phase side effect until the settings transaction commits.

    Runs inline when no transaction is open, so a phase helper called
    directly keeps its standalone behavior.
    """
    buckets = getattr(_deferred, 'buckets', None)
    if buckets is None:
        fn()
        return
    buckets[bucket].append(fn)


def _run_post_commit(callbacks):
    """Writes are already durable here, so a failing side effect must not 500."""
    for fn in callbacks:
        try:
            fn()
        except Exception:
            logger.exception("Post-commit settings side effect failed")


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


def _rebuild_served_feeds():
    """Re-render served feeds so a body-changing setting shows up now."""
    from main_app.feeds import rebuild_all_served_feeds
    try:
        rebuild_all_served_feeds()
    except Exception as e:
        logger.warning(f"Served feed rebuild after a settings change failed: {e}")


@api.route('/settings/ad-detection', methods=['PUT'])
@log_request
def update_ad_detection_settings():
    """Update ad detection settings.

    Each phase helper applies a related slice of fields (prompts, model
    selection, numeric clamps, provider gating, whisper config) and returns
    None or a Flask error response. All phases run in one transaction, so a
    rejection persists nothing; cache invalidation, hold clearing, and
    reviewer calibration follow the commit.
    """
    data = request.get_json()

    if not data:
        return error_response('Request body required', 400)

    db = get_database()

    # Provider A/B rename aliases (#806): normalise before any validation
    # or applier phase sees the payload.
    for alias, canonical in common.PROVIDER_B_ALIASES.items():
        if alias in data and canonical not in data:
            data[canonical] = data.pop(alias)
    stage_provider_keys = (
        'detectionProvider', 'verificationProvider', 'chaptersProvider', 'reviewProvider')
    for key in stage_provider_keys:
        if key in data and data[key] is not None and not isinstance(data[key], str):
            return error_response(f'{key} must be a string or null', 400)
        if isinstance(data.get(key), str) and data[key] in common.SLOT_ALIASES:
            data[key] = common.SLOT_ALIASES[data[key]]

    if 'adAddressingMode' in data:
        value = str(data['adAddressingMode'] or '').strip().lower()
        if value not in ('timestamps', 'segment_ids', 'random'):
            return error_response(
                'adAddressingMode must be "timestamps", "segment_ids", or "random"', 400)

    validation_issue = validate_settings_payload(db, data)
    if validation_issue is not None:
        return error_response(*validation_issue)

    provider_endpoint_error = validate_provider_endpoint_security(data)
    if provider_endpoint_error is not None:
        return error_response(*provider_endpoint_error)

    buckets = {name: [] for name in _POST_COMMIT_BUCKETS}
    _deferred.buckets = buckets
    try:
        with db.settings_transaction():
            changed_stages, previous_calibration = apply_settings_payload_in_transaction(db, data)
    except common._PhaseRejected as rejected:
        return rejected.response
    except sqlite3.Error:
        logger.exception("Settings save failed; no field was persisted")
        return error_response('Settings could not be saved', 500)
    finally:
        _deferred.buckets = None

    _run_post_commit(buckets['side_effects'])
    _run_post_commit(buckets['holds'])

    finish_settings_payload_after_commit(db, changed_stages, previous_calibration)

    return json_response({'message': 'Settings updated'})


def apply_settings_payload_in_transaction(db, data, *, allow_inactive_tunables=False):
    """Validate and apply an ad-detection settings payload inside a caller transaction."""
    validation_error = validate_settings_payload(
        db, data, allow_inactive_tunables=allow_inactive_tunables)
    if validation_error is not None:
        raise common._PhaseRejected(error_response(*validation_error))

    previous_model_identities = _model_identity_snapshot(db)

    phases = (
        _apply_prompt_fields,
        _apply_review_fields,
        _apply_model_fields,
        _apply_provider_routing_fields,
        _apply_model_pricing_fields,
        _apply_processing_flags,
        _apply_feed_refresh_fields,
        _apply_queue_boost_fields,
        _apply_min_cut_confidence,
        _apply_audio_fields,
        _apply_size_caps,
        # Secondary first so this save's stage routing resolves against the
        # slot state it is setting.
        _apply_secondary_provider_fields,
        _apply_failover_llm_fields,
        _apply_failover_whisper_fields,
        _apply_failover_policy_fields,
        _apply_provider_timeout_fields,
        _apply_systemone_connection_fields,
        _apply_provider_fields,
        _apply_whisper_fields,
        _apply_vad_gap_fields,
        _apply_audio_cue_fields,
        _apply_positional_prior_fields,
        _apply_podcast_index_fields,
        _apply_transcribe_chunk_fields,
        lambda target_db, payload: _apply_stage_tunables(
            target_db, payload, allow_inactive_tunables=allow_inactive_tunables),
        _apply_systemone_tunables,
        _apply_opening_exclusion_fields,
        _apply_ad_merge_fields,
        _apply_max_ad_duration_fields,
        _apply_detection_tuning_fields,
        _apply_segment_category_actions,
        _apply_ad_chapter_fields,
        _apply_community_sync_categories,
        _apply_jit_blocked_user_agents,
        _apply_user_agent_fields,
        _apply_provider_rate_limit_fields,
    )
    # A stage-model change makes that account's cooldown meaningless: the new
    # model can carry entirely different limits (issue #747). Detect before
    # the phases persist the new values, lift the hold after they succeed.
    changed_stages = _changed_stage_models(db, data)
    # Review route as stored now; the post-commit hook reruns the self-test
    # only when this save moves it.
    previous_calibration = calibration_revision()
    for phase in phases:
        err = phase(db, data)
        if err is not None:
            raise common._PhaseRejected(err)
    changed_stages.extend(_clear_models_for_identity_changes(
        db, previous_model_identities, data))
    changed_stages = sorted(set(changed_stages))
    return changed_stages, previous_calibration


def finish_settings_payload_after_commit(db, changed_stages, previous_calibration):
    """Run hold and reviewer effects after a shared settings transaction commits."""
    targets: dict[str, set] = {}
    for stage in changed_stages:
        provider_key, credential_slot = _stage_hold_target(db, stage)
        if provider_key:
            targets.setdefault(credential_slot, set()).add(provider_key)
    for credential_slot, provider_keys in targets.items():
        clear_holds_for_provider_change(
            db, 'stage model changed', provider_keys,
            credential_slot=credential_slot)
    trigger_reviewer_calibration(db, previous_calibration)


_STAGE_MODEL_KEYS = {
    'detection': ('claude_model', 'claudeModel'),
    'verification': ('verification_model', 'verificationModel'),
    'chapters': ('chapters_model', 'chaptersModel'),
    'review': ('review_model', 'reviewModel'),
}


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


def _model_identity_snapshot(db):
    identities = {
        stage: _provider_account_identity(db, resolved_stage_slot(db, stage))
        for stage in _STAGE_MODEL_KEYS
    }
    identities['pattern_cleanup'] = _provider_account_identity(
        db, resolved_stage_slot(db, 'pattern_cleanup'))
    identities['failover_llm'] = _failover_llm_identity(db)
    return identities


def _clear_models_for_identity_changes(db, previous, data):
    current = _model_identity_snapshot(db)
    changed_stages = []
    for stage, (setting_key, payload_key) in _STAGE_MODEL_KEYS.items():
        if previous[stage] == current[stage] or payload_key in data:
            continue
        if stage == 'review' and db.get_setting(setting_key) == SAME_AS_PASS:
            continue
        db.set_setting(setting_key, '', is_default=False)
        changed_stages.append(stage)
    if (previous['pattern_cleanup'] != current['pattern_cleanup']
            and 'patternCleanupModel' not in data
            and db.get_setting('pattern_cleanup_model') is not None):
        db.set_setting('pattern_cleanup_model', '', is_default=False)
    if previous['failover_llm'] != current['failover_llm']:
        for stage in ('detection', 'review', 'verification', 'chapters'):
            setting_key = f'failover_llm_{stage}_model'
            payload_key = 'failoverLlm' + stage.capitalize() + 'Model'
            if payload_key not in data:
                db.set_setting(setting_key, '', is_default=False)
    return changed_stages


def _validate_processing_defaults_payload(data):
    """Validate global feed defaults before the settings transaction writes."""
    if 'chaptersMode' in data:
        value = str(data['chaptersMode'] or '').strip().lower()
        if value not in ('auto', 'generate', 'off'):
            return 'chaptersMode must be auto, generate, or off', 400
    if 'skipSecondPass' in data and not isinstance(data['skipSecondPass'], bool):
        return 'skipSecondPass must be a boolean', 400
    if ('transcriptDifferentialEnabled' in data
            and not isinstance(data['transcriptDifferentialEnabled'], bool)):
        return 'transcriptDifferentialEnabled must be a boolean', 400
    if 'spliceVetoEnabled' in data and not isinstance(data['spliceVetoEnabled'], bool):
        return 'spliceVetoEnabled must be a boolean', 400
    if 'differentialFetchMode' in data:
        value = str(data['differentialFetchMode'] or '').strip().lower()
        if value not in ('auto', 'on', 'off'):
            return 'differentialFetchMode must be auto, on, or off', 400
    if 'adAddressingMode' in data:
        value = str(data['adAddressingMode'] or '').strip().lower()
        if value not in ('timestamps', 'segment_ids', 'random'):
            return 'adAddressingMode must be "timestamps", "segment_ids", or "random"', 400
    if 'maxFeedEpisodes' in data:
        try:
            maximum = int(data['maxFeedEpisodes'])
        except (TypeError, ValueError):
            return 'maxFeedEpisodes must be an integer', 400
        if maximum < 10 or maximum > 500:
            return 'maxFeedEpisodes must be between 10 and 500', 400
    if ('artworkBadgePosition' in data
            and data['artworkBadgePosition'] not in BADGE_POSITIONS):
        return f'artworkBadgePosition must be one of: {", ".join(BADGE_POSITIONS)}', 400
    if 'lowAdYieldAction' in data and data['lowAdYieldAction'] not in LOW_AD_YIELD_ACTIONS:
        return f'lowAdYieldAction must be one of: {", ".join(LOW_AD_YIELD_ACTIONS)}', 400
    if 'episodeLogRetentionDays' in data:
        days = data['episodeLogRetentionDays']
        if (not isinstance(days, int) or isinstance(days, bool)
                or days < EPISODE_LOG_RETENTION_DAYS_MIN
                or days > EPISODE_LOG_RETENTION_DAYS_MAX):
            return (
                'episodeLogRetentionDays must be an integer between '
                f'{EPISODE_LOG_RETENTION_DAYS_MIN} and {EPISODE_LOG_RETENTION_DAYS_MAX}', 400)
    if ('episodeLogLevel' in data
            and data['episodeLogLevel'] not in EPISODE_LOG_LEVELS):
        return f'episodeLogLevel must be one of: {", ".join(EPISODE_LOG_LEVELS)}', 400
    if 'feedAuthEnabled' in data and not isinstance(data['feedAuthEnabled'], bool):
        return 'feedAuthEnabled must be a boolean', 400
    return None


# Stage models the rate-limit hold is scoped by; payload keys come from the
# registry so a renamed key cannot silently stop lifting holds.
_STAGE_MODEL_SETTINGS = (
    ('detection', 'claude_model'),
    ('review', 'review_model'),
    ('verification', 'verification_model'),
    ('chapters', 'chapters_model'),
)


def _changed_stage_models(db, data) -> list[str]:
    """Stages whose model this payload actually changes."""
    changed = []
    for stage, db_key in _STAGE_MODEL_SETTINGS:
        payload_key = SETTINGS_REGISTRY[db_key].payload_key
        if (payload_key in data
                and str(data[payload_key] or '') != (db.get_setting(db_key) or '')):
            changed.append(stage)
    return changed


def _stage_hold_target(db, stage: str) -> tuple[str | None, str]:
    """(provider_key, credential_slot) a stage's calls would hit right now."""
    slot = resolved_stage_slot(db, stage)
    if slot == SLOT_SECONDARY:
        return db.get_setting('secondary_provider'), SLOT_SECONDARY
    return db.get_setting('llm_provider') or get_effective_provider(), SLOT_PRIMARY


def _apply_prompt_fields(db, data):
    """Persist the prompt strings.

    An empty/whitespace prompt is never valid (the runtime falls back to the
    default), so clearing a field and saving resets it to default rather than
    storing a blank row that would render as an unrecoverable empty textarea.
    """
    for payload_key, db_key, log_label in (
        ('systemPrompt', 'system_prompt', 'system prompt'),
        ('verificationPrompt', 'verification_prompt', 'verification prompt'),
        ('reviewPrompt', 'review_prompt', 'review prompt'),
        ('resurrectPrompt', 'resurrect_prompt', 'resurrect prompt'),
        ('chapterPrompt', 'chapter_prompt', 'chapter prompt'),
        ('patternCleanupPrompt', 'pattern_cleanup_prompt', 'pattern cleanup prompt'),
    ):
        if payload_key in data:
            if not str(data[payload_key] or '').strip():
                db.reset_setting(db_key)
                logger.info(f"Reset {log_label} to default (blank submitted)")
            else:
                db.set_setting(db_key, data[payload_key], is_default=False)
                logger.info(f"Updated {log_label}")
    # Per-pass overrides: empty is valid (means "no override for this pass"),
    # so store the value as-is rather than resetting to a default.
    for payload_key, db_key in (
        ('systemPromptOverride', 'system_prompt_override'),
        ('verificationPromptOverride', 'verification_prompt_override'),
        ('reviewPromptOverride', 'review_prompt_override'),
        ('resurrectPromptOverride', 'resurrect_prompt_override'),
        ('chapterPromptOverride', 'chapter_prompt_override'),
    ):
        if payload_key in data:
            db.set_setting(db_key, str(data[payload_key] or ''), is_default=False)
            logger.info(f"Updated {db_key}")
    return


def _apply_review_fields(db, data):
    """Persist the LLM-reviewer toggle, model, and boundary-shift clamp."""
    values, issue = _review_field_values(data)
    if issue is not None:
        return error_response(*issue)

    if 'enableAdReview' in data:
        value = 'true' if bool(data['enableAdReview']) else 'false'
        db.set_setting('enable_ad_review', value, is_default=False)
        logger.info(f"Updated enable_ad_review to: {value}")

    if 'reviewModel' in data:
        db.set_setting('review_model', data['reviewModel'], is_default=False)
        logger.info(f"Updated review_model to: {data['reviewModel']}")

    for db_key, value in values:
        db.set_setting(db_key, str(value), is_default=False)
        logger.info(f"Updated {db_key} to: {value}")

    return None


def _review_field_values(data):
    values = []
    if 'reviewProvider' in data:
        provider = data['reviewProvider']
        valid = VALID_SLOTS + (SAME_AS_PASS,)
        if provider not in valid:
            return None, (f'reviewProvider must be one of: {", ".join(valid)}', 400)
        values.append(('review_provider', provider))
    if 'reviewMaxBoundaryShift' in data:
        try:
            shift = max(1, min(600, int(data['reviewMaxBoundaryShift'])))
        except (TypeError, ValueError):
            return None, ('reviewMaxBoundaryShift must be an integer', 400)
        values.append(('review_max_boundary_shift', shift))
    return values, None


def _validate_review_payload(data):
    _values, issue = _review_field_values(data)
    return issue


def _apply_model_fields(db, data):
    """Persist primary model selections; whisper change marks model for reload."""
    if 'claudeModel' in data:
        db.set_setting('claude_model', data['claudeModel'], is_default=False)
        logger.info(f"Updated Claude model to: {data['claudeModel']}")

    if 'verificationModel' in data:
        db.set_setting('verification_model', data['verificationModel'], is_default=False)
        logger.info(f"Updated verification model to: {data['verificationModel']}")

    if 'whisperModel' in data:
        db.set_setting('whisper_model', data['whisperModel'], is_default=False)
        logger.info(f"Updated Whisper model to: {data['whisperModel']}")
        _after_commit(_mark_whisper_for_reload)

    if 'chaptersModel' in data:
        db.set_setting('chapters_model', data['chaptersModel'], is_default=False)
        logger.info(f"Updated chapters model to: {data['chaptersModel']}")
    return


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


def _apply_model_pricing_fields(db, data):
    """Validate and merge per-model USD pricing overrides."""
    if 'modelPricingOverrides' not in data:
        return None
    patch, issue = _model_pricing_patch(data['modelPricingOverrides'])
    if issue is not None:
        return error_response(*issue)
    if patch:
        db.merge_model_pricing_overrides(patch)
        logger.info("Updated model pricing overrides for %d model(s)", len(patch))
    return None


def _model_pricing_patch(submitted):
    if not isinstance(submitted, Mapping):
        return None, ('modelPricingOverrides must be an object', 400)

    patch = {}
    for raw_model_id, rates in submitted.items():
        if not isinstance(raw_model_id, str) or not raw_model_id.strip():
            return None, ('model pricing override IDs must be non-empty strings', 400)
        model_id = raw_model_id.strip()
        if model_id in patch:
            return None, (f'modelPricingOverrides contains duplicate model ID {model_id}', 400)
        if rates is None:
            patch[model_id] = None
            continue
        if not isinstance(rates, Mapping):
            return None, (f'modelPricingOverrides.{model_id} must be an object or null', 400)
        required = {'inputCostPerMtok', 'outputCostPerMtok'}
        if set(rates) != required:
            return None, (
                f'modelPricingOverrides.{model_id} must contain inputCostPerMtok '
                'and outputCostPerMtok', 400)

        parsed = {}
        for field in required:
            value = rates[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                return None, (
                    f'modelPricingOverrides.{model_id}.{field} must be a '
                    'non-negative number', 400)
            value = float(value)
            if not math.isfinite(value) or value < 0:
                return None, (
                    f'modelPricingOverrides.{model_id}.{field} must be a '
                    'non-negative number', 400)
            parsed[field] = value
        patch[model_id] = parsed

    return patch, None


def _validate_model_pricing_payload(data):
    if 'modelPricingOverrides' not in data:
        return None
    _patch, issue = _model_pricing_patch(data['modelPricingOverrides'])
    return issue


def _apply_size_caps(db, data):
    """Persist the download/artwork/RSS size caps (env-backed, issue #491).

    Validates every field before writing any, so a 400 never leaves part of
    the payload persisted.
    """
    writes, issue = _size_cap_writes(data)
    if issue is not None:
        return error_response(*issue)
    for db_key, value in writes:
        db.set_setting(db_key, str(value), is_default=False)
        logger.info(f"Updated {db_key} to: {value}")


def _size_cap_writes(data):
    caps = (
        ('maxArtworkBytes', 'max_artwork_bytes', MAX_ARTWORK_BYTES_MIN, MAX_ARTWORK_BYTES_MAX),
        ('maxRssBytes', 'max_rss_bytes', MAX_RSS_BYTES_MIN, None),
        ('maxAudioDownloadMb', 'max_audio_download_mb', MAX_AUDIO_DOWNLOAD_MB_MIN, None),
    )
    writes = []
    for payload_key, db_key, floor, ceiling in caps:
        if payload_key not in data:
            continue
        try:
            n = int(data[payload_key])
        except (TypeError, ValueError):
            return None, (f'{payload_key} must be an integer', 400)
        if n < floor or (ceiling is not None and n > ceiling):
            bound = f'between {floor} and {ceiling}' if ceiling is not None else f'at least {floor}'
            return None, (f'{payload_key} must be {bound}', 400)
        writes.append((db_key, n))
    return writes, None


def _validate_size_caps_payload(data):
    _writes, issue = _size_cap_writes(data)
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


def _apply_user_agent_fields(db, data):
    """Persist the outbound User-Agent strings.

    Validates both before writing either, so a 400 never leaves half the
    payload persisted. An empty string resets the field to its default.
    """
    writes, log_download_query, issue = _user_agent_values(data)
    if issue is not None:
        return error_response(*issue)
    if log_download_query is not None:
        db.set_setting('log_download_query',
                       'true' if log_download_query else 'false', is_default=False)
        logger.info(f"Updated log_download_query: {log_download_query}")
    for db_key, value in writes:
        if value:
            db.set_setting(db_key, value, is_default=False)
            logger.info(f"Updated {db_key}")
        else:
            db.clear_setting(db_key)
            logger.info(f"Reset {db_key} to the default")
    if writes:
        _after_commit(invalidate_user_agent_cache)


def _user_agent_values(data):
    fields = (
        ('downloadUserAgent', 'download_user_agent'),
        ('feedUserAgent', 'feed_user_agent'),
    )
    writes = []
    for payload_key, db_key in fields:
        if payload_key not in data:
            continue
        value = data[payload_key]
        if not isinstance(value, str):
            return None, None, (f'{payload_key} must be a string', 400)
        value = value.strip()
        if value and not validate_user_agent(value):
            return None, None, (
                f'{payload_key} must be printable ASCII on a single line, '
                f'at most {USER_AGENT_MAX_LENGTH} characters', 400)
        writes.append((db_key, value))
    log_download_query = None
    if 'logDownloadQuery' in data:
        if not isinstance(data['logDownloadQuery'], bool):
            return None, None, ('logDownloadQuery must be a boolean', 400)
        log_download_query = data['logDownloadQuery']
    return writes, log_download_query, None


def _validate_user_agent_payload(data):
    _writes, _log_query, issue = _user_agent_values(data)
    return issue


def _clear_format_probes(db) -> None:
    """Forget every response_format probe answer, stored and in-process."""
    db.set_setting(_JSON_FORMAT_SETTING_KEY, '', is_default=True)
    db.clear_setting(_JSON_SCHEMA_SETTING_KEY)
    _after_commit(reset_schema_probe_memo)
    _after_commit(invalidate_provider_cache)


def _apply_processing_flags(db, data):
    """Persist boolean processing toggles and the maxFeedEpisodes clamp."""
    if 'autoProcessEnabled' in data:
        value = 'true' if data['autoProcessEnabled'] else 'false'
        db.set_setting('auto_process_enabled', value, is_default=False)
        logger.info(f"Updated auto-process to: {value}")

    if 'maxFeedEpisodes' in data:
        try:
            max_ep = int(data['maxFeedEpisodes'])
        except (TypeError, ValueError):
            return error_response('maxFeedEpisodes must be an integer', 400)
        if max_ep < 10 or max_ep > 500:
            return error_response('maxFeedEpisodes must be between 10 and 500', 400)
        db.set_setting('max_feed_episodes', str(max_ep), is_default=False)
        logger.info(f"Updated max feed episodes to: {max_ep}")

    if 'onlyExposeProcessedDefault' in data:
        value = 'true' if data['onlyExposeProcessedDefault'] else 'false'
        changed = (db.get_setting('only_expose_processed_default') or 'false') != value
        db.set_setting('only_expose_processed_default', value, is_default=False)
        if changed:
            # Inheriting feeds 304-skip the rebuild that would hide or show
            # unprocessed episodes; force the next refresh to fetch in full.
            _after_commit(db.clear_all_podcast_etags)
        logger.info(f"Updated only-expose-processed default to: {value}")

    if 'detectShowSegments' in data:
        value = 'true' if data['detectShowSegments'] else 'false'
        db.set_setting('detect_show_segments', value, is_default=False)
        logger.info(f"Updated detect-show-segments default to: {value}")

    if 'textRecurrenceHints' in data:
        value = 'true' if data['textRecurrenceHints'] else 'false'
        db.set_setting('text_recurrence_hints', value, is_default=False)
        logger.info(f"Updated text-recurrence-hints to: {value}")

    if 'adAddressingMode' in data:
        # Already validated (400 on reject) by the route before any phase
        # runs; this only persists, so it lands in the same phase order as
        # its sibling flags instead of ahead of the rest of the payload.
        value = str(data['adAddressingMode'] or '').strip().lower()
        db.set_setting('ad_addressing_mode', value, is_default=False)
        logger.info(f"Updated ad_addressing_mode to: {value}")

    for payload_key, db_key in (
        ('seedSponsorsDetection', 'seed_sponsors_detection'),
        ('seedSponsorsVerification', 'seed_sponsors_verification'),
        ('seedSponsorsReviewer', 'seed_sponsors_reviewer'),
        ('seedSponsorsResurrect', 'seed_sponsors_resurrect'),
    ):
        if payload_key in data:
            value = 'true' if data[payload_key] else 'false'
            db.set_setting(db_key, value, is_default=False)
            logger.info(f"Updated {db_key} to: {value}")

    if 'processNewEpisodesFirst' in data:
        value = 'true' if data['processNewEpisodesFirst'] else 'false'
        db.set_setting('process_new_episodes_first', value, is_default=False)
        logger.info(f"Updated process-new-episodes-first to: {value}")

    if 'artworkWatermarkEnabled' in data:
        value = 'true' if data['artworkWatermarkEnabled'] else 'false'
        db.set_setting('artwork_watermark_enabled', value, is_default=False)
        # The badge state is part of the cover URL token, and a steady feed
        # 304-skips the re-render that would move it, so apps would keep
        # fetching the old image. Same reason as the feed-auth clear below.
        _after_commit(db.clear_all_podcast_etags)
        logger.info(f"Updated artwork watermark to: {value}")

    if 'artworkBadgePosition' in data:
        if data['artworkBadgePosition'] not in BADGE_POSITIONS:
            return error_response(
                f'artworkBadgePosition must be one of: {", ".join(BADGE_POSITIONS)}', 400)
        db.set_setting('artwork_badge_position', data['artworkBadgePosition'],
                       is_default=False)
        _after_commit(db.clear_all_podcast_etags)
        logger.info(f"Updated artwork badge position to: {data['artworkBadgePosition']}")

    if 'lowAdYieldAction' in data:
        if data['lowAdYieldAction'] not in LOW_AD_YIELD_ACTIONS:
            return error_response(
                f'lowAdYieldAction must be one of: {", ".join(LOW_AD_YIELD_ACTIONS)}', 400)
        db.set_setting('low_ad_yield_action', data['lowAdYieldAction'],
                       is_default=False)
        logger.info(f"Updated low-ad-yield action to: {data['lowAdYieldAction']}")

    if 'episodeLogRetentionDays' in data:
        days = data['episodeLogRetentionDays']
        if (not isinstance(days, int) or isinstance(days, bool)
                or days < EPISODE_LOG_RETENTION_DAYS_MIN
                or days > EPISODE_LOG_RETENTION_DAYS_MAX):
            return error_response(
                'episodeLogRetentionDays must be an integer between '
                f'{EPISODE_LOG_RETENTION_DAYS_MIN} and '
                f'{EPISODE_LOG_RETENTION_DAYS_MAX}', 400)
        db.set_setting('episode_log_retention_days', str(days), is_default=False)
        logger.info(f"Updated episode log retention to {days} days")

    if 'episodeLogLevel' in data:
        if data['episodeLogLevel'] not in EPISODE_LOG_LEVELS:
            return error_response(
                f'episodeLogLevel must be one of: {", ".join(EPISODE_LOG_LEVELS)}', 400)
        db.set_setting('episode_log_level', data['episodeLogLevel'],
                       is_default=False)
        logger.info(f"Updated episode log level to: {data['episodeLogLevel']}")

    if 'feedAuthEnabled' in data:
        enabled = data['feedAuthEnabled']
        # Strict type check: bool("false") is True, so a stringly-typed
        # disable request would ENABLE enforcement and lock out every
        # subscribed app. Too much blast radius for lenient coercion.
        if not isinstance(enabled, bool):
            return error_response('feedAuthEnabled must be a boolean', 400)
        # No-op guard: clearing every feed's etag is expensive enough that a
        # repeated PUT with the current value must not trigger it.
        if db.get_setting_bool('feed_auth_enabled', False) != enabled:
            if enabled and not db.get_setting('feed_auth_key'):
                # Lazy generation: first enable mints the key. Never logged.
                from main_app.feed_auth import generate_feed_key
                db.set_setting('feed_auth_key', generate_feed_key(),
                               is_default=False)
                logger.info("Generated feed auth key")
            value = 'true' if enabled else 'false'
            db.set_setting('feed_auth_enabled', value, is_default=False)
            # Clear conditional-GET validators so the scheduled refresher
            # cannot 304-skip re-rendering served feeds with the new state.
            _after_commit(db.clear_all_podcast_etags)
            logger.info(f"Updated feed auth to: {value}")

    if 'vttTranscriptsEnabled' in data:
        value = 'true' if data['vttTranscriptsEnabled'] else 'false'
        db.set_setting('vtt_transcripts_enabled', value, is_default=False)
        logger.info(f"Updated VTT transcripts to: {value}")

    if 'chaptersEnabled' in data:
        value = 'true' if data['chaptersEnabled'] else 'false'
        db.set_setting('chapters_enabled', value, is_default=False)
        logger.info(f"Updated chapters generation to: {value}")

    if 'chaptersMode' in data:
        value = str(data['chaptersMode'] or '').strip().lower()
        if value not in ('auto', 'generate', 'off'):
            return error_response('chaptersMode must be auto, generate, or off', 400)
        db.set_setting('chapters_mode', value, is_default=False)

    if 'skipSecondPass' in data:
        if not isinstance(data['skipSecondPass'], bool):
            return error_response('skipSecondPass must be a boolean', 400)
        db.set_setting('skip_second_pass',
                       'true' if data['skipSecondPass'] else 'false',
                       is_default=False)

    if 'transcriptDifferentialEnabled' in data:
        if not isinstance(data['transcriptDifferentialEnabled'], bool):
            return error_response('transcriptDifferentialEnabled must be a boolean', 400)
        db.set_setting('transcript_differential_enabled',
                       'true' if data['transcriptDifferentialEnabled'] else 'false',
                       is_default=False)

    if 'differentialFetchMode' in data:
        value = str(data['differentialFetchMode'] or '').strip().lower()
        if value not in ('auto', 'on', 'off'):
            return error_response('differentialFetchMode must be auto, on, or off', 400)
        db.set_setting('differential_fetch_mode', value, is_default=False)

    if 'chaptersInNotes' in data:
        value = 'true' if data['chaptersInNotes'] else 'false'
        changed = (db.get_setting('chapters_in_notes') or 'false') != value
        db.set_setting('chapters_in_notes', value, is_default=False)
        logger.info(f"Updated chapters in descriptions to: {value}")
        if changed:
            # Re-render now (local feeds included) rather than wait for a
            # scheduled refresh that would 304-skip the change.
            _after_commit(_rebuild_served_feeds)

    if 'omitTemperature' in data:
        value = 'true' if data['omitTemperature'] else 'false'
        db.set_setting('omit_temperature', value, is_default=False)
        logger.info(f"Updated omit_temperature to: {value}")

    if 'llmJsonSchemaEnabled' in data:
        value = 'true' if data['llmJsonSchemaEnabled'] else 'false'
        db.set_setting('llm_json_schema_enabled', value, is_default=False)
        # Re-probe on the next endpoint verification now that the opt-in
        # changed.
        _clear_format_probes(db)
        logger.info(f"Updated llm_json_schema_enabled to: {value}")
    return None


def _apply_queue_boost_fields(db, data):
    """Persist queue boost sizes. Manual should stay above bulk or backlog
    work outranks user requests again; that relationship is the operator's
    call, so it is documented, not enforced."""
    writes, issue = _queue_boost_writes(data)
    if issue is not None:
        return error_response(*issue)
    for db_key, value in writes:
        db.set_setting(db_key, str(value), is_default=False)
        logger.info(f"Updated {db_key} to: {value}")
    return None


def _queue_boost_writes(data):
    writes = []
    for json_key, db_key in (
        ('queueManualBoost', 'queue_manual_boost'),
        ('queueFreshBoost', 'queue_fresh_boost'),
        ('queueBulkBoost', 'queue_bulk_boost'),
    ):
        if json_key in data:
            try:
                value = int(data[json_key])
            except (TypeError, ValueError):
                return None, (f'{json_key} must be an integer', 400)
            if value < 0 or value > 100:
                return None, (f'{json_key} must be between 0 and 100', 400)
            writes.append((db_key, value))
    return writes, None


def _validate_queue_boost_payload(data):
    _writes, issue = _queue_boost_writes(data)
    return issue


def _apply_feed_refresh_fields(db, data):
    """Persist the RSS refresh interval."""
    value, issue = _rss_refresh_minutes(data)
    if issue is not None:
        return error_response(*issue)
    if value is not None:
        db.set_setting('rss_refresh_interval_minutes', str(value), is_default=False)
        logger.info(f"Updated RSS refresh interval to: {value} minutes")

    if 'podpingEnabled' in data:
        value = 'true' if data['podpingEnabled'] else 'false'
        db.set_setting('podping_enabled', value, is_default=False)
        logger.info(f"Updated podping listener to: {value}")
    return None


def _rss_refresh_minutes(data):
    if 'rssRefreshIntervalMinutes' not in data:
        return None, None
    try:
        minutes = int(data['rssRefreshIntervalMinutes'])
    except (TypeError, ValueError):
        return None, ('rssRefreshIntervalMinutes must be an integer', 400)
    if minutes < 5 or minutes > 1440:
        return None, ('rssRefreshIntervalMinutes must be between 5 and 1440', 400)
    return minutes, None


def _validate_rss_refresh_payload(data):
    _minutes, issue = _rss_refresh_minutes(data)
    return issue


def _apply_segment_category_actions(db, data):
    """Merge a partial segmentCategoryActions map over the stored global map.

    Every key must be a known segment category and every value a known
    action; the merged full map (not just the partial payload) is persisted
    so later reads never need to fall back through a partial global row.
    """
    value, issue = _segment_category_actions_value(db, data)
    if issue is not None:
        return error_response(*issue)
    if value is not None:
        db.set_setting('segment_category_actions', value, is_default=False)
        logger.info(f"Updated segment category actions: {value}")
    return None


def _segment_category_actions_value(db, data):
    if 'segmentCategoryActions' not in data:
        return None, None
    value = data['segmentCategoryActions']
    if not isinstance(value, dict):
        return None, ('segmentCategoryActions must be an object', 400)
    for category, action in value.items():
        if category not in SEGMENT_CATEGORIES:
            return None, (f"segmentCategoryActions: unknown category '{category}'", 400)
        if action not in SEGMENT_ACTIONS:
            return None, (
                f"segmentCategoryActions: unknown action '{action}' for '{category}'", 400)
    merged = resolve_segment_category_actions_map(
        db.get_setting('segment_category_actions'))
    merged.update(value)
    return json.dumps(merged), None


def _validate_segment_actions_payload(db, data):
    _value, issue = _segment_category_actions_value(db, data)
    return issue


def _translate_ad_chapter_compat(db, data):
    """Translate adChaptersEnabled/adChapterCategories into segment_category_actions
    (spec 1.4). (new_map_or_None, error); None means neither field was present."""
    if 'adChaptersEnabled' not in data and 'adChapterCategories' not in data:
        return None, None

    merged = resolve_segment_category_actions_map(db.get_setting('segment_category_actions'))
    changes, error = apply_ad_chapter_compat(merged, data)
    if error:
        return None, error
    merged.update(changes)
    return merged, None


def _ad_chapter_values(db, data):
    writes = []
    if 'adChaptersIncludeHeld' in data:
        writes.append(('ad_chapters_include_held',
                       'true' if coerce_bool_setting(data['adChaptersIncludeHeld']) else 'false'))

    for key, setting in (('adChapterTitleFormat', 'ad_chapter_title_format'),
                         ('adChapterHeldTitleFormat', 'ad_chapter_held_title_format'),
                         ('adChapterResumeTitle', 'ad_chapter_resume_title')):
        if key not in data:
            continue
        title = data[key]
        if not isinstance(title, str):
            return None, None, (f'{key} must be a string', 400)
        if not title.strip():
            writes.append((setting, ''))
            continue
        if setting != 'ad_chapter_resume_title' and not valid_ad_chapter_title_format(title):
            return None, None, (
                f'{key} must be text using only the {{label}} and {{category}} placeholders', 400)
        writes.append((setting, title.strip()))

    if 'adChapterMinConfidence' in data:
        value = data['adChapterMinConfidence']
        if not SETTINGS_REGISTRY['ad_chapter_min_confidence'].validator(str(value)):
            return None, None, ('adChapterMinConfidence must be between 0 and 1', 400)
        writes.append(('ad_chapter_min_confidence', str(float(value))))

    actions, message = _translate_ad_chapter_compat(db, data)
    if message:
        return None, None, (message, 400)
    if actions is not None:
        writes.append(('segment_category_actions', json.dumps(actions)))
    return writes, actions, None


def _validate_ad_chapter_payload(db, data):
    _writes, _actions, issue = _ad_chapter_values(db, data)
    return issue


def _apply_ad_chapter_fields(db, data):
    """Persist validated ad chapter settings and compatibility fields."""
    writes, actions, issue = _ad_chapter_values(db, data)
    if issue is not None:
        return error_response(*issue)
    for setting, value in writes:
        if value == '':
            db.clear_setting(setting)
            logger.info(f"Reset {setting} to the default")
        else:
            db.set_setting(setting, value, is_default=False)
    if actions is not None:
        logger.info(f"Updated segment category actions via ad chapter compatibility fields: {actions}")
    return None


def _apply_community_sync_categories(db, data):
    """Persist the global community-sync per-category accept list."""
    if 'communitySyncCategories' in data:
        categories, err = common.validate_community_sync_categories(data['communitySyncCategories'])
        if err is not None:
            return error_response(err, 400)
        db.set_setting('community_sync_categories', json.dumps(categories), is_default=False)
        logger.info(f"Updated community sync categories: {categories}")
    return None


def _validate_community_sync_payload(data):
    if 'communitySyncCategories' not in data:
        return None
    _categories, message = common.validate_community_sync_categories(data['communitySyncCategories'])
    return (message, 400) if message is not None else None


JIT_AGENT_MAX_LEN = 200
JIT_AGENT_MAX_COUNT = 50


def validate_jit_blocked_user_agents(value):
    """Return (patterns, error). Entries are trimmed; blanks are dropped."""
    if not isinstance(value, list):
        return None, 'jitBlockedUserAgents must be a list'
    cleaned = []
    for entry in value:
        if not isinstance(entry, str):
            return None, 'jitBlockedUserAgents entries must be strings'
        trimmed = entry.strip()
        if not trimmed:
            continue
        if len(trimmed) > JIT_AGENT_MAX_LEN:
            return None, f'jitBlockedUserAgents entries must be 1-{JIT_AGENT_MAX_LEN} characters'
        cleaned.append(trimmed)
    if len(cleaned) > JIT_AGENT_MAX_COUNT:
        return None, f'jitBlockedUserAgents must have at most {JIT_AGENT_MAX_COUNT} patterns'
    return cleaned, None


def _apply_jit_blocked_user_agents(db, data):
    """Persist the agents barred from triggering just-in-time processing."""
    if 'jitBlockedUserAgents' in data:
        patterns, err = validate_jit_blocked_user_agents(data['jitBlockedUserAgents'])
        if err is not None:
            return error_response(err, 400)
        db.set_setting('jit_blocked_user_agents', json.dumps(patterns), is_default=False)
        logger.info(f"Updated JIT-blocked user agents: {patterns}")
    return None


def _validate_jit_blocked_user_agents_payload(data):
    if 'jitBlockedUserAgents' not in data:
        return None
    _patterns, message = validate_jit_blocked_user_agents(data['jitBlockedUserAgents'])
    return (message, 400) if message is not None else None


def _apply_min_cut_confidence(db, data):
    """Clamp min_cut_confidence to [0.50, 0.95]."""
    value, issue = _min_cut_confidence_value(data)
    if issue is not None:
        return error_response(*issue)
    if value is not None:
        db.set_setting('min_cut_confidence', str(value), is_default=False)
        logger.info(f"Updated min cut confidence to: {value}")
    return None


def _min_cut_confidence_value(data):
    if 'minCutConfidence' not in data:
        return None, None
    try:
        raw = float(data['minCutConfidence'])
    except (TypeError, ValueError):
        return None, ('minCutConfidence must be a number', 400)
    if not math.isfinite(raw):
        return None, ('minCutConfidence must be a finite number', 400)
    return max(0.50, min(0.95, raw)), None


def _apply_audio_fields(db, data):
    """Persist validated audio output settings."""
    writes, issue = _audio_field_writes(data)
    if issue is not None:
        return error_response(*issue)
    for db_key, value in writes:
        if value is None:
            db.reset_setting(db_key)
        else:
            db.set_setting(db_key, str(value), is_default=False)
        logger.info(f"Updated {db_key} to: {value}")
    return None


def _audio_field_writes(data):
    writes = []
    for payload_key, db_key in (
            ('audioReplacementSoundEnabled', 'audio_replacement_sound_enabled'),
            ('audioMp3StreamCopyEnabled', 'audio_mp3_stream_copy_enabled')):
        if payload_key in data:
            value = data[payload_key]
            if value is not None and not isinstance(value, bool):
                return None, (f'{payload_key} must be boolean or null', 400)
            writes.append((db_key, None if value is None else str(value).lower()))
    if 'audioBitrate' in data:
        val = str(data['audioBitrate']).strip()
        if val not in ALLOWED_AUDIO_BITRATES:
            return None, (
                f'audioBitrate must be one of: {", ".join(ALLOWED_AUDIO_BITRATES)}', 400)
        writes.append(('audio_bitrate', val))

    if 'audioEncoderCompressionLevel' in data:
        val = str(data['audioEncoderCompressionLevel']).strip()
        if val not in ALLOWED_AUDIO_ENCODER_COMPRESSION_LEVELS:
            return None, (
                f'audioEncoderCompressionLevel must be one of: '
                f'{", ".join(ALLOWED_AUDIO_ENCODER_COMPRESSION_LEVELS)}', 400)
        writes.append(('audio_encoder_compression_level', val))

    if 'audioNormalizeEnabled' in data:
        value = 'true' if data['audioNormalizeEnabled'] else 'false'
        writes.append(('audio_normalize_enabled', value))

    if 'audioNormalizeIntensity' in data:
        # Derive the allowed set from the presets themselves so the validator
        # can never drift from what AudioProcessor actually supports.
        valid_intensities = set(NORMALIZE_PRESETS.keys())
        if data['audioNormalizeIntensity'] not in valid_intensities:
            return None, (
                f'audioNormalizeIntensity must be one of: {", ".join(sorted(valid_intensities))}', 400)
        writes.append(('audio_normalize_intensity', data['audioNormalizeIntensity']))

    for payload_key, db_key, lo, hi in (
        ('adDetectionParallelWindows', 'ad_detection_parallel_windows',
         AD_DETECTION_PARALLEL_WINDOWS_MIN, AD_DETECTION_PARALLEL_WINDOWS_MAX),
        ('adReviewerParallelAds', 'ad_reviewer_parallel_ads',
         AD_REVIEWER_PARALLEL_ADS_MIN, AD_REVIEWER_PARALLEL_ADS_MAX),
    ):
        if payload_key not in data:
            continue
        try:
            n = int(data[payload_key])
        except (ValueError, TypeError):
            return None, (f'{payload_key} must be an integer', 400)
        if not (lo <= n <= hi):
            return None, (f'{payload_key} must be between {lo} and {hi}', 400)
        writes.append((db_key, n))
    return writes, None


def _validate_audio_payload(data):
    _writes, issue = _audio_field_writes(data)
    return issue


def _transcribe_chunk_values(db, data):
    parsed = {}
    for field_name, db_key, minimum, maximum in (
        ('transcribeMaxChunkSeconds', 'transcribe_max_chunk_seconds', 1, 7200),
        ('transcribeConcurrentChunks', 'transcribe_concurrent_chunks', 1, 32),
        ('transcribeChunkOverlapSeconds', 'transcribe_chunk_overlap_seconds', 1, 600),
        ('whisperApiTimeoutSeconds', 'whisper_api_timeout_seconds',
         WHISPER_API_TIMEOUT_MIN, WHISPER_API_TIMEOUT_MAX),
        ('whisperPoolMaxRequests', 'whisper_pool_max_requests',
         *WHISPER_POOL_MAX_REQUESTS_RANGE),
        ('whisperPoolMaxEpisodes', 'whisper_pool_max_episodes',
         *WHISPER_POOL_MAX_EPISODES_RANGE),
    ):
        if field_name not in data:
            continue
        try:
            value = int(data[field_name])
        except (TypeError, ValueError):
            return None, None, ('must be an integer', field_name, 400)
        if not minimum <= value <= maximum:
            return None, None, (
                f'must be between {minimum} and {maximum}', field_name, 400)
        parsed[db_key] = value

    pool_enabled = None
    if 'whisperPoolEnabled' in data:
        if not isinstance(data['whisperPoolEnabled'], bool):
            return None, None, ('must be a boolean', 'whisperPoolEnabled', 400)
        pool_enabled = data['whisperPoolEnabled']

    if parsed:
        def effective(db_key, fallback):
            if db_key in parsed:
                return parsed[db_key]
            stored = db.get_setting(db_key)
            try:
                return int(stored) if stored else fallback
            except (ValueError, TypeError):
                return fallback

        if effective('transcribe_chunk_overlap_seconds', 30) >= effective(
                'transcribe_max_chunk_seconds', 600):
            return None, None, (
                'must be less than transcribeMaxChunkSeconds',
                'transcribeChunkOverlapSeconds', 400)
    pool_touched = ('whisperPoolEnabled' in data
                    or 'whisper_pool_max_requests' in parsed
                    or 'whisper_pool_max_episodes' in parsed)
    return parsed, (pool_enabled, pool_touched), None


def _validate_transcribe_chunk_payload(db, data):
    _values, _pool, issue = _transcribe_chunk_values(db, data)
    if issue is None:
        return None
    message, field, status = issue
    if field == 'transcribeChunkOverlapSeconds':
        return f'{field} {message}', status
    return f'{field} {message}', status


def _apply_transcribe_chunk_fields(db, data):
    """Persist validated chunk and transcription-pool settings."""
    parsed, pool, issue = _transcribe_chunk_values(db, data)
    if issue is not None:
        message, field, status = issue
        if field == 'transcribeChunkOverlapSeconds':
            message = f'{field} {message}'
        else:
            message = f'{field} {message}'
        return error_response(message, status)
    pool_enabled, pool_touched = pool
    if pool_enabled is not None:
        db.set_setting('whisper_pool_enabled', 'true' if pool_enabled else 'false', is_default=False)
        logger.info(f"Updated whisper_pool_enabled to: {pool_enabled}")
    for db_key, value in parsed.items():
        db.set_setting(db_key, str(value), is_default=False)
        logger.info(f"Updated {db_key} to: {value}")
    if pool_touched:
        _after_commit(_refresh_whisper_pool)
    return None


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


def validate_settings_payload(
        db, data, *, allow_inactive_tunables=False, prospective_feed_modes=None):
    """Return a payload validation issue without writes, network access, or Flask context."""
    for validate in (
        _validate_processing_defaults_payload,
        lambda payload: _validate_failover_settings_payload(payload, db),
        _validate_provider_payload,
        _validate_review_payload,
        _validate_provider_routing_payload,
        _validate_model_pricing_payload,
        _validate_size_caps_payload,
        _validate_provider_rate_limit_payload,
        _validate_queue_boost_payload,
        _validate_rss_refresh_payload,
        _validate_audio_payload,
        _validate_user_agent_payload,
        _validate_podcast_search_provider_payload,
    ):
        issue = validate(data)
        if issue is not None:
            return issue
    issue = _validate_audio_cue_payload(db, data)
    if issue is not None:
        return issue
    issue = _validate_transcribe_chunk_payload(db, data)
    if issue is not None:
        return issue
    issue = _validate_max_ad_duration_payload(db, data)
    if issue is not None:
        return issue
    issue = _validate_detection_tuning_payload(db, data)
    if issue is not None:
        return issue
    _value, issue = _opening_exclusion_value(data)
    if issue is not None:
        return issue
    _value, issue = _ad_merge_value(data)
    if issue is not None:
        return issue
    _value, issue = _min_cut_confidence_value(data)
    if issue is not None:
        return issue
    issue = _validate_vad_gap_payload(data)
    if issue is not None:
        return issue
    for validate in (
        lambda payload: _validate_segment_actions_payload(db, payload),
        _validate_community_sync_payload,
        _validate_jit_blocked_user_agents_payload,
    ):
        issue = validate(data)
        if issue is not None:
            return issue
    issue = _validate_ad_chapter_payload(db, data)
    if issue is not None:
        return issue
    issue = _validate_systemone_phase_enables(db, data, prospective_feed_modes)
    if issue is not None:
        return issue
    issue = _validate_systemone_tunables_payload(db, data)
    if issue is not None:
        return issue
    issue = _validate_stage_tunables_payload(
        db, data, allow_inactive_tunables=allow_inactive_tunables)
    if issue is not None:
        return issue
    return None


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
        _after_commit(
            lambda: clear_holds_for_provider_change(
                db, 'LLM provider settings changed', affected_providers),
            bucket='holds')

    if provider_changed:
        # Clear the cached probe answers so the new endpoint gets re-probed:
        # a stored false against a model name the new endpoint also serves
        # would otherwise pin it to the fallback format forever.
        _clear_format_probes(db)
        _after_commit(_probe_after_provider_change)
    # The TTL cache backing get_effective_base_url / get_effective_provider
    # lags writes by up to 5s. Without this invalidation, the GET /settings
    # response that fires right after this PUT returns the pre-write value,
    # the UI re-hydrates state to the stale value, hasChanges flips back to
    # false, and the Save Changes button vanishes (issue #234).
    _after_commit(invalidate_provider_cache)
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
            _after_commit(lambda: failover.cancel(failover.TARGET_LLM_SECONDARY, source='manual'))
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
        _after_commit(invalidate_provider_cache)
        # No secondary type configured: nothing to lift, and a null provider
        # would target the unscoped blanket marker, which may be primary's.
        known = [p for p in affected_providers if p]
        _after_commit(
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
        _after_commit(invalidate_provider_cache)
        _after_commit(
            lambda: clear_holds_for_provider_change(
                db, 'System One provider settings changed', changed_providers,
                credential_slot=SLOT_PRIMARY),
            bucket='holds')
    return None


def _apply_failover_llm_fields(db, data):
    """Persist LLM failover settings and clear active state when disabled."""
    changed = False
    if 'failoverLlmEnabled' in data:
        enabled = data['failoverLlmEnabled']
        db.set_setting('failover_llm_enabled', 'true' if enabled else 'false', is_default=False)
        if not enabled:
            for target in (failover.TARGET_LLM_PRIMARY, failover.TARGET_LLM_SECONDARY):
                _after_commit(lambda t=target: failover.cancel(t, source='manual'))
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
        _after_commit(invalidate_provider_cache)
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
            _after_commit(lambda: failover.cancel(failover.TARGET_WHISPER, source='manual'))
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
        _after_commit(_refresh_whisper_pool)
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
        _after_commit(_refresh_whisper_pool)

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
        _after_commit(_mark_whisper_for_reload)
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


def _apply_vad_gap_fields(db, data):
    """Persist VAD gap-detection toggle plus the three positive-float thresholds."""
    writes, issue = _vad_gap_writes(data)
    if issue is not None:
        return error_response(*issue)
    if 'vadGapDetectionEnabled' in data:
        enabled = coerce_bool_setting(data['vadGapDetectionEnabled'])
        db.set_setting('vad_gap_detection_enabled', 'true' if enabled else 'false', is_default=False)
        logger.info(f"Updated vad_gap_detection_enabled to: {enabled}")
    for db_key, value in writes:
        db.set_setting(db_key, str(value), is_default=False)
        logger.info(f"Updated {db_key} to: {value}")
    return None


def _vad_gap_writes(data):
    writes = []
    for field_name, db_key in (
        ('vadGapStartMinSeconds', 'vad_gap_start_min_seconds'),
        ('vadGapMidMinSeconds', 'vad_gap_mid_min_seconds'),
        ('vadGapTailMinSeconds', 'vad_gap_tail_min_seconds'),
    ):
        if field_name not in data:
            continue
        try:
            value = float(data[field_name])
        except (TypeError, ValueError):
            return None, (f'{field_name} must be a positive number', 400)
        if not math.isfinite(value) or value <= 0:
            return None, (f'{field_name} must be a positive number', 400)
        writes.append((db_key, value))
    return writes, None


def _validate_vad_gap_payload(data):
    _writes, issue = _vad_gap_writes(data)
    return issue


def _apply_opening_exclusion_fields(db, data):
    value, issue = _opening_exclusion_value(data)
    if issue is not None:
        return error_response(*issue)
    if value is None:
        return None
    db.set_setting('ad_detection_exclude_start_seconds', str(value), is_default=False)
    logger.info(f"Updated ad_detection_exclude_start_seconds to: {value}")
    return None


def _opening_exclusion_value(data):
    if 'adDetectionExcludeStartSeconds' not in data:
        return None, None
    try:
        value = float(data['adDetectionExcludeStartSeconds'])
    except (TypeError, ValueError):
        return None, ('adDetectionExcludeStartSeconds must be a number', 400)
    if not math.isfinite(value) or value < 0 or value > 600:
        return None, ('adDetectionExcludeStartSeconds must be between 0 and 600', 400)
    return value, None


def _apply_ad_merge_fields(db, data):
    """Persist the ad filler-gap merge threshold (#458)."""
    value, issue = _ad_merge_value(data)
    if issue is not None:
        return error_response(*issue)
    if value is None:
        return None
    db.set_setting('min_content_between_ads_seconds', str(value), is_default=False)
    logger.info(f"Updated min_content_between_ads_seconds to: {value}")
    return None


def _ad_merge_value(data):
    if 'minContentBetweenAdsSeconds' not in data:
        return None, None
    try:
        value = float(data['minContentBetweenAdsSeconds'])
    except (TypeError, ValueError):
        return None, ('minContentBetweenAdsSeconds must be a number', 400)
    if not math.isfinite(value) or value < 0 or value > 60:
        return None, ('minContentBetweenAdsSeconds must be between 0 and 60', 400)
    return value, None


def _max_ad_duration_values(db, data):
    writes = []
    values = {}
    if not any(key in data for key in (
            'maxAdDurationSeconds', 'maxAdDurationConfirmedSeconds')):
        return writes, None
    for payload_key, db_key, default in (
        ('maxAdDurationSeconds', 'max_ad_duration_seconds', MAX_AD_DURATION),
        ('maxAdDurationConfirmedSeconds', 'max_ad_duration_confirmed_seconds',
         MAX_AD_DURATION_CONFIRMED),
    ):
        if payload_key not in data:
            values[payload_key] = db.get_setting_float(db_key, default)
            continue
        try:
            value = float(data[payload_key])
        except (TypeError, ValueError):
            return None, (f'{payload_key} must be a number', 400)
        if not math.isfinite(value) or value < 30.0 or value > 3600.0:
            return None, (f'{payload_key} must be between 30 and 3600', 400)
        values[payload_key] = value
        writes.append((db_key, value))
    if values['maxAdDurationSeconds'] > values['maxAdDurationConfirmedSeconds']:
        return None, (
            'maxAdDurationSeconds cannot exceed maxAdDurationConfirmedSeconds', 400)
    return writes, None


def _validate_max_ad_duration_payload(db, data):
    _writes, issue = _max_ad_duration_values(db, data)
    return issue


def _apply_max_ad_duration_fields(db, data):
    writes, issue = _max_ad_duration_values(db, data)
    if issue is not None:
        return error_response(*issue)
    for setting, value in writes:
        db.set_setting(setting, str(value), is_default=False)
        logger.info(f"Updated {setting} to: {value}")
    return None


def _audio_cue_writes(db, data):
    from config import AUDIO_CUE_FREQ_MIN_HZ, AUDIO_CUE_FREQ_MAX_HZ

    writes = []
    for payload_key, db_key in (
        ('audioCueDetectionEnabled', 'audio_cue_detection_enabled'),
        ('audioCueCreateFromPairs', 'audio_cue_create_from_pairs'),
    ):
        if payload_key in data:
            enabled = coerce_bool_setting(data[payload_key])
            writes.append((db_key, 'true' if enabled else 'false'))

    parsed = {}
    for field_name, db_key, low, high in (
        ('audioCueFreqMinHz', 'audio_cue_freq_min_hz', 20.0, 20000.0),
        ('audioCueFreqMaxHz', 'audio_cue_freq_max_hz', 20.0, 20000.0),
        ('audioCueProminenceDb', 'audio_cue_prominence_db', 1.0, 40.0),
        ('audioCueMinConfidence', 'audio_cue_min_confidence', 0.0, 1.0),
        ('audioCueTemplateScore', 'audio_cue_template_score', 0.0, 0.99),
        ('audioCueFormantAttenDb', 'audio_cue_formant_atten_db', 0.0, 24.0),
        ('audioCueSnapConfidence', 'audio_cue_snap_confidence', 0.0, 1.0),
        ('audioCueSnapLeadSeconds', 'audio_cue_snap_lead_seconds', 0.5, 30.0),
        ('audioCueSnapLagSeconds', 'audio_cue_snap_lag_seconds', 0.5, 30.0),
        ('audioCueCaptureMinSeconds', 'audio_cue_capture_min_seconds', 0.05, 10.0),
        ('audioCueCaptureMaxSeconds', 'audio_cue_capture_max_seconds', 0.05, 30.0),
        ('audioCueCaptureMaxIntroSeconds', 'audio_cue_capture_max_intro_seconds', 0.05, 120.0),
        ('audioCueCaptureMaxOutroSeconds', 'audio_cue_capture_max_outro_seconds', 0.05, 120.0),
        ('audioCuePairConfidence', 'audio_cue_pair_confidence', 0.0, 1.0),
        ('audioCuePairMinBreakSeconds', 'audio_cue_pair_min_break_seconds', 1.0, 600.0),
        ('audioCuePairMaxBreakSeconds', 'audio_cue_pair_max_break_seconds', 1.0, 3600.0),
        ('audioCuePairMaxBreakFraction', 'audio_cue_pair_max_break_fraction', 0.0, 1.0),
        ('audioCuePairOrientWindowSeconds', 'audio_cue_pair_orient_window_seconds', 0.0, 120.0),
        ('silenceSnapNoiseDb', 'silence_snap_noise_db', -90.0, -20.0),
        ('silenceSnapMinDurationSeconds', 'silence_snap_min_duration_seconds', 0.1, 5.0),
        ('silenceSnapMaxDistanceSeconds', 'silence_snap_max_distance_seconds', 0.25, 10.0),
    ):
        if field_name not in data:
            continue
        try:
            value = float(data[field_name])
        except (TypeError, ValueError):
            return None, (f'{field_name} must be a number', 400)
        if not math.isfinite(value) or value < low or value > high:
            return None, (f'{field_name} must be between {low} and {high}', 400)
        if field_name in ('audioCueFreqMinHz', 'audioCueFreqMaxHz'):
            if not value.is_integer():
                return None, (f'{field_name} must be an integer', 400)
            value = int(value)
        parsed[field_name] = value
        writes.append((db_key, str(value)))

    if 'audioCueFreqMinHz' in parsed or 'audioCueFreqMaxHz' in parsed:
        try:
            freq_min = parsed.get('audioCueFreqMinHz', float(
                db.get_setting('audio_cue_freq_min_hz') or AUDIO_CUE_FREQ_MIN_HZ))
            freq_max = parsed.get('audioCueFreqMaxHz', float(
                db.get_setting('audio_cue_freq_max_hz') or AUDIO_CUE_FREQ_MAX_HZ))
        except (TypeError, ValueError):
            return None, ('Stored audio cue frequency is invalid', 400)
        if freq_min >= freq_max:
            return None, ('audioCueFreqMinHz must be below audioCueFreqMaxHz', 400)
    return writes, None


def _validate_audio_cue_payload(db, data):
    _writes, issue = _audio_cue_writes(db, data)
    return issue


def _apply_audio_cue_fields(db, data):
    writes, issue = _audio_cue_writes(db, data)
    if issue is not None:
        return error_response(*issue)
    for db_key, value in writes:
        db.set_setting(db_key, value, is_default=False)
        logger.info(f"Updated {db_key} to: {value}")
    return None


def _detection_tuning_writes(db, data):
    writes = []
    if 'verificationMissAutocutMinConfidence' in data:
        try:
            value = float(data['verificationMissAutocutMinConfidence'])
        except (TypeError, ValueError):
            return None, ('verificationMissAutocutMinConfidence must be a number', 400)
        if not math.isfinite(value) or (value != 0 and not (0.5 <= value <= 1.0)):
            return None, (
                'verificationMissAutocutMinConfidence must be 0 or between 0.5 and 1.0', 400)
        writes.append(('verification_miss_autocut_min_confidence', str(value)))

    for field_name, db_key, low, high in (
        ('verificationMissHoldMinConfidence', 'verification_miss_hold_min_confidence', 0.0, 1.0),
        ('learningMinConfidence', 'learning_min_confidence', 0.5, 1.0),
        ('learningMinConfidenceLong', 'learning_min_confidence_long', 0.5, 1.0),
        ('differentialMeasuredCorrMax', 'differential_measured_corr_max', 0.0, 1.0),
        ('differentialHoldMinSeconds', 'differential_hold_min_seconds', 0.0, 120.0),
    ):
        if field_name not in data:
            continue
        try:
            value = float(data[field_name])
        except (TypeError, ValueError):
            return None, (f'{field_name} must be a number', 400)
        if not math.isfinite(value) or value < low or value > high:
            return None, (f'{field_name} must be between {low} and {high}', 400)
        writes.append((db_key, str(value)))

    bounds = {}
    for field_name, db_key, low, high in (
        ('learningMinPatternDuration', 'learning_min_pattern_duration', 1, 600),
        ('learningMaxPatternDuration', 'learning_max_pattern_duration', 1, 1800),
    ):
        if field_name not in data:
            continue
        try:
            seconds = int(data[field_name])
        except (TypeError, ValueError):
            return None, (f'{field_name} must be an integer', 400)
        if seconds < low or seconds > high:
            return None, (f'{field_name} must be between {low} and {high}', 400)
        bounds[db_key] = seconds
        writes.append((db_key, str(seconds)))

    if bounds:
        low = bounds.get('learning_min_pattern_duration')
        if low is None:
            low = db.get_setting_int(
                'learning_min_pattern_duration', int(registry_get_default('learning_min_pattern_duration')))
        high = bounds.get('learning_max_pattern_duration')
        if high is None:
            high = db.get_setting_int(
                'learning_max_pattern_duration', int(registry_get_default('learning_max_pattern_duration')))
        if low >= high:
            return None, (
                'learningMinPatternDuration must be below learningMaxPatternDuration', 400)
    return writes, None


def _validate_detection_tuning_payload(db, data):
    _writes, issue = _detection_tuning_writes(db, data)
    return issue


def _apply_detection_tuning_fields(db, data):
    writes, issue = _detection_tuning_writes(db, data)
    if issue is not None:
        return error_response(*issue)
    for db_key, value in writes:
        db.set_setting(db_key, value, is_default=False)
        logger.info(f"Updated {db_key} to: {value}")
    return None


def _apply_positional_prior_fields(db, data):
    """Persist the learned positional prior experiment toggle (#360)."""
    if 'positionalPriorEnabled' in data:
        enabled = coerce_bool_setting(data['positionalPriorEnabled'])
        db.set_setting('positional_prior_enabled', 'true' if enabled else 'false', is_default=False)
        logger.info(f"Updated positional_prior_enabled to: {enabled}")

    if 'daiDifferentialOverridesKeep' in data:
        enabled = coerce_bool_setting(data['daiDifferentialOverridesKeep'])
        db.set_setting('dai_differential_overrides_keep',
                       'true' if enabled else 'false', is_default=False)
        logger.info(f"Updated dai_differential_overrides_keep to: {enabled}")

    if 'spliceVetoEnabled' in data:
        enabled = coerce_bool_setting(data['spliceVetoEnabled'])
        db.set_setting('splice_veto_enabled', 'true' if enabled else 'false', is_default=False)
        logger.info(f"Updated splice_veto_enabled to: {enabled}")
    return


def _apply_podcast_index_fields(db, data):
    """Persist podcast search provider choice and PodcastIndex credentials."""
    if 'podcastSearchProvider' in data:
        provider = data['podcastSearchProvider']
        if provider not in PODCAST_SEARCH_PROVIDERS:
            return json_response(
                {'error': f'podcastSearchProvider must be one of: '
                          f'{", ".join(PODCAST_SEARCH_PROVIDERS)}'}, 400)
        db.set_setting('podcast_search_provider', provider, is_default=False)
        logger.info(f"Updated podcast search provider to: {provider}")

    if 'podcastIndexApiKey' in data:
        try:
            set_or_clear_secret(db, 'podcast_index_api_key', data['podcastIndexApiKey'])
        except SecretWriteRejected:
            return error_response('provider_crypto_unavailable', 409)
        logger.info("Updated Podcast Index API key")

    if 'podcastIndexApiSecret' in data:
        try:
            set_or_clear_secret(db, 'podcast_index_api_secret', data['podcastIndexApiSecret'])
        except SecretWriteRejected:
            return error_response('provider_crypto_unavailable', 409)
        logger.info("Updated Podcast Index API secret")
    return None


def _validate_podcast_search_provider_payload(data):
    if ('podcastSearchProvider' in data
            and data['podcastSearchProvider'] not in PODCAST_SEARCH_PROVIDERS):
        return (
            f'podcastSearchProvider must be one of: {", ".join(PODCAST_SEARCH_PROVIDERS)}', 400)
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


def _stage_tunable_values(db, data, *, allow_inactive_tunables=False):
    from config import (
        STAGE_TUNABLE_PAYLOAD_KEYS, STAGE_TUNABLE_RANGES,
        STAGE_TUNABLE_REASONING_LEVELS, get_stage_tunable,
    )

    settings = db.get_all_settings()

    def effective(payload_key, db_key):
        if payload_key in data:
            raw = data[payload_key]
            if raw is None or (isinstance(raw, str) and raw.strip() == ''):
                reset_settings = {key: value for key, value in settings.items()
                                  if key != db_key}
                return get_stage_tunable(db_key, settings=reset_settings)
            try:
                return int(raw)
            except (TypeError, ValueError):
                return None
        return get_stage_tunable(db_key, settings=settings)

    if 'windowSizeSeconds' in data or 'windowOverlapSeconds' in data:
        size = effective('windowSizeSeconds', 'window_size_seconds')
        overlap = effective('windowOverlapSeconds', 'window_overlap_seconds')
        if size is not None and overlap is not None and overlap >= size:
            return None, ('windowOverlapSeconds must be less than windowSizeSeconds', 400)

    chapter_keys = ('chapterTargetSeconds', 'chapterWindowSeconds',
                    'chapterMinDurationSeconds')
    if any(key in data for key in chapter_keys):
        target = effective('chapterTargetSeconds', 'chapter_target_seconds')
        window = effective('chapterWindowSeconds', 'chapter_window_seconds')
        minimum = effective('chapterMinDurationSeconds', 'chapter_min_duration_seconds')
        if target is not None and window is not None and target > window:
            return None, ('chapterTargetSeconds must not exceed chapterWindowSeconds', 400)
        if minimum is not None and target is not None and minimum > target:
            return None, ('chapterMinDurationSeconds must not exceed chapterTargetSeconds', 400)

    def coerce_int(raw):
        return int(raw)

    def coerce_float(raw):
        return float(raw)

    def coerce_level(raw):
        normalized = str(raw).strip().lower()
        if normalized not in STAGE_TUNABLE_REASONING_LEVELS:
            raise ValueError(
                f"must be one of: {', '.join(sorted(STAGE_TUNABLE_REASONING_LEVELS))}")
        return normalized

    kind_rules = {
        'float': (coerce_float, 'a number', None, True),
        'int': (coerce_int, 'an integer', None, True),
        'budget': (coerce_int, 'an integer', None, True),
        'level': (coerce_level, None, None, False),
        'ollama_ctx': (coerce_int, 'an integer', 'ollama', True),
    }
    provider = None
    normalized_values = {}
    for payload_key, db_key, kind in STAGE_TUNABLE_PAYLOAD_KEYS:
        if payload_key not in data:
            continue
        raw = data[payload_key]
        if raw is None or (isinstance(raw, str) and raw.strip() == ''):
            normalized_values[db_key] = None
            continue
        coerce, type_message, gate, check_range = kind_rules[kind]
        if gate is not None and not allow_inactive_tunables:
            if provider is None:
                provider = _effective_provider_after_update(data, db)
            gated_error = None
            if gate == 'anthropic' and provider != 'anthropic':
                gated_error = f'{payload_key} is only valid when llmProvider is anthropic'
            elif gate == 'not_anthropic' and provider == 'anthropic':
                gated_error = f'{payload_key} is not valid when llmProvider is anthropic'
            elif gate == 'ollama' and provider != 'ollama':
                gated_error = f'{payload_key} is only valid when llmProvider is ollama'
            if gated_error:
                return None, (gated_error, 400)
        try:
            value = coerce(raw)
        except (TypeError, ValueError) as exc:
            message = f'{payload_key} must be {type_message}' if type_message else f'{payload_key} {exc}'
            return None, (message, 400)
        if check_range:
            low, high = STAGE_TUNABLE_RANGES[db_key]
            if not low <= value <= high:
                return None, (f'{payload_key} must be between {low} and {high}', 400)
        normalized_values[db_key] = value
    return normalized_values, None


def _validate_stage_tunables_payload(db, data, *, allow_inactive_tunables=False):
    _values, issue = _stage_tunable_values(
        db, data, allow_inactive_tunables=allow_inactive_tunables)
    return issue


def _apply_stage_tunables(db, data, *, allow_inactive_tunables=False):
    """Persist validated per-stage tunables."""
    values, issue = _stage_tunable_values(
        db, data, allow_inactive_tunables=allow_inactive_tunables)
    if issue is not None:
        return error_response(*issue)
    for db_key, value in values.items():
        if value is None:
            db.set_setting(db_key, '', is_default=True)
            logger.info(f"Cleared {db_key}")
        else:
            db.set_setting(db_key, str(value), is_default=False)
            logger.info(f"Updated {db_key} to: {value!r}")
    return None


@api.route('/settings/ad-detection/reset', methods=['POST'])
@log_request
def reset_ad_detection_settings():
    """Reset ad detection settings to defaults.

    The key list derives from SETTINGS_REGISTRY (entries flagged
    ``in_ad_reset``): prompts, models, whisper/VAD, the audio-cue family,
    env-backed keys, the two provider secrets, and all stage tunables.
    """
    db = get_database()

    for key in AD_RESET_SETTING_KEYS:
        db.reset_setting(key)
    _clear_format_probes(db)

    # Recreate LLM client with reset settings
    client = get_llm_client(force_new=True)
    if hasattr(client, 'probe_json_format_support'):
        client.probe_json_format_support()

    _mark_whisper_for_reload()

    logger.info("Reset all settings to defaults")
    return json_response({'message': 'Settings reset to defaults'})
