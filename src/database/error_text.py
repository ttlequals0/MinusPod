"""Stored error text: which values hold it and the one redaction every writer applies."""
import json

from utils.http import redact_log_text, redact_stored_text

FAILOVER_STATE_PREFIX = 'failover_state:'
FAILOVER_PROBE_PREFIX = 'failover_probe:'
NODE_HEALTH_KEY = 'podping_node_health'
_ERROR_SETTING_SUFFIX = '_last_error'
_SETTING_TABLES = ('settings', 'system_settings')
STATS_ERROR_FIELDS = ('error', 'chapters_degraded_reason')

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
    ('systemone_call_diagnostics', 'reason'),
    ('episodes', 'detection_degraded'),
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


def _redact_fields_deep(node, fields) -> bool:
    """Redact the named string fields at any depth in place; True when something changed."""
    changed = False
    if isinstance(node, dict):
        for key, item in node.items():
            if key in fields and isinstance(item, str):
                redacted = redact_stored_text(item)
                if redacted != item:
                    node[key] = redacted
                    changed = True
            elif isinstance(item, (dict, list)):
                changed |= _redact_fields_deep(item, fields)
    elif isinstance(node, list):
        for item in node:
            changed |= _redact_fields_deep(item, fields)
    return changed


def redact_json_fields(value, fields):
    """Redact the named string fields of a JSON object at any depth; other or invalid JSON is untouched."""
    if not isinstance(value, str) or not any(field in value for field in fields):
        return value
    try:
        data = json.loads(value)
    except ValueError:
        return value
    if not isinstance(data, dict):
        return value
    return json.dumps(data) if _redact_fields_deep(data, fields) else value


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
        return redact_json_fields(value, ('last_failure_reason',))
    return value


def redact_json_error(value):
    """Redact every error field of a stored differential or upstream transcript JSON string."""
    return redact_json_fields(value, ('error',)) if isinstance(value, str) else value


def redaction_units():
    """(key, table, column, rewrite, where, params) for every stored error value; the key is stable and persisted."""
    units = [(f'col:{table}.{column}', table, column, redact_stored_text, '1', ())
             for table, column in STORED_ERROR_COLUMNS]
    for table in _SETTING_TABLES:
        units.append((f'setting:{table}.last_error', table, 'value', redact_stored_text,
                      "key LIKE ? ESCAPE '\\'", ('%\\_last\\_error',)))
    units.append(('setting:failover_state', 'settings', 'value', lambda v: redact_json_fields(v, ('reason',)),
                  "key LIKE ? ESCAPE '\\'", ('failover\\_state:%',)))
    units.append(('setting:failover_probe', 'settings', 'value', lambda v: redact_json_fields(v, ('detail',)),
                  "key LIKE ? ESCAPE '\\'", ('failover\\_probe:%',)))
    units.append(('setting:node_health', 'settings', 'value',
                  lambda v: redact_json_fields(v, ('last_failure_reason',)), 'key = ?', (NODE_HEALTH_KEY,)))
    units += [(f'json:{table}.{column}', table, column, redact_json_error, '1', ())
              for table, column in STORED_ERROR_JSON_COLUMNS]
    units.append(('json:processing_history.processing_stats_json', 'processing_history', 'processing_stats_json',
                  lambda v: redact_json_fields(v, STATS_ERROR_FIELDS), '1', ()))
    return units
