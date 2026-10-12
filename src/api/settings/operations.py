"""Operational settings routes: backup, retention, queue, rate limit hold, audio and more."""

import json
import logging
import sqlite3
from flask import request, send_file
import replacement_audio
from api import (
    api, log_request, json_response, error_response,
    get_database, limiter,
)
from config import (
    SEGMENT_CATEGORIES, resolve_community_sync_categories,
)
from api.pattern_cleanup import cleanup_settings_view
from offline_queue import (
    get_offline_queue_ttl_hours, is_offline_queue_enabled,
    TTL_HOURS_MIN, TTL_HOURS_MAX,
)
from rate_limit_hold import (
    get_any_active_hold, is_rate_limit_hold_enabled,
    any_hold_active, clear_all_holds,
    get_llm_usage_url, get_rate_limit_probe_minutes,
    RATE_LIMIT_PROBE_MINUTES_MIN, RATE_LIMIT_PROBE_MINUTES_MAX,
)
from llm_route import VALID_SLOTS, SAME_AS_DETECTION
from pattern_cleanup import BATCH_SIZE_RANGE, UNUSED_DAYS_RANGE
from utils.url import SSRFError, validate_base_url
from webhook_service import fire_queue_resumed_event
from db_backup_service import (
    DEFAULT_CRON, KEEP_COUNT_MAX, KEEP_COUNT_MIN, dest_writable,
    validate_backup_dest,
)
from utils.cron import is_valid_expression
from utils.time import utc_now_iso
from api.settings import common

logger = logging.getLogger('podcast.api')


@api.route('/settings/feed-auth/regenerate-key', methods=['POST'])
@limiter.limit("3 per hour")
@log_request
def regenerate_feed_auth_key():
    """Rotate the global feed auth key (authenticated feeds).

    409 while the feature is disabled - rotation of a dormant key would be
    invisible and surprising on re-enable. The old key is rejected on the
    very next request (enforcement reads the DB per request; no caching).
    Subscribed apps must be re-added with the new key (OPML export carries
    it). Returns the new key - it is a bearer credential the operator needs.
    """
    from main_app.feed_auth import generate_feed_key

    db = get_database()
    if not db.get_setting_bool('feed_auth_enabled', False):
        return error_response('feed auth is not enabled', 409)
    new_key = generate_feed_key()
    db.set_setting('feed_auth_key', new_key, is_default=False)
    db.clear_all_podcast_etags()
    logger.info("Feed auth key regenerated")
    return json_response({'feedAuthKey': new_key})


@api.route('/settings/retention', methods=['GET'])
@log_request
def get_retention_settings():
    """Get retention configuration.

    `originalRetentionDays` defaults to whatever `retentionDays` is when the
    operator has never set it explicitly; that keeps the two values matched
    by default and lets `retention_days` changes propagate without forcing
    the operator to touch both fields.
    """
    db = get_database()
    retention_days = int(db.get_setting('retention_days') or '30')
    original_raw = db.get_setting('original_retention_days')
    original_retention_days = (
        int(original_raw) if original_raw else retention_days
    )
    return json_response({
        'retentionDays': retention_days,
        'originalRetentionDays': original_retention_days,
        'enabled': retention_days > 0,
    })


def _clamp_original_retention(retention_days: int, original: int) -> int:
    """Clamp `original` so an original cannot outlive its processed peer.

    When retention is disabled (`retention_days == 0`), there is nothing
    to clamp to; the operator's stored original value is kept as-is.
    Otherwise the original is capped at `retention_days`.
    """
    if retention_days <= 0:
        return original
    return min(original, retention_days)


@api.route('/settings/retention', methods=['PUT'])
@log_request
def update_retention_settings():
    """Update retention configuration.

    Server-side clamp: `originalRetentionDays` is capped to `retentionDays`
    because an original outliving its processed file would be orphaned the
    moment the next cleanup pass resets the episode.
    """
    data = request.get_json()
    if not data or 'retentionDays' not in data:
        return error_response('retentionDays is required', 400)

    days = data['retentionDays']
    if not isinstance(days, int) or isinstance(days, bool) or days < 0 or days > 3650:
        return error_response('retentionDays must be an integer between 0 and 3650', 400)

    # Stage the whole payload before writing: a bad optional field must not
    # leave retention_days persisted behind a 400.
    staged = {'retention_days': str(days)}
    # original_retention_days is optional; absent => match retention_days.
    original_days = days  # response default
    if 'originalRetentionDays' in data:
        original = data['originalRetentionDays']
        if (not isinstance(original, int) or isinstance(original, bool)
                or original < 1 or original > 3650):
            return error_response(
                'originalRetentionDays must be an integer between 1 and 3650', 400
            )
        # Clamp; never let original outlive processed.
        original_days = _clamp_original_retention(days, original)
        staged['original_retention_days'] = str(original_days)

    db = get_database()
    try:
        with db.settings_transaction():
            for key, value in staged.items():
                db.set_setting(key, value, is_default=False)
    except sqlite3.Error:
        logger.exception("Retention save failed; no field was persisted")
        return error_response('Settings could not be saved', 500)

    logger.info(f"Updated retention_days to {days}")
    if 'original_retention_days' in staged:
        logger.info(f"Updated original_retention_days to {original_days}")

    return json_response({
        'retentionDays': days,
        'originalRetentionDays': original_days,
        'enabled': days > 0,
    })


def _offline_queue_view(db) -> dict:
    """Offline queue settings payload shared by GET and PUT (#482)."""
    return {
        'enabled': is_offline_queue_enabled(db),
        'ttlHours': get_offline_queue_ttl_hours(db),
        'deferredCount': db.count_deferred_episodes(),
    }


@api.route('/settings/offline-queue', methods=['GET'])
@log_request
def get_offline_queue_settings():
    """Get offline queue configuration (#482)."""
    return json_response(_offline_queue_view(get_database()))


@api.route('/settings/offline-queue', methods=['PUT'])
@log_request
def update_offline_queue_settings():
    """Update offline queue configuration (#482).

    When enabled, episodes that fail because the LLM provider or Whisper
    endpoint is unreachable wait in a queue and process automatically once
    the service is back. ttlHours bounds how long they wait before being
    marked permanently failed.
    """
    data = request.get_json()
    db = get_database()
    error = _apply_toggle_ttl_update(db, data, 'offline_queue')
    if error:
        return error
    view = _offline_queue_view(db)
    logger.info(f"Updated offline_queue_enabled: {view['enabled']}")
    return json_response(view)


def _apply_enabled_update(db, data, prefix: str):
    """Validate and store {enabled} for a queue-wait feature.

    `prefix` is the settings key stem ('offline_queue', 'rate_limit_hold').
    Returns an error response on invalid input, else None.
    """
    if not isinstance(data, dict) or not data:
        return error_response('No data provided', 400)

    if 'enabled' in data:
        if not isinstance(data['enabled'], bool):
            return error_response('enabled must be a boolean', 400)
        db.set_setting(f'{prefix}_enabled',
                       'true' if data['enabled'] else 'false', is_default=False)
    return None


def _apply_toggle_ttl_update(db, data, prefix: str):
    """_apply_enabled_update plus the ttlHours give-up window (#482)."""
    error = _apply_enabled_update(db, data, prefix)
    if error:
        return error

    if 'ttlHours' in data:
        ttl = data['ttlHours']
        if not isinstance(ttl, int) or isinstance(ttl, bool) \
                or ttl < TTL_HOURS_MIN or ttl > TTL_HOURS_MAX:
            return error_response(
                f'ttlHours must be an integer between {TTL_HOURS_MIN} and {TTL_HOURS_MAX}', 400)
        db.set_setting(f'{prefix}_ttl_hours', str(ttl), is_default=False)
    return None


def _rate_limit_hold_view(db) -> dict:
    """Rate-limit hold settings payload shared by GET and PUT (#696)."""
    return {
        'enabled': is_rate_limit_hold_enabled(db),
        'holdUntil': get_any_active_hold(db)[0],
        'llmUsageUrl': get_llm_usage_url(db),
        'rateLimitProbeMinutes': get_rate_limit_probe_minutes(db),
    }


@api.route('/settings/rate-limit-hold', methods=['GET'])
@log_request
def get_rate_limit_hold_settings():
    """Get rate-limit hold configuration (#696)."""
    return json_response(_rate_limit_hold_view(get_database()))


@api.route('/settings/rate-limit-hold', methods=['PUT'])
@log_request
def update_rate_limit_hold_settings():
    """Update rate-limit hold configuration (#696).

    When enabled, a provider 429 carrying a reset time sends the episode
    back to the queue and pauses new claims until the reset instead of
    failing the job. llmUsageUrl and rateLimitProbeMinutes configure the
    probe that periodically re-checks an active hold.
    """
    data = request.get_json()
    db = get_database()
    error = _apply_enabled_update(db, data, 'rate_limit_hold')
    if error:
        return error
    if 'llmUsageUrl' in data:
        usage_url = data['llmUsageUrl']
        if usage_url:
            try:
                validate_base_url(usage_url)
            except SSRFError as e:
                return json_response({'error': f'Invalid LLM usage URL: {e}'}, 400)
        db.set_setting('llm_usage_url', usage_url, is_default=False)
    if 'rateLimitProbeMinutes' in data:
        minutes = data['rateLimitProbeMinutes']
        if not isinstance(minutes, int) or isinstance(minutes, bool) \
                or minutes < RATE_LIMIT_PROBE_MINUTES_MIN or minutes > RATE_LIMIT_PROBE_MINUTES_MAX:
            return error_response(
                'rateLimitProbeMinutes must be an integer between '
                f'{RATE_LIMIT_PROBE_MINUTES_MIN} and {RATE_LIMIT_PROBE_MINUTES_MAX}', 400)
        db.set_setting('rate_limit_probe_minutes', str(minutes), is_default=False)
    if data.get('enabled') is False and any_hold_active(db):
        # Escape hatch: turning the hold off lifts every active pause,
        # legacy and provider-scoped, not just the default provider's.
        fire_queue_resumed_event(held_since=clear_all_holds(db))
    view = _rate_limit_hold_view(db)
    logger.info(f"Updated rate_limit_hold_enabled: {view['enabled']}")
    return json_response(view)


@api.route('/settings/rate-limit-hold/reset', methods=['POST'])
@log_request
def reset_rate_limit_hold():
    """Lift every active rate-limit hold while leaving the feature enabled."""
    db = get_database()
    if any_hold_active(db):
        fire_queue_resumed_event(held_since=clear_all_holds(db))
        logger.info("Manually reset rate-limit holds")
    return json_response(_rate_limit_hold_view(db))


# ========== Update check settings ==========

@api.route('/settings/update-check', methods=['GET'])
@log_request
def get_update_check_settings():
    """Get update-check settings (channel + enabled/disabled)."""
    db = get_database()
    return json_response({
        'enabled': db.get_setting_bool('update_check_enabled', default=True),
        'channel': db.get_setting('update_channel') or 'stable',
    })


@api.route('/settings/update-check', methods=['PUT'])
@log_request
def put_update_check_settings():
    """Update update-check settings. Body: {enabled?, channel?}."""
    data = request.get_json(silent=True) or {}
    if 'channel' in data and data['channel'] not in ('stable', 'edge'):
        return error_response('channel must be stable or edge', 400)
    db = get_database()
    if 'enabled' in data:
        db.set_setting('update_check_enabled',
                       'true' if bool(data['enabled']) else 'false', is_default=False)
        logger.info(f"Updated update_check_enabled to: {bool(data['enabled'])}")
    if 'channel' in data:
        db.set_setting('update_channel', data['channel'], is_default=False)
        logger.info(f"Updated update_channel to: {data['channel']}")
    return get_update_check_settings()


@api.route('/settings/audio', methods=['GET'])
@log_request
def get_audio_settings():
    """Get audio-related settings (currently: keep original audio)."""
    db = get_database()
    raw = db.get_setting('keep_original_audio')
    keep = (raw or 'true').lower() != 'false'
    return json_response({'keepOriginalAudio': keep})


@api.route('/settings/audio', methods=['PUT'])
@log_request
def update_audio_settings():
    """Update audio-related settings."""
    data = request.get_json() or {}
    if 'keepOriginalAudio' not in data:
        return error_response('keepOriginalAudio is required', 400)
    keep = data['keepOriginalAudio']
    if not isinstance(keep, bool):
        return error_response('keepOriginalAudio must be a boolean', 400)
    db = get_database()
    db.set_setting('keep_original_audio', 'true' if keep else 'false', is_default=False)
    logger.info(f"Updated keep_original_audio to {keep}")
    return json_response({'keepOriginalAudio': keep})


@api.route('/settings/processing-timeouts', methods=['GET'])
@log_request
def get_processing_timeouts():
    """Get processing timeout configuration."""
    from processing_timeouts import (
        get_soft_timeout, get_hard_timeout,
        DEFAULT_SOFT_SECONDS, DEFAULT_HARD_SECONDS,
        SOFT_MIN, HARD_MAX,
    )
    return json_response({
        'softTimeoutSeconds': get_soft_timeout(),
        'hardTimeoutSeconds': get_hard_timeout(),
        'defaults': {
            'softTimeoutSeconds': DEFAULT_SOFT_SECONDS,
            'hardTimeoutSeconds': DEFAULT_HARD_SECONDS,
        },
        'limits': {
            'softMin': SOFT_MIN,
            'hardMax': HARD_MAX,
        },
    })


@api.route('/settings/processing-timeouts', methods=['PUT'])
@log_request
def update_processing_timeouts():
    """Update processing timeout configuration."""
    from processing_timeouts import validate, invalidate_cache
    data = request.get_json() or {}
    if 'softTimeoutSeconds' not in data or 'hardTimeoutSeconds' not in data:
        return error_response('softTimeoutSeconds and hardTimeoutSeconds are required', 400)

    soft = data['softTimeoutSeconds']
    hard = data['hardTimeoutSeconds']
    err = validate(soft, hard)
    if err:
        return error_response(err, 400)

    db = get_database()
    db.set_setting('processing_soft_timeout_seconds', str(soft), is_default=False)
    db.set_setting('processing_hard_timeout_seconds', str(hard), is_default=False)
    invalidate_cache()
    logger.info(f"Updated processing timeouts: soft={soft}s hard={hard}s")
    return json_response({
        'softTimeoutSeconds': soft,
        'hardTimeoutSeconds': hard,
    })


# ========== Ad Reviewer settings ==========

@api.route('/settings/reviewer', methods=['GET'])
@log_request
def get_reviewer_settings():
    """Return the ad-reviewer auto-update settings."""
    from config import (
        AD_REVIEWER_PARALLEL_ADS_DEFAULT,
        AD_REVIEWER_PARALLEL_ADS_MIN,
        AD_REVIEWER_PARALLEL_ADS_MAX,
    )
    db = get_database()
    parallel_ads = common._clamped_int(
        db.get_setting('ad_reviewer_parallel_ads'),
        AD_REVIEWER_PARALLEL_ADS_DEFAULT,
        AD_REVIEWER_PARALLEL_ADS_MIN, AD_REVIEWER_PARALLEL_ADS_MAX,
    )
    return json_response({
        'updatePatternsFromReviewerAdjustments': db.get_setting_bool(
            'update_patterns_from_reviewer_adjustments', default=True
        ),
        'minTrimThreshold': db.get_setting_float('min_trim_threshold', default=20.0),
        'parallelAds': parallel_ads,
        'parallelAdsDefault': AD_REVIEWER_PARALLEL_ADS_DEFAULT,
    })


@api.route('/settings/reviewer', methods=['PUT'])
@log_request
def update_reviewer_settings():
    """Update the ad-reviewer auto-update settings.

    Body: {updatePatternsFromReviewerAdjustments: bool, minTrimThreshold: float,
           parallelAds: int}
    """
    from config import (
        AD_REVIEWER_PARALLEL_ADS_MIN,
        AD_REVIEWER_PARALLEL_ADS_MAX,
    )
    db = get_database()
    data = request.get_json() or {}
    if 'updatePatternsFromReviewerAdjustments' in data:
        v = bool(data['updatePatternsFromReviewerAdjustments'])
        db.set_setting('update_patterns_from_reviewer_adjustments', 'true' if v else 'false')
    if 'minTrimThreshold' in data:
        try:
            v = float(data['minTrimThreshold'])
        except (TypeError, ValueError):
            return error_response('minTrimThreshold must be a number', 400)
        if v <= 0 or v > 120:
            return error_response('minTrimThreshold must be between 1 and 120', 400)
        db.set_setting('min_trim_threshold', str(v))
    if 'parallelAds' in data:
        try:
            n = int(data['parallelAds'])
        except (TypeError, ValueError):
            return error_response('parallelAds must be an integer', 400)
        if not (AD_REVIEWER_PARALLEL_ADS_MIN <= n <= AD_REVIEWER_PARALLEL_ADS_MAX):
            return error_response(
                f'parallelAds must be between {AD_REVIEWER_PARALLEL_ADS_MIN} '
                f'and {AD_REVIEWER_PARALLEL_ADS_MAX}',
                400,
            )
        db.set_setting('ad_reviewer_parallel_ads', str(n), is_default=False)
    return get_reviewer_settings()


# ========== Community-pattern sync settings ==========

def _community_category_breakdown(db) -> dict[str, int]:
    """Per-category counts of active community patterns, resolved the same way
    community_sync filters, so an unset category counts as sponsor there too."""
    breakdown = {cat: 0 for cat in SEGMENT_CATEGORIES}
    for pattern in db.get_patterns_by_source('community', active_only=True):
        category = pattern.get('category')
        # Folded rather than passed to normalize_segment_category, whose
        # docstring rules it out for a displayed count. The fold is still right
        # here: community_sync's filter treats unset as sponsor, so this is the
        # number of patterns the sponsor toggle actually syncs.
        breakdown[category if category in SEGMENT_CATEGORIES else 'sponsor'] += 1
    return breakdown


@api.route('/settings/community-sync', methods=['GET'])
@log_request
def get_community_sync_settings():
    """Return the community-pattern sync settings."""
    from community_sync import DEFAULT_CRON
    db = get_database()
    return json_response({
        'enabled': db.get_setting_bool('community_sync_enabled', default=False),
        'cron': db.get_setting('community_sync_cron') or DEFAULT_CRON,
        'lastRun': db.get_setting('community_sync_last_run') or None,
        'lastError': db.get_setting('community_sync_last_error') or None,
        'manifestVersion': db.get_setting('community_sync_manifest_version') or None,
        'lastSummary': db.get_setting('community_sync_last_summary') or None,
        'categories': resolve_community_sync_categories(
            db.get_setting('community_sync_categories')),
        'categoryBreakdown': _community_category_breakdown(db),
    })


@api.route('/settings/community-sync', methods=['PUT'])
@log_request
def update_community_sync_settings():
    """Update community-pattern sync settings.

    Body: {enabled?: bool, cron?: str, categories?: string[]}. Cron
    expression is validated; categories must be a subset of the known
    segment categories.
    """
    from utils.cron import is_valid_expression
    db = get_database()
    data = request.get_json() or {}
    if 'enabled' in data:
        db.set_setting('community_sync_enabled', 'true' if bool(data['enabled']) else 'false')
    if 'cron' in data:
        cron = (data['cron'] or '').strip()
        if not is_valid_expression(cron):
            return error_response(f'invalid cron expression: {cron}', 400)
        db.set_setting('community_sync_cron', cron)
    if 'categories' in data:
        categories, err = common.validate_community_sync_categories(data['categories'])
        if err is not None:
            return error_response(err, 400)
        db.set_setting('community_sync_categories', json.dumps(categories), is_default=False)
        logger.info(f"Updated community sync categories: {categories}")
    return get_community_sync_settings()


# ========== Community-pattern sync triggers ==========

# ========== Scheduled DB backup settings ==========

@api.route('/settings/db-backup', methods=['GET'])
@log_request
def get_db_backup_settings():
    """Return scheduled DB backup settings plus derived dest state.

    effectiveDest resolves the configured dest ('' -> <data_dir>/backups);
    destWritable is a live probe of that resolved directory so the UI can
    warn before a scheduled run fails.
    """
    db = get_database()
    settings = db.get_all_settings()

    def _val(key):
        entry = settings.get(key)
        return entry.get('value') if entry else None

    enabled_raw = _val('db_backup_enabled')
    enabled = (
        False if enabled_raw is None
        else str(enabled_raw).strip().lower() in ('true', '1', 'yes', 'on')
    )
    dest = _val('db_backup_dest') or ''
    try:
        effective = validate_backup_dest(dest, db.data_dir)
        effective_dest = str(effective)
        # dest_writable matches mkdir(parents=True): probe the nearest existing
        # ancestor, not just the immediate parent, so a multi-level dest under a
        # writable ancestor is not falsely reported unwritable.
        writable = dest_writable(effective)
    except ValueError:
        effective_dest = ''
        writable = False
    return json_response({
        'enabled': enabled,
        'cron': _val('db_backup_cron') or DEFAULT_CRON,
        'dest': dest,
        'effectiveDest': effective_dest,
        'destWritable': writable,
        'keepCount': int(_val('db_backup_keep_count') or '1'),
        'lastRun': _val('db_backup_last_run') or None,
        'lastError': _val('db_backup_last_error') or None,
        'lastSummary': _val('db_backup_last_summary') or None,
    })


@api.route('/settings/db-backup', methods=['PUT'])
@log_request
def update_db_backup_settings():
    """Update scheduled DB backup settings.

    Body: {enabled?, cron?, dest?, keepCount?}. dest '' resets to default;
    validation errors from validate_backup_dest are surfaced verbatim.
    """
    db = get_database()
    data = request.get_json() or {}

    # Stage every present field first, then commit the set together: neither
    # a late 400 nor a failed write may persist part of the payload.
    staged = {}
    if 'enabled' in data:
        staged['db_backup_enabled'] = 'true' if bool(data['enabled']) else 'false'
    if 'cron' in data:
        cron = (data['cron'] or '').strip()
        if not is_valid_expression(cron):
            return error_response(f'invalid cron expression: {cron}', 400)
        staged['db_backup_cron'] = cron
    if 'keepCount' in data:
        keep = data['keepCount']
        if (not isinstance(keep, int) or isinstance(keep, bool)
                or keep < KEEP_COUNT_MIN or keep > KEEP_COUNT_MAX):
            return error_response(
                f'keepCount must be an integer between {KEEP_COUNT_MIN} and {KEEP_COUNT_MAX}',
                400,
            )
        staged['db_backup_keep_count'] = str(keep)
    if 'dest' in data:
        dest = data['dest']
        try:
            validate_backup_dest(dest, db.data_dir)
        except ValueError as e:
            return error_response(str(e), 400)
        staged['db_backup_dest'] = dest

    try:
        with db.settings_transaction():
            for key, value in staged.items():
                db.set_setting(key, value)
    except sqlite3.Error:
        logger.exception("DB backup settings save failed; no field was persisted")
        return error_response('Settings could not be saved', 500)

    # Read back after the commit so the response reflects durable state.
    return get_db_backup_settings()


# ========== Pattern cleanup (Experiments) settings ==========

@api.route('/settings/pattern-cleanup', methods=['PUT'])
@log_request
def update_pattern_cleanup_settings():
    """Update pattern cleanup settings; a blank provider inherits detection's slot."""
    db = get_database()
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not data:
        return error_response('request body must be a non-empty JSON object', 400)

    staged = {}
    if 'enabled' in data:
        enabling = data['enabled']
        if not isinstance(enabling, bool):
            return error_response('enabled must be a boolean', 400)
        staged['pattern_cleanup_enabled'] = 'true' if enabling else 'false'
        was_enabled = db.get_setting_bool('pattern_cleanup_enabled', default=False)
        if enabling and not was_enabled:
            # Turning the schedule on waits for the next cron slot, even after a stale run.
            staged['pattern_cleanup_schedule_anchor'] = utc_now_iso()
    if 'cron' in data:
        if not isinstance(data['cron'], str):
            return error_response('cron must be a string', 400)
        cron = data['cron'].strip()
        if not is_valid_expression(cron):
            return error_response(f'invalid cron expression: {cron}', 400)
        staged['pattern_cleanup_cron'] = cron
    if 'batchSize' in data:
        value = data['batchSize']
        lo, hi = BATCH_SIZE_RANGE
        if not isinstance(value, int) or isinstance(value, bool) or value < lo or value > hi:
            return error_response(f'batchSize must be an integer between {lo} and {hi}', 400)
        staged['pattern_cleanup_batch_size'] = str(value)
    if 'unusedDays' in data:
        value = data['unusedDays']
        lo, hi = UNUSED_DAYS_RANGE
        if not isinstance(value, int) or isinstance(value, bool) or value < lo or value > hi:
            return error_response(f'unusedDays must be an integer between {lo} and {hi}', 400)
        staged['pattern_cleanup_unused_days'] = str(value)
    if 'provider' in data:
        value = data['provider']
        if value is not None and not isinstance(value, str):
            return error_response('provider must be a string or null', 400)
        value = common.SLOT_ALIASES.get(value, value) if value else value
        valid = VALID_SLOTS + (SAME_AS_DETECTION,)
        if not value:
            staged['pattern_cleanup_provider'] = ''
        elif value in valid:
            staged['pattern_cleanup_provider'] = value
        else:
            return error_response(f'provider must be one of: {", ".join(valid)}, or blank', 400)
    if 'model' in data:
        value = data['model']
        if value is not None and not isinstance(value, str):
            return error_response('model must be a string or null', 400)
        value = (value or '').strip()
        if len(value) > 200:
            return error_response('model must be 200 characters or fewer', 400)
        staged['pattern_cleanup_model'] = value

    try:
        with db.settings_transaction():
            for key, value in staged.items():
                if value:
                    db.set_setting(key, value, is_default=False)
                else:
                    db.clear_setting(key)
    except sqlite3.Error:
        logger.exception("Pattern cleanup settings save failed; no field was persisted")
        return error_response('Settings could not be saved', 500)

    return json_response(cleanup_settings_view(db))


@api.route('/settings/replacement-audio', methods=['GET'])
@log_request
def get_replacement_audio():
    """Metadata for the audio spliced in where an ad was cut."""
    return json_response(replacement_audio.describe())


@api.route('/settings/replacement-audio/file', methods=['GET'])
@log_request
def get_replacement_audio_file():
    """Serve the current replacement audio so the UI can play it."""
    path, mimetype = replacement_audio.current_file()
    if not path:
        return error_response('no replacement audio is installed', 404)
    response = send_file(path, mimetype=mimetype, as_attachment=False,
                         download_name='replace.mp3')
    # The path is stable across uploads, so without this a swap keeps playing
    # the file the browser already cached.
    response.headers['Cache-Control'] = 'no-store'
    return response


@api.route('/settings/replacement-audio', methods=['POST'])
@limiter.limit("10/minute")
@log_request
def upload_replacement_audio():
    """Install an operator-supplied replacement, transcoded to MP3."""
    upload = request.files.get('file')
    if upload is None:
        return error_response('an audio file is required (multipart field "file")', 400)
    # Read whole so the rejection message can name the real size; Flask's
    # MAX_CONTENT_LENGTH already bounds the request at 10 MB.
    raw = upload.stream.read()
    try:
        info = replacement_audio.save_upload(raw)
    except replacement_audio.ToolMissingError as e:
        return error_response(str(e), 503)
    except replacement_audio.ReplacementAudioError as e:
        return error_response(str(e), 400)
    return json_response(info)


@api.route('/settings/replacement-audio', methods=['DELETE'])
@log_request
def delete_replacement_audio():
    """Drop the uploaded replacement and fall back to the shipped default."""
    reverted = replacement_audio.revert()
    info = replacement_audio.describe()
    info['reverted'] = reverted
    return json_response(info)
