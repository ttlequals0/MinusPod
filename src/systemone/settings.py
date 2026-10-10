"""Resolve a coherent immutable System One tuning snapshot."""

import database
from config import PROVIDER_SYSTEMONE_COMPATIBLE, PROVIDER_TYPESAFE
from llm_timeout import get_llm_timeout_from_snapshot
from systemone.adapter import SystemOneSettings
from systemone.tuning import (
    SystemOneSettingsError,
    profile_from_snapshot,
)


def get_systemone_settings(provider: str, credential_slot: str, model: str) -> SystemOneSettings:
    """Build request settings from one database snapshot and provider profile."""
    provider = (provider or "").strip().lower()
    if provider not in (PROVIDER_TYPESAFE, PROVIDER_SYSTEMONE_COMPATIBLE):
        raise SystemOneSettingsError("provider is not a System One provider")
    if credential_slot not in ("primary", "secondary"):
        raise SystemOneSettingsError("credential slot must be primary or secondary")
    if not isinstance(model, str) or not model.strip():
        raise SystemOneSettingsError("model must be a non-empty string")
    snapshot = database.Database().get_all_settings()
    values = profile_from_snapshot(snapshot, credential_slot, provider)
    request_timeout = get_llm_timeout_from_snapshot(provider, credential_slot, snapshot)
    return SystemOneSettings(
        model=model,
        request_timeout=request_timeout,
        request_deadline_seconds=values["requestDeadlineSeconds"],
        max_concurrent_operations=values["maxConcurrentOperations"],
        retry_after_max_seconds=values["retryAfterMaxSeconds"],
        detection_enter=values["detectionEnter"],
        detection_stay=values["detectionStay"],
        category_pass=values["categoryPass"],
        category_context=values["categoryContext"],
        default_category=values["defaultCategory"],
        refine_boundaries=values["refineBoundaries"],
        review_evidence_enter=values["reviewEvidenceEnter"],
        review_choice_enter=values["reviewChoiceEnter"],
        review_programme_veto=values["reviewProgrammeVeto"],
        review_boundary_cap_seconds=values["reviewBoundaryCapSeconds"],
        review_context_seconds=values["reviewContextSeconds"],
        max_questions_per_request=values["maxQuestionsPerRequest"],
        max_request_bytes=values["maxRequestBytes"],
        max_choice_options=values["maxChoiceOptions"],
    )
