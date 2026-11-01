"""Episode quality rating; a 5-star rating confirms every cut ad segment."""

import logging

from flask import request

from api import api, log_request, json_response, error_response, get_database
from api.episodes.common import rating_json
from api.patterns import _handle_confirm_correction
from config import AD_CATEGORIES, is_keep_like, is_pending_review, normalize_segment_category
from pattern_service import PatternService
from utils.markers import parse_ad_markers, spans_match

logger = logging.getLogger('podcast.api')

MAX_NOTES_CHARS = 2000


def _needs_confirm(marker, existing) -> bool:
    """A cut ad span that is neither a community mark, held, kept, nor already confirmed."""
    start, end = marker.get('start'), marker.get('end')
    return not (not marker.get('was_cut') or is_pending_review(marker) or start is None or end is None
                or marker.get('source') == 'sponsorblock'
                or is_keep_like(marker.get('action_applied'))
                or normalize_segment_category(marker.get('category')) not in AD_CATEGORIES
                or (marker.get('validation') or {}).get('user_confirmed')
                or any(spans_match(start, end, c.get('start'), c.get('end')) for c in existing))


def _confirm_cut_markers(db, slug, episode_id) -> int:
    """Record a confirm correction for each cut ad marker not yet confirmed; returns how many."""
    episode = db.get_episode(slug, episode_id) or {}
    podcast = db.get_podcast_by_slug(slug)
    existing = db.get_confirmed_corrections(podcast['id'], episode_id)
    transcript = db.get_transcript_for_timestamps(slug, episode_id)
    service = PatternService(db)
    confirmed = 0
    for marker in parse_ad_markers(episode.get('ad_markers_json')) or []:
        if not _needs_confirm(marker, existing):
            continue
        original_ad = {'start': float(marker['start']), 'end': float(marker['end']),
                       'pattern_id': marker.get('pattern_id'), 'sponsor': marker.get('sponsor'),
                       'reason': marker.get('reason', '')}
        response = _handle_confirm_correction(
            db, service, slug, episode_id, original_ad, {}, addressing_mode=marker.get('addressing_mode'),
            podcast=podcast, transcript=transcript)
        if getattr(response, 'status_code', 500) < 400:
            confirmed += 1
        else:
            logger.warning(f"[{slug}:{episode_id}] Rating confirm skipped {marker['start']}-{marker['end']}: "
                           f"{response.status_code}")
    return confirmed


@api.route('/feeds/<slug>/episodes/<episode_id>/rating', methods=['PUT'])
@log_request
def put_episode_rating(slug, episode_id):
    """Rate the episode 1 to 5; notes are kept below 5 and a 5 confirms the cut ads."""
    db = get_database()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response('JSON body required', 400)
    rating, notes = data.get('rating'), data.get('notes')
    if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
        return error_response('rating must be an integer from 1 to 5', 400)
    if notes is not None and not isinstance(notes, str):
        return error_response('notes must be a string or null', 400)
    notes = (notes or '').strip() or None
    if notes and len(notes) > MAX_NOTES_CHARS:
        return error_response(f'notes must be at most {MAX_NOTES_CHARS} characters', 400)
    if rating == 5:
        notes = None
    if not db.set_episode_rating(slug, episode_id, rating, notes):
        return error_response('Episode not found', 404)
    confirmed = _confirm_cut_markers(db, slug, episode_id) if rating == 5 else 0
    return json_response({'rating': rating_json(db.get_episode(slug, episode_id)),
                          'confirmedMarkers': confirmed})


@api.route('/feeds/<slug>/episodes/<episode_id>/rating', methods=['DELETE'])
@log_request
def delete_episode_rating(slug, episode_id):
    """Clear the rating and its notes; earlier confirmations stay recorded."""
    db = get_database()
    if not db.set_episode_rating(slug, episode_id, None):
        return error_response('Episode not found', 404)
    return json_response({'rating': None})
