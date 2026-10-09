"""System One tunable profiles and validation."""

import json
import math
from typing import Any

from config import (
    PROVIDER_SYSTEMONE_COMPATIBLE,
    PROVIDER_TYPESAFE,
    SEGMENT_CATEGORIES,
    SYSTEMONE_TUNABLE_DEFAULTS,
    SYSTEMONE_TUNABLE_PROFILE_KEYS,
)


NULLABLE_FIELDS = frozenset({
    "reviewEvidenceEnter", "reviewChoiceEnter", "maxQuestionsPerRequest",
    "maxRequestBytes", "maxChoiceOptions",
})
FLOAT_RANGES = {
    "detectionEnter": (0.0, 1.0),
    "detectionStay": (0.0, 1.0),
    "reviewEvidenceEnter": (0.0, 1.0),
    "reviewChoiceEnter": (0.0, 1.0),
    "reviewProgrammeVeto": (0.0, 1.0),
}
POSITIVE_FLOAT_FIELDS = frozenset({
    "reviewBoundaryCapSeconds", "reviewContextSeconds", "requestDeadlineSeconds",
})
BOOLEAN_FIELDS = frozenset({"categoryPass", "refineBoundaries"})


class SystemOneSettingsError(ValueError):
    """Stored System One tuning is malformed or outside its supported range."""


def profile_key(slot: str, provider: str) -> str:
    try:
        return SYSTEMONE_TUNABLE_PROFILE_KEYS[(slot, provider)]
    except KeyError:
        raise SystemOneSettingsError("unsupported System One slot or provider") from None


def merge_profile(defaults: dict[str, Any], current: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Merge a partial profile while preserving fields unknown to older exports."""
    if not isinstance(patch, dict):
        raise SystemOneSettingsError("profile must be an object or null")
    unknown = set(patch) - set(defaults)
    if unknown:
        raise SystemOneSettingsError(f"unsupported System One tuning fields: {', '.join(sorted(unknown))}")
    merged = {**defaults, **current, **patch}
    validate_profile(merged, defaults)
    return merged


def validate_profile(values: dict[str, Any], defaults: dict[str, Any] | None = None) -> None:
    """Validate field types and cross-field confidence constraints."""
    defaults = defaults or SYSTEMONE_TUNABLE_DEFAULTS[PROVIDER_TYPESAFE]
    if set(values) - set(defaults):
        raise SystemOneSettingsError("profile contains unsupported fields")
    for field, (low, high) in FLOAT_RANGES.items():
        value = values.get(field)
        if value is None and field in NULLABLE_FIELDS:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise SystemOneSettingsError(f"{field} must be a finite number")
        if not low <= value <= high:
            raise SystemOneSettingsError(f"{field} must be between {low} and {high}")
    for field in POSITIVE_FLOAT_FIELDS:
        value = values.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise SystemOneSettingsError(f"{field} must be a finite number greater than zero")
    cap = values.get("retryAfterMaxSeconds")
    if isinstance(cap, bool) or not isinstance(cap, (int, float)) or not math.isfinite(cap) or cap < 0:
        raise SystemOneSettingsError("retryAfterMaxSeconds must be a finite non-negative number")
    for field in BOOLEAN_FIELDS:
        if not isinstance(values.get(field), bool):
            raise SystemOneSettingsError(f"{field} must be a boolean")
    if values.get("detectionStay", 0.0) > values.get("detectionEnter", 1.0):
        raise SystemOneSettingsError("detectionStay must not exceed detectionEnter")
    context = values.get("categoryContext")
    if isinstance(context, bool) or not isinstance(context, int) or context < 0:
        raise SystemOneSettingsError("categoryContext must be a non-negative integer")
    if values.get("defaultCategory") not in SEGMENT_CATEGORIES:
        raise SystemOneSettingsError("defaultCategory is not a supported category")
    for field in ("maxQuestionsPerRequest", "maxRequestBytes", "maxChoiceOptions", "maxConcurrentOperations"):
        value = values.get(field)
        if value is None and field in NULLABLE_FIELDS:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            suffix = " or null" if field in NULLABLE_FIELDS else ""
            raise SystemOneSettingsError(f"{field} must be a positive integer{suffix}")
    if defaults.get("maxChoiceOptions") == 255 and values.get("maxChoiceOptions") != 255:
        raise SystemOneSettingsError("TypeSafe supports a fixed maximum of 255 Choice options")


def profile_from_snapshot(snapshot: dict[str, Any], slot: str, provider: str) -> dict[str, Any]:
    key = profile_key(slot, provider)
    entry = snapshot.get(key)
    raw = entry.get("value") if isinstance(entry, dict) else None
    if raw is None:
        raw = {}
    try:
        stored = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError) as exc:
        raise SystemOneSettingsError(f"{key} is not valid JSON") from exc
    if not isinstance(stored, dict):
        raise SystemOneSettingsError(f"{key} must contain an object")
    defaults = SYSTEMONE_TUNABLE_DEFAULTS[provider]
    values = {**defaults, **(stored or {})}
    validate_profile(values, defaults)
    return values


def profile_payload(snapshot: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return effective profiles, default flags, and shipped profile defaults."""
    profiles: dict[str, Any] = {}
    defaults: dict[str, Any] = {}
    shipped: dict[str, Any] = {}
    for slot in ("primary", "secondary"):
        profiles[slot], defaults[slot], shipped[slot] = {}, {}, {}
        for provider in (PROVIDER_TYPESAFE, PROVIDER_SYSTEMONE_COMPATIBLE):
            key = profile_key(slot, provider)
            profiles[slot][provider] = profile_from_snapshot(snapshot, slot, provider)
            entry = snapshot.get(key)
            defaults[slot][provider] = not bool(
                isinstance(entry, dict) and not entry.get("is_default", True)
            )
            shipped[slot][provider] = dict(SYSTEMONE_TUNABLE_DEFAULTS[provider])
    return profiles, defaults, shipped
