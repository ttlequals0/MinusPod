"""Shared settings helpers: setting views, clamping and phase rejection."""

import logging
from contextlib import contextmanager
import threading
from dataclasses import dataclass
from typing import Any
from collections.abc import Mapping
from config import (
    PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER, PROVIDER_OPENAI_COMPATIBLE, PROVIDER_OLLAMA,
    SYSTEMONE_PROVIDERS,
    SEGMENT_CATEGORIES,
)

logger = logging.getLogger('podcast.api')

# Every LLM provider the settings API accepts.
VALID_LLM_PROVIDERS = (
    PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER,
    PROVIDER_OPENAI_COMPATIBLE, PROVIDER_OLLAMA, *SYSTEMONE_PROVIDERS,
)

# Provider B payload keys accepted as aliases of the stored secondary_* keys
# (#806 rename: Primary/Secondary becomes Provider A/Provider B).
PROVIDER_B_ALIASES = {
    'providerBEnabled': 'secondaryProviderEnabled',
    'providerB': 'secondaryProvider',
    'providerBBaseUrl': 'secondaryProviderBaseUrl',
    'providerBApiKey': 'secondaryProviderApiKey',
    'providerBRequestsPerMin': 'secondaryProviderRequestsPerMin',
    'providerBRequestsPerDay': 'secondaryProviderRequestsPerDay',
    'providerBTokensPerMin': 'secondaryProviderTokensPerMin',
}
SLOT_ALIASES = {'a': 'primary', 'b': 'secondary'}


@dataclass(frozen=True)
class SettingEntry:
    """Typed view of one row from db.get_all_settings().

    The producer (src/database/settings.py:get_all_settings) returns
    ``{key: {'value': X, 'is_default': Y}}`` dicts to stay compatible with
    consumers outside this module (secrets_crypto, main_app, tests). The
    helpers below wrap that dict shape into ``SettingEntry`` so consumers
    in this file get attribute access and a stable type.
    """
    value: Any
    is_default: bool


def _settings_view(raw: Mapping[str, Any]) -> dict[str, SettingEntry]:
    """Wrap the raw get_all_settings() dict into a SettingEntry mapping.

    Skips entries that lack the expected shape so a malformed row can't
    crash the GET /settings endpoint.
    """
    view: dict[str, SettingEntry] = {}
    for key, info in raw.items():
        if not isinstance(info, Mapping):
            continue
        view[key] = SettingEntry(
            value=info.get('value'),
            is_default=bool(info.get('is_default', True)),
        )
    return view


def _setting_value(settings, key, default=None):
    """Extract value from the ``SettingEntry`` view built by _settings_view()."""
    entry = settings.get(key)
    if entry is None:
        return default
    return entry.value if entry.value is not None else default


def _setting_is_default(settings, key):
    """Check if a setting is still at its default value."""
    entry = settings.get(key)
    if entry is None:
        return True
    return entry.is_default


def _clamped_int(raw, default, lo, hi):
    """Parse ``raw`` as an int (falling back to ``default``) and clamp to [lo, hi]."""
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


class _PhaseRejected(Exception):
    """A phase returned an error response; unwinds the settings transaction."""

    def __init__(self, response):
        super().__init__('settings phase rejected')
        self.response = response


def validate_community_sync_categories(value) -> tuple:
    """Validate a communitySyncCategories/categories payload list.

    Returns (categories, error_message); categories is None when
    error_message is set. Shared by the ad-detection PUT phase below and
    the dedicated /settings/community-sync PUT so both reject the same
    malformed input the same way.
    """
    if not isinstance(value, list):
        return None, 'communitySyncCategories must be a list'
    for cat in value:
        if cat not in SEGMENT_CATEGORIES:
            return None, f"communitySyncCategories: unknown category '{cat}'"
    # Dedupe while keeping SEGMENT_CATEGORIES order, so the stored JSON list
    # is deterministic regardless of client submission order.
    return [c for c in SEGMENT_CATEGORIES if c in value], None


# Post-commit ordering contract: caches are invalidated before holds lift, so
# a resumed queue reads the configuration the save just committed.
_POST_COMMIT_BUCKETS = ('side_effects', 'holds')
_deferred = threading.local()


def after_commit(fn, bucket: str = 'side_effects'):
    """Hold a phase side effect until the settings transaction commits.

    Runs inline when no transaction is open, so a phase helper called
    directly keeps its standalone behavior.
    """
    buckets = getattr(_deferred, 'buckets', None)
    if buckets is None:
        fn()
        return
    buckets[bucket].append(fn)


@contextmanager
def post_commit_scope():
    """Collect deferred side effects for one settings save; always clears the scope."""
    buckets = {name: [] for name in _POST_COMMIT_BUCKETS}
    _deferred.buckets = buckets
    try:
        yield buckets
    finally:
        _deferred.buckets = None


def run_post_commit(buckets):
    """Writes are already durable here, so a failing side effect must not 500."""
    for name in _POST_COMMIT_BUCKETS:
        for fn in buckets[name]:
            try:
                fn()
            except Exception:
                logger.exception("Post-commit settings side effect failed")
