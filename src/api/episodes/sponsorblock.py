"""Manual SponsorBlock submission: list reviewable segments and send the ones a person picks."""

import json
import logging

from flask import request

from api import api, limiter, log_request, json_response, error_response, get_database
from api.episodes.common import sponsorblock_block_reason
from config import known_ad_evidence, normalize_segment_category
from utils.json_fields import load_json_list
from utils.markers import parse_ad_markers, spans_match
from youtube import settings as yt_settings
from youtube import sponsorblock_submit as sbs

logger = logging.getLogger('podcast.api')

MAX_SUBMIT_SEGMENTS = 20
_STATUS_CODES = {'rate_limited': 429, 'error': 502}


def _confirmed_markers(db, podcast, episode_id, markers) -> list[dict]:
    """Copies of the markers with a stored confirm correction flagged as user confirmed."""
    corrections = [c for c in db.get_confirmed_corrections(podcast['id'], episode_id)
                   if c.get('correction_type') == 'confirm']
    out = []
    for marker in markers:
        if any(spans_match(marker.get('start'), marker.get('end'), c.get('start'), c.get('end'))
               for c in corrections):
            marker = {**marker, 'validation': {**(marker.get('validation') or {}), 'user_confirmed': True}}
        out.append(marker)
    return out


def _context(slug, episode_id):
    """(db, podcast, episode, block reason, candidates); each candidate carries its submission."""
    db = get_database()
    podcast = db.get_podcast_by_slug(slug)
    episode = db.get_episode(slug, episode_id) if podcast else None
    if not episode:
        return db, podcast, None, None, []
    reason = sponsorblock_block_reason(db, podcast, episode)
    if reason:
        return db, podcast, episode, reason, []
    markers = parse_ad_markers(episode.get('ad_markers_json')) or []
    recorded = load_json_list(episode.get('sb_submissions'))
    candidates = [{**m, 'submission': sbs.find_submission(recorded, m)}
                  for m in sbs.eligible_markers(_confirmed_markers(db, podcast, episode_id, markers))]
    return db, podcast, episode, None, candidates


@api.route('/feeds/<slug>/episodes/<episode_id>/sponsorblock/candidates', methods=['GET'])
@log_request
def sponsorblock_candidates(slug, episode_id):
    """Segments a person may review for SponsorBlock, with any recorded outcome."""
    _, _, episode, reason, candidates = _context(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)
    if reason:
        return json_response({'allowed': False, 'reason': reason, 'segments': []})
    segments = [{
        'start': m['start'], 'end': m['end'], 'category': m.get('category'),
        'sbCategory': sbs.SUBMIT_CATEGORIES[normalize_segment_category(m.get('category'))],
        'evidence': known_ad_evidence(m), 'submission': m['submission'],
    } for m in candidates]
    return json_response({'allowed': True, 'reason': None, 'segments': segments})


@api.route('/feeds/<slug>/episodes/<episode_id>/sponsorblock/submit', methods=['POST'])
@limiter.limit('10 per minute')
@log_request
def sponsorblock_submit(slug, episode_id):
    """Send the segments a person selected; every outcome is recorded on the episode."""
    db, _, episode, reason, candidates = _context(slug, episode_id)
    if not episode:
        return error_response('Episode not found', 404)
    if reason:
        return error_response(f'SponsorBlock submission is not available: {reason}', 400)
    data = request.get_json(silent=True)
    raw = data.get('segments') if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw:
        return error_response('segments must be a non-empty list', 400)
    if len(raw) > MAX_SUBMIT_SEGMENTS:
        return error_response(f'at most {MAX_SUBMIT_SEGMENTS} segments per request', 400)
    chosen = {}
    for item in raw:
        start, end = (item.get('start'), item.get('end')) if isinstance(item, dict) else (None, None)
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in (start, end)):
            return error_response('each segment needs numeric start and end', 400)
        match = next((c for c in candidates if spans_match(c['start'], c['end'], start, end)), None)
        if match is None:
            return error_response(f'No reviewable segment at {start}-{end}', 400)
        if (match['submission'] or {}).get('status') in sbs.FINAL_STATUSES:
            return error_response(f'Segment {start}-{end} was already submitted', 400)
        chosen[(match['start'], match['end'])] = match
    markers = list(chosen.values())
    status, detail, created = sbs.submit(
        sbs.build_payload(episode['youtube_video_id'], yt_settings.sb_user_id(db), markers))
    results = sbs.build_records(markers, status, detail, created)
    recorded = load_json_list(episode.get('sb_submissions'))
    db.update_episode_sponsorblock(slug, episode_id,
                                   sb_submissions=json.dumps(sbs.merge_records(recorded, results)))
    logger.info(f"[{slug}:{episode_id}] SponsorBlock submit {status} segments={len(results)}")
    return json_response({'results': results}, _STATUS_CODES.get(status, 200))
