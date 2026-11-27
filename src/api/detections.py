"""Cross-episode ad detection review: /detections endpoint."""
import logging
from collections import Counter

from flask import request

from api import (
    api, limiter, log_request, json_response, error_response,
    get_database, get_feed_auth_key,
)
from api.audio_state import add_pattern_audio_state
import config
from config import ALL_HOLD_REASONS, SEGMENT_CATEGORIES, count_pending_review, resolve_hold_for_review
from detection_review import (
    UNSET_CATEGORY, filter_detections, flatten_detections, paginate,
    sort_detections, summarize_cut_detections, summarize_detections,
)
from utils.episode_paths import episode_public_url
from utils.markers import close_holds, parse_ad_markers

logger = logging.getLogger('podcast.api')

VALID_STATUS = {'needs_review', 'pending', 'rejected', 'accepted', 'all'}
VALID_SORT = {'date', 'confidence', 'podcast'}
VALID_ORDER = {'asc', 'desc'}
VALID_CATEGORY = set(SEGMENT_CATEGORIES) | {UNSET_CATEGORY}
VALID_REVIEWER = {'adjusted', 'unadjusted'}


@api.route('/detections', methods=['GET'])
@log_request
def list_detections():
    """List ad detections across all feeds with filter/sort/pagination."""
    db = get_database()

    limit = min(max(1, request.args.get('limit', 20, type=int)), 100)
    page = max(1, request.args.get('page', 1, type=int))
    status = request.args.get('status', 'needs_review')
    if status not in VALID_STATUS:
        return error_response(f"Invalid status '{status}'", 400)
    feed = request.args.get('feed') or None
    q = request.args.get('q') or None
    sort = request.args.get('sort', 'date')
    if sort not in VALID_SORT:
        return error_response(f"Invalid sort '{sort}'", 400)
    order = request.args.get('order', 'desc')
    if order not in VALID_ORDER:
        return error_response(f"Invalid order '{order}'", 400)
    category = request.args.get('category') or None
    if category is not None and category not in VALID_CATEGORY:
        return error_response(f"Invalid category '{category}'", 400)
    reviewer = request.args.get('reviewer') or None
    if reviewer is not None and reviewer not in VALID_REVIEWER:
        return error_response(f"Invalid reviewer '{reviewer}'", 400)
    hold_reason = request.args.get('holdReason') or None
    if hold_reason is not None and hold_reason not in ALL_HOLD_REASONS:
        return error_response(f"Invalid holdReason '{hold_reason}'", 400)

    rows = db.get_detection_rows()
    corrections = db.get_review_corrections()
    # Not persisted: a Keep<->Mark switch since a row's markers were stamped
    # shows up in actionApplied here without a recut.
    actions_by_feed = {}
    for row in rows:
        slug = row['feed_slug']
        if slug not in actions_by_feed:
            actions_by_feed[slug] = db.resolve_segment_actions(
                slug, {'segment_category_actions': row.get('segment_category_actions')})
    items = flatten_detections(rows, corrections, actions_by_feed=actions_by_feed)
    counts = summarize_detections(items)
    items = filter_detections(items, status=status, feed=feed)
    # Follows the feed and status filters only; q, reviewer, holdReason and category apply after.
    counts['pendingByHoldReason'] = dict(Counter(
        i['holdReason'] for i in items if i['holdReason']))
    items = filter_detections(items, status='all', q=q,
                              reviewer=reviewer, hold_reason=hold_reason)
    # Summarised before the category filter so byCategory keeps every bucket
    # while the podcast and search filters still narrow the header.
    cut_summary = summarize_cut_detections(
        [i for i in items if i['status'] == 'accepted'])
    if category:
        items = filter_detections(items, status='all', category=category)
    items = sort_detections(items, sort=sort, order=order)
    page_items, total, total_pages, page = paginate(items, page, limit)

    add_pattern_audio_state(db, page_items, 'patternId')

    # Same-origin processed-audio URL, carrying the feed auth key when
    # enabled so the waveform editor does not 401 (mirrors the episode
    # detail endpoint's processedUrl). Built only for the returned page.
    feed_auth_key = get_feed_auth_key(db)
    for item in page_items:
        version = item.pop('processedVersion', 0)
        item['processedUrl'] = episode_public_url(
            config.URL_PREFIX, item['feedSlug'], item['episodeId'], version,
            key=feed_auth_key)

    return json_response({
        'detections': page_items,
        'total': total,
        'page': page,
        'totalPages': total_pages,
        'limit': limit,
        'counts': counts,
        'cutSummary': cut_summary,
    })


@api.route('/detections/close-pending', methods=['POST'])
@limiter.limit("10 per minute")
@log_request
def close_pending_detections():
    """Close pending holds on feeds where holding is off, for one feed or all; no recut."""
    db = get_database()
    payload = request.get_json(silent=True)
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        return error_response('JSON object body required', 400)
    slug = payload.get('feed')
    if slug is not None and not isinstance(slug, str):
        return error_response('feed must be a feed slug string', 400)
    if slug and not db.get_podcast_row(slug):
        return error_response('Feed not found', 404)

    by_episode = {}
    for c in db.get_review_corrections():
        fps, confirmed = by_episode.setdefault((c['podcast_id'], c['episode_id']), ([], []))
        (fps if c['correction_type'] == 'false_positive' else confirmed).append(c)

    closed_total, episodes, skipped = 0, 0, 0
    holds = {}
    for row in db.get_held_episodes(slug or None):
        if row['slug'] not in holds:
            holds[row['slug']] = resolve_hold_for_review(db, row['slug'])
        if holds[row['slug']]:
            continue
        markers = parse_ad_markers(row['ad_markers_json'])
        if not markers:
            continue
        closed = close_holds(markers, by_episode.get((row['podcast_id'], row['episode_id']), ([], [])))
        if not closed:
            continue
        if not db.replace_markers_if_unchanged(row['slug'], row['episode_id'], row['ad_markers_json'],
                                               markers, count_pending_review(markers)):
            skipped += 1
            continue
        closed_total += closed
        episodes += 1
    logger.info(f"Closed {closed_total} pending hold(s) in {episodes} episode(s), skipped {skipped}")
    return json_response({'closed': closed_total, 'episodes': episodes, 'skipped': skipped})
