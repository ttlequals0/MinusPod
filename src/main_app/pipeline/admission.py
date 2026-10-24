"""Route snapshots and provider admission gates."""

import json
import logging
from processing_queue import is_processing_paused
from config import (
    PROCESSING_MODE_PASSTHROUGH,
    PROCESSING_MODE_SKIP_DETECTION,
    PROCESSING_MODE_CUE_ONLY,
    CHAPTERS_MODE_OFF,
    resolve_processing_mode,
    resolve_skip_second_pass,
    resolve_chapters_mode,
)

from database import Database
from llm_client import ProviderAccountChangedError

from llm_route import apply_failover_dict, resolve_route, route_account_mismatch
from rate_limit_hold import (
    HOLD_REASON_ACCOUNT_CHANGED, HOLD_REASON_PAUSED, active_hold_reason,
)

import run_context

from main_app.pipeline import review
from main_app.pipeline.shared import db

audio_logger = logging.getLogger('podcast.audio')


def _load_route_snapshot(run_id: str) -> dict | None:
    """The persisted snapshot for an existing run row, or None when there
    isn't one yet (fresh run) or the row/column can't be read."""
    try:
        row = db.get_connection().execute(
            "SELECT route_snapshot_json FROM processing_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    except Exception as exc:
        audio_logger.warning(f"Could not read route snapshot for run {run_id}: {exc}")
        return None
    if not row or not row['route_snapshot_json']:
        return None
    try:
        return json.loads(row['route_snapshot_json'])
    except (TypeError, ValueError) as exc:
        audio_logger.warning(f"Could not parse route snapshot for run {run_id}: {exc}")
        return None


def _persist_route_snapshot(run_id: str, snapshot: dict) -> None:
    try:
        conn = db.get_connection()
        conn.execute(
            "UPDATE processing_runs SET route_snapshot_json = ? WHERE run_id = ?",
            (json.dumps(snapshot), run_id),
        )
        conn.commit()
    except Exception as exc:
        audio_logger.warning(f"Could not persist route snapshot for run {run_id}: {exc}")


def _admission_gates(settings_db) -> dict:
    """Admission's feed-independent feature gates, so a multi-row scan reads
    them once instead of per row."""
    return {'review': review._ad_review_enabled(settings_db),
            'chapters_enabled': settings_db.get_setting('chapters_enabled')}


def _chapters_enabled_for_admission(chapters_enabled, podcast_row, settings_db) -> bool:
    """Whether this run's chapter step could call the chapters LLM at all.

    Mirrors the two settings that turn chapters off outright: the global
    chapters_enabled flag and the per-feed chapters_mode. Fails open (True)
    on an unresolved/'auto' mode, since counting an unused provider as
    required is always the safe direction; only a definite 'off' excludes it.
    """
    if chapters_enabled is not None and chapters_enabled.lower() != 'true':
        return False
    return resolve_chapters_mode(podcast_row, db=settings_db) != CHAPTERS_MODE_OFF


def _admission_mode_rows(settings_db, slug: str, episode_id: str | None,
                         queue_row: dict | None) -> tuple[dict | None, dict | None]:
    """Podcast and episode rows the mode resolvers read, taken from a pending
    queue row when the scan already selected those columns."""
    if queue_row is not None:
        return ({'passthrough_enabled': queue_row.get('feed_passthrough_enabled'),
                 'skip_ad_detection': queue_row.get('skip_ad_detection'),
                 'skip_second_pass': queue_row.get('skip_second_pass'),
                 'detection_mode': queue_row.get('detection_mode'),
                 'chapters_mode': queue_row.get('chapters_mode')},
                {'passthrough_enabled': queue_row.get('episode_passthrough_enabled')})
    episode_row = (settings_db.get_episode_state(slug, episode_id)
                   if episode_id else None)
    return settings_db.get_podcast_row(slug), episode_row


def _required_providers_for_admission(slug: str, episode_id: str | None = None,
                                      snapshot: dict | None = None,
                                      gates: dict | None = None,
                                      queue_row: dict | None = None
                                      ) -> list[tuple[str, str]] | None:
    """Distinct (provider_key, credential_slot) pairs this run's *enabled*
    phases would resolve to right now, or None when resolution fails
    (admission then falls back to the legacy unscoped hold check, a safe
    superset of any real per-provider hold).

    A phase whose feature is off for this run is excluded: a held account
    that this run will never actually call must not refuse it. The resolved
    processing mode counts here too, so a pass-through run needs no account
    at all and a skip-detection or cue-only run needs only the chapters
    account. A phase snapshot with no credential_slot (older callers, tests)
    defaults to 'primary', matching single-provider installs today.

    Inside a run, uses the run's frozen route snapshot so a settings change
    during transcription cannot make the reservation cover different
    providers than the run will actually call; at dispatch admission (no
    installed snapshot yet) it resolves fresh from live settings. Callers
    scanning many feeds in one pass pass `snapshot` and `gates` in, since
    neither varies by feed, and `queue_row` so the mode needs no lookup.
    """
    active_phases = _active_phases_for_admission(
        slug, episode_id, snapshot=snapshot, gates=gates, queue_row=queue_row)
    if active_phases is None:
        return None
    return list({(route['provider_key'], route.get('credential_slot', 'primary'))
                 for route in active_phases.values()})


def _active_phases_for_admission(slug: str, episode_id: str | None = None,
                                 snapshot: dict | None = None,
                                 gates: dict | None = None,
                                 queue_row: dict | None = None
                                 ) -> dict | None:
    """{phase: route} for the phases this run's configuration actually
    enables, or None when the routes cannot be resolved.

    The phase-keyed form of _required_providers_for_admission, so a caller
    that must name the blocked phase (the queue admission explanation) reads
    the same resolved set admission itself gates on.
    """
    ctx = run_context.current()
    if snapshot is None:
        snapshot = (ctx.route_snapshot if ctx else None) or _resolve_route_snapshot()
    if snapshot is None:
        return None
    active_phases = dict(snapshot)
    try:
        settings_db = Database()
        if gates is None:
            gates = _admission_gates(settings_db)
        podcast_row, episode_row = _admission_mode_rows(
            settings_db, slug, episode_id, queue_row)
        mode = resolve_processing_mode(podcast_row, episode_row)
        if mode == PROCESSING_MODE_PASSTHROUGH:
            return {}
        if mode in (PROCESSING_MODE_SKIP_DETECTION, PROCESSING_MODE_CUE_ONLY):
            # Both modes skip detection, review and verification outright, so
            # chapters is the only phase left that can reach an LLM.
            active_phases = {phase: route for phase, route in active_phases.items()
                             if phase == 'chapters'}
        if not gates['review']:
            active_phases.pop('review', None)
        if resolve_skip_second_pass(podcast_row, db=settings_db):
            active_phases.pop('verification', None)
        if not _chapters_enabled_for_admission(
                gates['chapters_enabled'], podcast_row, settings_db):
            active_phases.pop('chapters', None)
    except Exception as exc:
        # Fail open to the full (unfiltered) phase set: overcounting a
        # provider as required is the safe direction, a crash here is not.
        audio_logger.warning(f"Could not resolve phase enablement for admission: {exc}")
        active_phases = dict(snapshot)
    return {phase: apply_failover_dict({**route, 'phase': phase})
            for phase, route in active_phases.items()}


# Pipeline order, so an explanation names the phase a run reaches first.
_ADMISSION_PHASE_ORDER = ('detection', 'review', 'verification', 'chapters')


def admission_explanation(settings_db, slug: str, episode_id: str | None = None,
                          snapshot: dict | None = None, gates: dict | None = None,
                          queue_row: dict | None = None, paused: bool | None = None,
                          hold_cache: dict | None = None) -> dict:
    """Why a queued job is or is not admissible right now.

    {'blocked', 'phase', 'slot', 'reason', 'resumesAt'}: computed from the
    same resolved phase set admission gates on, so a phase this run will not
    run (a skipped verification, chapters turned off) never appears as the
    blocker. `paused` and `hold_cache` let a caller scoring a whole page of
    rows read each hold marker once instead of once per row.
    """
    idle = {'blocked': False, 'phase': None, 'slot': None,
            'reason': None, 'resumesAt': None}
    if paused if paused is not None else is_processing_paused(settings_db):
        return {'blocked': True, 'phase': None, 'slot': None,
                'reason': HOLD_REASON_PAUSED, 'resumesAt': None}

    def hold_for(provider_key, slot):
        if hold_cache is None:
            return active_hold_reason(settings_db, provider_key, slot)
        key = (provider_key, slot)
        if key not in hold_cache:
            hold_cache[key] = active_hold_reason(settings_db, provider_key, slot)
        return hold_cache[key]

    active_phases = _active_phases_for_admission(
        slug, episode_id, snapshot=snapshot, gates=gates, queue_row=queue_row)
    if active_phases is None:
        # Routes unresolved: admission falls back to the unscoped marker, so
        # report that same blanket hold rather than claiming a phase.
        reason, resumes_at = hold_for(None, 'primary')
        if reason is None:
            return idle
        return {'blocked': True, 'phase': None, 'slot': None,
                'reason': reason, 'resumesAt': resumes_at}
    for phase in _ADMISSION_PHASE_ORDER:
        route = active_phases.get(phase)
        if route is None:
            continue
        slot = route.get('credential_slot', 'primary')
        if route_account_mismatch(route) is not None:
            return {'blocked': True, 'phase': phase, 'slot': slot,
                    'reason': HOLD_REASON_ACCOUNT_CHANGED, 'resumesAt': None}
        reason, resumes_at = hold_for(route['provider_key'], slot)
        if reason is not None:
            return {'blocked': True, 'phase': phase, 'slot': slot,
                    'reason': reason, 'resumesAt': resumes_at}
    return idle


def _resolve_route_snapshot() -> dict | None:
    """Resolve this run's per-phase LLM routes once, non-secret only
    ({provider_key, configured_model, base_url, credential_slot}). None when
    resolution fails (e.g. no model configured yet): callers then fall back
    to today's per-call resolution against the live global settings.

    The review entry also freezes the raw review_provider/review_model
    setting VALUES as of run start under 'gate'. AdReviewer resolves its
    route from this frozen gate (honoring same_as_pass against whichever
    pass is calling) instead of re-reading settings per call, so an
    operator changing review_provider mid-run cannot change pass-2's
    provider after pass-1 already ran on a different one.
    """
    try:
        detection = resolve_route('detection')
        verification = resolve_route('verification')
        chapters = resolve_route('chapters')
        review = resolve_route(
            'review', pass_provider=detection.provider_key,
            pass_model=detection.model_id, pass_base_url=detection.base_url,
            pass_credential_slot=detection.credential_slot)
        # Database(), not the module-level db: resolve_route above already
        # reads settings through its own fresh Database() singleton lookup,
        # and the gate must match that same source of truth.
        settings_db = Database()
        review_gate = {
            'review_provider': settings_db.get_setting('review_provider'),
            'review_model': settings_db.get_setting('review_model'),
        }
    except Exception as exc:
        audio_logger.warning(f"Could not resolve per-phase LLM routes: {exc}")
        return None
    snapshot = {}
    for route in (detection, review, verification, chapters):
        entry = {
            'provider_key': route.provider_key, 'configured_model': route.model_id,
            'base_url': route.base_url, 'credential_slot': route.credential_slot,
            # Non-secret account identity (provider type + endpoint), so a
            # later call can refuse to pair this endpoint with a key that now
            # belongs to a different account.
            'account_id': route.account_id,
        }
        snapshot[route.phase] = entry
    snapshot['review']['gate'] = review_gate
    return snapshot


def _stale_snapshot_phases(snapshot: dict | None) -> list[tuple[str, str, str | None]]:
    """(phase, credential_slot, current_account_id) for every phase whose
    frozen account no longer matches its slot's configuration."""
    stale = []
    for phase, route in (snapshot or {}).items():
        if not isinstance(route, dict):
            continue
        mismatch = route_account_mismatch(route)
        if mismatch is not None:
            stale.append((phase, route.get('credential_slot', 'primary'), mismatch[1]))
    return stale


def _assert_route_snapshot_current(snapshot: dict | None) -> None:
    """Refuse to start or resume on routes frozen against a replaced account.

    A recovered run reloads its persisted snapshot, so without this it would
    spend calls pairing the old endpoint with the slot's new credential.
    """
    for phase, route in (snapshot or {}).items():
        if isinstance(route, dict) and route.get('credential_slot') == 'failover':
            raise ProviderAccountChangedError(
                'Legacy standby snapshot requires fresh original routes',
                credential_slot=route.get('failover_from', 'primary'), phase=phase)
    stale = _stale_snapshot_phases(snapshot)
    if not stale:
        return
    phase, slot, current = stale[0]
    raise ProviderAccountChangedError(
        f"{slot} provider account changed since this run resolved its "
        f"{phase} route",
        credential_slot=slot, phase=phase,
        expected_account_id=(snapshot or {}).get(phase, {}).get('account_id'),
        current_account_id=current)


def clear_route_snapshot(run_id: str | None) -> None:
    """Drop a run's persisted routes so its next attempt resolves fresh."""
    if not run_id:
        return
    try:
        conn = db.get_connection()
        conn.execute(
            "UPDATE processing_runs SET route_snapshot_json = NULL WHERE run_id = ?",
            (run_id,),
        )
        conn.commit()
    except Exception as exc:
        audio_logger.warning(f"Could not clear route snapshot for run {run_id}: {exc}")


def runs_for_account(account_id: str, credential_slot: str) -> list[dict]:
    """Active runs whose frozen routes name this (slot, account) pair.

    The rows a provider-account change strands: their endpoint belongs to the
    old account while the slot's key now belongs to the new one.
    """
    try:
        rows = db.get_connection().execute(
            """SELECT r.run_id, r.episode_id, r.state, r.route_snapshot_json,
                      p.slug
               FROM processing_runs r
               JOIN podcasts p ON p.id = r.podcast_id
               WHERE r.state IN ('running', 'cancel_requested')
                 AND r.route_snapshot_json IS NOT NULL""",
        ).fetchall()
    except Exception as exc:
        audio_logger.warning(f"Could not list runs for account {account_id}: {exc}")
        return []
    affected = []
    for row in rows:
        try:
            snapshot = json.loads(row['route_snapshot_json'])
        except (TypeError, ValueError):
            continue
        phases = sorted(
            phase for phase, route in (snapshot or {}).items()
            if isinstance(route, dict)
            and route.get('account_id') == account_id
            and route.get('credential_slot', 'primary') == credential_slot)
        if phases:
            affected.append({'runId': row['run_id'], 'slug': row['slug'],
                             'episodeId': row['episode_id'],
                             'state': row['state'], 'phases': phases})
    return affected


def _resolve_or_load_route_snapshot(run_id: str | None) -> dict | None:
    """This run's immutable route snapshot: reused from the row when a
    prior resolution for the same run_id already persisted one (recovery),
    else resolved fresh from current settings and persisted.
    """
    if run_id:
        existing = _load_route_snapshot(run_id)
        if existing is not None:
            return existing

    snapshot = _resolve_route_snapshot()
    if snapshot is not None and run_id:
        _persist_route_snapshot(run_id, snapshot)
    return snapshot
