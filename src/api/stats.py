"""Stats routes: /stats/* endpoints."""
import logging
import math
from datetime import datetime
from decimal import Decimal

from flask import request

from api import (
    api, error_response, log_request, json_response,
    get_database,
)
# The ledger's own billable predicate, so this endpoint lists exactly the rows
# the totals are summed from rather than re-deriving the rule.
from database.stats import _ledger_row_is_billable
from utils.time import parse_iso_utc

logger = logging.getLogger('podcast.api')


_DATE_PARAM_ERROR = ("{name} must be a date (YYYY-MM-DD) or an ISO 8601 "
                     "timestamp")


def _date_param_is_valid(value: str) -> bool:
    """True when a from/to value parses as a bare date or an ISO timestamp.

    Same parse the db layer's day-start canonicalization uses, so the API
    never accepts a value the query then silently treats as raw text."""
    text = value.strip()
    if parse_iso_utc(text) is not None:
        return True
    try:
        datetime.strptime(text[:10], '%Y-%m-%d')
        return True
    except ValueError:
        return False


def _invalid_date_param(params: dict) -> str | None:
    """Name of the first unparseable from/to param, or None."""
    for name, key in (('from', 'from_date'), ('to', 'to_date')):
        value = params.get(key)
        if value and not _date_param_is_valid(value):
            return name
    return None


def _date_scope_params() -> dict:
    """from/to/podcastSlug scope for the ledger endpoints. from/to pass
    through verbatim: the db layer canonicalizes them to whole UTC days."""
    return {
        'from_date': request.args.get('from'),
        'to_date': request.args.get('to'),
        'podcast_slug': request.args.get('podcastSlug'),
    }


def _parse_list_params():
    """Shared page/limit/sortBy/sortDir/filter parsing for the ledger list
    endpoints below. Mirrors GET /history's page+limit contract."""
    limit = min(max(1, request.args.get('limit', 50, type=int)), 100)
    page = max(1, request.args.get('page', 1, type=int))
    return {
        'page': page,
        'limit': limit,
        'sort_by': request.args.get('sortBy', ''),
        'sort_dir': request.args.get('sortDir', 'desc'),
        **_date_scope_params(),
        'provider': request.args.get('provider'),
        'model': request.args.get('model'),
    }


@api.route('/stats/dashboard', methods=['GET'])
@log_request
def get_dashboard_stats():
    """Get aggregate dashboard statistics with avg/min/max."""
    db = get_database()
    podcast_slug = request.args.get('podcast_slug')
    stats = db.get_dashboard_stats(podcast_slug=podcast_slug)
    return json_response(stats)


@api.route('/stats/by-day', methods=['GET'])
@log_request
def get_stats_by_day():
    """Get episode processing counts by day of week."""
    db = get_database()
    podcast_slug = request.args.get('podcast_slug')
    data = db.get_stats_by_day(podcast_slug=podcast_slug)
    return json_response({'days': data})


@api.route('/stats/by-podcast', methods=['GET'])
@log_request
def get_stats_by_podcast():
    """Get per-podcast aggregate stats."""
    db = get_database()
    data = db.get_stats_by_podcast()
    return json_response({'podcasts': data})


@api.route('/stats/reviewer', methods=['GET'])
@log_request
def get_reviewer_stats():
    """Aggregate ad reviewer stats.

    Optional filters: ``podcast_slug`` and ``episode_id``. Without either,
    returns global aggregates over the ad_reviewer_log table.
    """
    db = get_database()
    podcast_slug = request.args.get('podcast_slug')
    episode_id = request.args.get('episode_id')
    return json_response(db.get_reviewer_stats(
        podcast_slug=podcast_slug, episode_id=episode_id
    ))


@api.route('/stats/addressing', methods=['GET'])
@log_request
def get_addressing_stats():
    """Per-addressing-mode LLM contract compliance aggregates."""
    db = get_database()
    podcast_slug = request.args.get('podcast_slug')
    return json_response(db.get_addressing_stats(podcast_slug=podcast_slug))


@api.route('/stats/model-usage', methods=['GET'])
@log_request
def get_model_usage_stats():
    """Paginated spend by (provider, model) over the llm_call_usage ledger.

    The from/to filters select whole UTC days, inclusive of both ends.
    """
    db = get_database()
    params = _parse_list_params()
    invalid = _invalid_date_param(params)
    if invalid:
        return error_response(_DATE_PARAM_ERROR.format(name=invalid), 400)
    items, total = db.get_model_usage_stats(**params)
    return json_response({
        'items': items,
        'total': total,
        'totalPages': math.ceil(total / params['limit']) if total > 0 else 1,
        'page': params['page'],
        'limit': params['limit'],
    })


@api.route('/stats/episode-costs', methods=['GET'])
@log_request
def get_episode_cost_stats():
    """Paginated per-episode cost breakdown over the llm_call_usage ledger.

    The from/to filters select whole UTC days, inclusive of both ends.
    """
    db = get_database()
    params = _parse_list_params()
    invalid = _invalid_date_param(params)
    if invalid:
        return error_response(_DATE_PARAM_ERROR.format(name=invalid), 400)
    items, total = db.get_episode_cost_stats(**params)
    return json_response({
        'items': items,
        'total': total,
        'totalPages': math.ceil(total / params['limit']) if total > 0 else 1,
        'page': params['page'],
        'limit': params['limit'],
    })


@api.route('/stats/episode-costs/runs', methods=['GET'])
@log_request
def get_episode_cost_runs():
    """Per-run, per-phase cost breakdown for one episode, for the expandable
    Episode-costs row. Same run shape as the episode detail page so both
    surfaces render an identical table."""
    from api.episodes import _processing_runs
    slug = request.args.get('slug')
    episode_id = request.args.get('episodeId')
    if not slug or not episode_id:
        return error_response('slug and episodeId are required', 400)
    db = get_database()
    episode = db.get_episode(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)
    return json_response({'runs': _processing_runs(db, episode)})


@api.route('/stats/ledger-filter-options', methods=['GET'])
@log_request
def get_ledger_filter_options():
    """Distinct providers and provider/model pairs in the ledger, for the
    Stats filters.

    Honors the same from/to/podcastSlug scope as the list endpoints (from/to
    select whole UTC days) but is never paginated, so a provider or model
    past the first page of a list request can still be selected.
    """
    params = _date_scope_params()
    invalid = _invalid_date_param(params)
    if invalid:
        return error_response(_DATE_PARAM_ERROR.format(name=invalid), 400)
    pairs = get_database().get_ledger_filter_options(
        from_date=params['from_date'], to_date=params['to_date'],
        podcast_slug=params['podcast_slug'])
    return json_response({
        'providers': sorted({p['provider'] for p in pairs}),
        'pairs': pairs,
    })


# One literal statement with named params, so optional scoping never
# concatenates SQL.
_SPEND_ATTEMPTS_SQL = """
    SELECT attempt_id, phase_key, invoking_pass, provider_key, credential_slot,
           configured_model, returned_model, state, input_tokens, output_tokens,
           cost_usd, cost_source, created_at, finalized_at
    FROM llm_call_usage
    WHERE finalized_at IS NOT NULL
      AND (:run_id = '' OR run_id = :run_id)
      AND (:podcast_id < 0 OR (podcast_id = :podcast_id AND episode_id = :episode_id))
      AND (:provider = '' OR provider_key = :provider)
    ORDER BY created_at, attempt_id
    LIMIT :limit
"""

_SPEND_ATTEMPTS_LIMIT = 200


def _spend_attempt_json(row) -> dict:
    return {
        'attemptId': row['attempt_id'],
        'phase': row['phase_key'],
        'invokingPass': row['invoking_pass'],
        'provider': row['provider_key'],
        'credentialSlot': row['credential_slot'] or 'primary',
        'model': row['configured_model'],
        'returnedModel': row['returned_model'],
        'status': row['state'],
        'inputTokens': row['input_tokens'],
        'outputTokens': row['output_tokens'],
        'costUsd': row['cost_usd'],
        'costSource': row['cost_source'],
        'createdAt': row['created_at'],
        'finalizedAt': row['finalized_at'],
    }


@api.route('/stats/spend/attempts', methods=['GET'])
@log_request
def get_spend_attempts():
    """Ledger attempts behind one run's or episode's spend.

    Backs the Incomplete chip, which needs to point at the attempt carrying
    no price. Reuses the billable predicate the totals are summed with, so
    the listed rows are exactly the contributing ones.
    """
    db = get_database()
    run_id = request.args.get('runId') or ''
    slug = request.args.get('slug')
    episode_id = request.args.get('episodeId')
    if not run_id and not (slug and episode_id):
        return error_response('runId, or slug and episodeId, is required', 400)

    podcast_id = -1
    if slug and episode_id:
        podcast = db.get_podcast_by_slug(slug)
        if not podcast:
            return error_response('Feed not found', 404)
        podcast_id = podcast['id']

    provider = request.args.get('provider') or ''
    rows = db.get_connection().execute(_SPEND_ATTEMPTS_SQL, {
        'run_id': run_id, 'podcast_id': podcast_id, 'episode_id': episode_id or '',
        'provider': provider, 'limit': _SPEND_ATTEMPTS_LIMIT + 1,
    }).fetchall()
    truncated = len(rows) > _SPEND_ATTEMPTS_LIMIT
    billable = [r for r in rows[:_SPEND_ATTEMPTS_LIMIT] if _ledger_row_is_billable(
        r['state'], r['input_tokens'], r['output_tokens'],
        float(r['cost_usd']) if r['cost_usd'] is not None else 0.0)]
    known = sum((Decimal(r['cost_usd']) for r in billable if r['cost_usd'] is not None),
                Decimal('0'))
    return json_response({
        'runId': run_id or None,
        'episodeId': episode_id,
        'provider': provider or None,
        'attempts': [_spend_attempt_json(r) for r in billable],
        'total': len(billable),
        'unknownCostCount': sum(1 for r in billable if r['cost_usd'] is None),
        'knownCostUsd': str(known),
        # More attempts exist than the cap returns; the totals still cover them.
        'truncated': truncated,
    })
