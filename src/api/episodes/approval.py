"""Manual approval routes: list, approve one, approve in bulk, and held-detection list."""

import logging
from flask import request
from api import (
    api, limiter, log_request, json_response, error_response,
    get_database, get_storage, paged_args,
)
from api.episodes.core import _episode_base_json
from database.podcasts import is_local_feed, is_youtube_feed
from utils.time import utc_now_iso
from webhook_service import fire_event, EVENT_EPISODE_PROCESSED

logger = logging.getLogger('podcast.api')

BULK_APPROVE_MAX = 500
LIST_DEFAULT_LIMIT = 100


def _approval_row(row):
    out = {
        'slug': row['podcast_slug'],
        'episodeId': row['episode_id'],
        'title': row['title'],
        'podcast': row['podcast_title'],
        'adCount': row['ads_removed'] or 0,
        'pendingReviewCount': row['pending_review_count'] or 0,
    }
    if row['approval_pending_at']:
        out['pendingSince'] = row['approval_pending_at']
    return out


def _approve(db, slug, episode_id, podcast_title):
    """Stamp one approval and fire the Processed event. False when it was not waiting."""
    if not db.approve_episode(slug, episode_id, utc_now_iso()):
        return False
    episode = db.get_episode(slug, episode_id) or {}
    try:
        fire_event(
            event=EVENT_EPISODE_PROCESSED, episode_id=episode_id, slug=slug,
            episode_title=episode.get('title'), processing_time=None, llm_cost=None,
            ads_removed=episode.get('ads_removed') or 0,
            original_duration=episode.get('original_duration'),
            new_duration=episode.get('new_duration'),
            podcast_name=podcast_title)
    except Exception as exc:
        logger.warning(f"[{slug}:{episode_id}] Approval webhook failed: {exc}")
    return True


def _publish_feeds(db, slugs):
    """Clear each feed's etag so the next poll re-renders, rebuild its RSS, then the Recents feed."""
    # lazy: pipeline.common pulls in main_app, which imports this package
    from main_app.pipeline import common
    for slug in slugs:
        try:
            db.update_podcast(slug, etag=None, last_modified_header=None)
            common.refresh_feed_rss(slug)
        except Exception:
            logger.exception(f"[{slug}] RSS refresh after approval failed")
    try:
        common.rebuild_recents()
    except Exception:
        logger.exception("Recents rebuild after approval failed")


def _parse_targets(payload, db):
    """The (slug, episode_id) pairs a bulk body names, or an error response."""
    if isinstance(payload.get('episodes'), list):
        if len(payload['episodes']) > BULK_APPROVE_MAX:
            return None, error_response(f'At most {BULK_APPROVE_MAX} episodes per request', 400)
        targets = []
        for item in payload['episodes']:
            if (not isinstance(item, dict) or not isinstance(item.get('slug'), str)
                    or not isinstance(item.get('episodeId'), str)):
                return None, error_response('episodes entries need slug and episodeId strings', 400)
            targets.append((item['slug'], item['episodeId']))
        return targets, None
    if isinstance(payload.get('slug'), str) and payload['slug']:
        podcast = db.get_podcast_row(payload['slug'])
        if not podcast:
            return None, error_response('Feed not found', 404)
        ids = db.get_awaiting_approval_episode_ids(podcast['id'])
        return [(payload['slug'], episode_id) for episode_id in ids], None
    return None, error_response('Provide episodes or slug', 400)


@api.route('/feeds/<slug>/episodes/<episode_id>/approve', methods=['POST'])
@limiter.limit("60/minute")
@log_request
def approve_episode(slug, episode_id):
    """Publish an episode that is waiting for manual approval."""
    db = get_database()
    podcast = db.get_podcast_by_slug(slug)
    if not podcast:
        return error_response('Feed not found', 404)
    if not _approve(db, slug, episode_id, podcast.get('title') or slug):
        return error_response('Episode is not awaiting approval', 409)
    _publish_feeds(db, [slug])
    return json_response({
        'approved': True,
        'episode': _episode_base_json(
            db.get_episode(slug, episode_id), slug=slug, is_local=is_local_feed(podcast),
            is_youtube=is_youtube_feed(podcast), storage=get_storage()),
    })


@api.route('/episodes/awaiting-approval', methods=['GET'])
@log_request
def list_awaiting_approval():
    """Episodes waiting for manual approval across feeds, oldest first."""
    limit, offset = paged_args(LIST_DEFAULT_LIMIT, BULK_APPROVE_MAX)
    rows, total = get_database().list_review_episodes(
        'awaiting', slug=request.args.get('slug') or None, limit=limit, offset=offset)
    return json_response({'count': total, 'episodes': [_approval_row(r) for r in rows]})


@api.route('/episodes/awaiting-approval/approve', methods=['POST'])
@limiter.limit("10 per minute")
@log_request
def approve_awaiting_episodes():
    """Approve listed episodes, or every awaiting episode of one feed, with one RSS rebuild per feed."""
    db = get_database()
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return error_response('JSON body required', 400)
    targets, error = _parse_targets(payload, db)
    if error:
        return error
    remaining = max(0, len(targets) - BULK_APPROVE_MAX)
    targets = targets[:BULK_APPROVE_MAX]

    approved, skipped = 0, 0
    touched = set()
    titles = {}
    for slug, episode_id in targets:
        if slug not in titles:
            titles[slug] = (db.get_podcast_row(slug) or {}).get('title') or slug
        if _approve(db, slug, episode_id, titles[slug]):
            approved += 1
            touched.add(slug)
        else:
            skipped += 1
    _publish_feeds(db, sorted(touched))
    return json_response({'approved': approved, 'skipped': skipped, 'remaining': remaining})


@api.route('/episodes/held', methods=['GET'])
@log_request
def list_held_episodes():
    """Episodes with detections held for review across feeds, newest first."""
    limit, offset = paged_args(LIST_DEFAULT_LIMIT, BULK_APPROVE_MAX)
    rows, total = get_database().list_review_episodes('held', limit=limit, offset=offset)
    return json_response({'count': total, 'episodes': [_approval_row(r) for r in rows]})
