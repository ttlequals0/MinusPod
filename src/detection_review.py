"""Pure aggregation logic for the cross-episode ad review endpoint.

Flattens per-episode ad_markers_json rows into one detection list with a
computed status (same three-bucket logic as the episode detail endpoint)
and a resolution derived from confirm/false_positive corrections.
Kept free of Flask and DB imports so it can be unit tested directly.
"""
import json
import math
from typing import Dict, List, Optional, Tuple

from config import is_pending_review

# Same tolerance the reject path uses to clear held markers
# (_clear_held_marker_on_reject in api/patterns.py).
BOUNDS_TOLERANCE_S = 0.5


def marker_status(marker: Dict) -> str:
    """Mirror of the episode endpoint's bucketing (api/episodes.py)."""
    if is_pending_review(marker):
        return 'pending'
    decision = (marker.get('validation') or {}).get('decision', 'ACCEPT')
    if decision == 'REJECT' or not marker.get('was_cut', True):
        return 'rejected'
    return 'accepted'


def marker_resolution(marker: Dict, episode_corrections: List[Dict]) -> str:
    start = marker.get('start')
    end = marker.get('end')
    if start is None or end is None:
        return 'unresolved'
    for c in episode_corrections:
        c_start = c.get('start')
        c_end = c.get('end')
        if c_start is None or c_end is None:
            continue
        if (abs(start - c_start) <= BOUNDS_TOLERANCE_S
                and abs(end - c_end) <= BOUNDS_TOLERANCE_S):
            return 'confirmed' if c['correction_type'] == 'confirm' else 'dismissed'
    return 'unresolved'


def flatten_detections(rows: List[Dict], corrections: List[Dict]) -> List[Dict]:
    by_episode: Dict[str, List[Dict]] = {}
    for c in corrections:
        by_episode.setdefault(c['episode_id'], []).append(c)

    items = []
    for row in rows:
        try:
            markers = json.loads(row['ad_markers_json'])
        except (TypeError, ValueError):
            continue
        if not isinstance(markers, list):
            continue
        episode_corrections = by_episode.get(row['episode_id'], [])
        for marker in markers:
            if not isinstance(marker, dict):
                continue
            items.append({
                'feedSlug': row['feed_slug'],
                'feedTitle': row['feed_title'],
                'episodeId': row['episode_id'],
                'episodeTitle': row['episode_title'],
                'publishDate': row.get('published_at') or row.get('created_at'),
                'hasOriginalAudio': bool(row.get('original_file')),
                'start': marker.get('start'),
                'end': marker.get('end'),
                'confidence': marker.get('confidence'),
                'sponsor': marker.get('sponsor'),
                'reason': marker.get('reason'),
                'patternId': marker.get('pattern_id'),
                'detectionStage': marker.get('detection_stage'),
                'status': marker_status(marker),
                'resolution': marker_resolution(marker, episode_corrections),
            })
    return items


def filter_detections(items: List[Dict], status: str = 'needs_review',
                      feed: Optional[str] = None,
                      q: Optional[str] = None) -> List[Dict]:
    out = items
    if status == 'needs_review':
        out = [i for i in out
               if i['status'] in ('pending', 'rejected')
               and i['resolution'] == 'unresolved']
    elif status in ('pending', 'rejected', 'accepted'):
        out = [i for i in out if i['status'] == status]
    if feed:
        out = [i for i in out if i['feedSlug'] == feed]
    if q:
        needle = q.lower()
        out = [i for i in out
               if needle in (i['sponsor'] or '').lower()
               or needle in (i['reason'] or '').lower()]
    return out


def sort_detections(items: List[Dict], sort: str = 'date',
                    order: str = 'desc') -> List[Dict]:
    reverse = order == 'desc'
    if sort == 'confidence':
        # None confidences always sort last regardless of direction, so the
        # group flag must flip with the sort direction.
        if reverse:
            key = lambda i: (1 if i['confidence'] is not None else 0,
                             i['confidence'] if i['confidence'] is not None else 0)
            return sorted(items, key=key, reverse=True)
        key = lambda i: (0 if i['confidence'] is not None else 1,
                         i['confidence'] if i['confidence'] is not None else 0)
        return sorted(items, key=key)
    if sort == 'podcast':
        key = lambda i: ((i['feedTitle'] or '').lower(),
                         i['publishDate'] or '', i['start'] or 0)
        return sorted(items, key=key, reverse=reverse)
    key = lambda i: (i['publishDate'] or '', i['start'] or 0)
    return sorted(items, key=key, reverse=reverse)


def paginate(items: List[Dict], page: int,
             limit: int) -> Tuple[List[Dict], int, int, int]:
    total = len(items)
    total_pages = math.ceil(total / limit) if total else 1
    page = min(max(1, page), total_pages)
    start = (page - 1) * limit
    return items[start:start + limit], total, total_pages, page
