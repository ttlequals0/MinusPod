"""Cycle-free provider request budgets shared by dispatch and probe paths."""

import json
from datetime import timedelta

from config import get_env_backed_int
from utils.time import ISO_FORMAT, utc_now


def request_token_estimate(payload):
    """No output addend: System One/TypeSafe bills input only, unlike _reserved_tokens' providers."""
    return max(1, len(json.dumps(payload, separators=(',', ':'))) // 2)


def manual_rate_limit_caps(credential_slot='primary'):
    if credential_slot == 'secondary':
        keys = ('secondary_provider_requests_per_min',
                'secondary_provider_requests_per_day',
                'secondary_provider_tokens_per_min')
    elif credential_slot == 'primary':
        keys = ('provider_requests_per_min', 'provider_requests_per_day',
                'provider_tokens_per_min')
    else:
        keys = None
    now = utc_now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if keys is None:
        rpm = rpd = tpm = 0
    else:
        rpm, rpd, tpm = (get_env_backed_int(key, floor=0) for key in keys)
    return {
        'rpm': rpm, 'rpd': rpd, 'tpm': tpm,
        'minute_since': (now - timedelta(seconds=60)).strftime(ISO_FORMAT),
        'day_since': midnight.strftime(ISO_FORMAT),
    }
