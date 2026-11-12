"""Stored error text: which values hold it and the one redaction every writer applies."""
import json

from utils.http import redact_log_text, redact_stored_text

FAILOVER_STATE_PREFIX = 'failover_state:'
FAILOVER_PROBE_PREFIX = 'failover_probe:'
NODE_HEALTH_KEY = 'podping_node_health'
_ERROR_SETTING_SUFFIX = '_last_error'
_SETTING_TABLES = ('settings', 'system_settings')

# Columns that hold stored exception or error text, served back by the API.
STORED_ERROR_COLUMNS = (
    ('episodes', 'error_message'),
    ('episodes', 'chapters_regen_error'),
    ('episodes', 'sb_last_error'),
    ('processing_history', 'error_message'),
    ('auto_process_queue', 'error_message'),
    ('podcasts', 'last_refresh_error'),
    ('pattern_cleanup_runs', 'error'),
    ('cue_candidate_scans', 'error'),
    ('cue_threshold_scans', 'error'),
    ('cue_cross_episode_scans', 'error'),
    ('cue_window_optimize_scans', 'error'),
    ('failover_events', 'reason'),
)

# JSON columns that embed error text under one top-level field.
STORED_ERROR_JSON_COLUMNS = (
    ('episode_details', 'dai_differential_json'),
    ('episode_details', 'upstream_transcript_json'),
)


def redact_error(value, limit: int | None = None):
    """Redact error text for storage or egress; None stays None, limit cuts after redaction."""
    if value is None:
        return None
    text = redact_log_text(value)
    return text if limit is None else text[:limit]


def redact_json_fields(value, fields, per_entry=False):
    """Redact the named top-level string fields of a JSON object, or of each entry's object."""
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        return value
    if not isinstance(data, dict):
        return value
    targets = [entry for entry in data.values() if isinstance(entry, dict)] if per_entry else [data]
    changed = False
    for target in targets:
        for field in fields:
            text = target.get(field)
            if isinstance(text, str):
                redacted = redact_stored_text(text)
                if redacted != text:
                    target[field] = redacted
                    changed = True
    return json.dumps(data) if changed else value


def redact_setting_value(key: str, value):
    """Redact a settings or system_settings value when its key is one that holds error text."""
    if not isinstance(value, str):
        return value
    if key.endswith(_ERROR_SETTING_SUFFIX):
        return redact_stored_text(value)
    if key.startswith(FAILOVER_STATE_PREFIX):
        return redact_json_fields(value, ('reason',))
    if key.startswith(FAILOVER_PROBE_PREFIX):
        return redact_json_fields(value, ('detail',))
    if key == NODE_HEALTH_KEY:
        return redact_json_fields(value, ('last_failure_reason',), per_entry=True)
    return value


def redact_json_error(value):
    """Redact the top-level error field of a stored differential JSON string."""
    return redact_json_fields(value, ('error',)) if isinstance(value, str) else value


def redaction_units():
    """(table, column, rewrite, where, params) for every stored error value the redaction step covers."""
    units = [(table, column, redact_stored_text, '1', ()) for table, column in STORED_ERROR_COLUMNS]
    for table in _SETTING_TABLES:
        units.append((table, 'value', redact_stored_text, "key LIKE ? ESCAPE '\\'", ('%\\_last\\_error',)))
    units.append(('settings', 'value', lambda v: redact_json_fields(v, ('reason',)),
                  "key LIKE ? ESCAPE '\\'", ('failover\\_state:%',)))
    units.append(('settings', 'value', lambda v: redact_json_fields(v, ('detail',)),
                  "key LIKE ? ESCAPE '\\'", ('failover\\_probe:%',)))
    units.append(('settings', 'value',
                  lambda v: redact_json_fields(v, ('last_failure_reason',), per_entry=True),
                  'key = ?', (NODE_HEALTH_KEY,)))
    for table, column in STORED_ERROR_JSON_COLUMNS:
        units.append((table, column, redact_json_error, '1', ()))
    return units
