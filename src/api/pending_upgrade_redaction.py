"""Redact stored error text in API responses until the stored-error redaction step has run."""
import json

from api import api, get_database
from database.schema.deferred import REDACT_GATE
from utils.http import redact_stored_text

_state = {'done': False}


def _is_error_key(key) -> bool:
    lowered = str(key).lower()
    return 'error' in lowered or 'reason' in lowered


def _redact_error_fields(value):
    if isinstance(value, dict):
        return {key: (redact_stored_text(item) if isinstance(item, str) and _is_error_key(key)
                      else _redact_error_fields(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_error_fields(item) for item in value]
    return value


def latched() -> bool:
    return _state['done']


def reset_latch() -> None:
    _state['done'] = False


@api.after_request
def redact_old_errors(response):
    """Older builds stored error text unredacted; the background step rewrites it, this covers the gap."""
    if _state['done'] or response.mimetype != 'application/json' or response.direct_passthrough:
        return response
    if REDACT_GATE not in get_database().pending_upgrade_steps():
        _state['done'] = True
        return response
    body = response.get_data()
    if b'rror' not in body and b'eason' not in body:
        return response
    try:
        parsed = json.loads(body)
    except ValueError:
        return response
    redacted = _redact_error_fields(parsed)
    if redacted != parsed:
        response.set_data(json.dumps(redacted))
    return response
