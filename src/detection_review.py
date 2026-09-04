"""Pure aggregation logic for the cross-episode ad review endpoint.

Flattens per-episode ad_markers_json rows into one detection list with a
computed status (same three-bucket logic as the episode detail endpoint)
and a resolution derived from corrections: false_positive dismisses, while
confirm and boundary_adjustment (an Edit that kept the ad) confirm.
Kept free of Flask and DB imports so it can be unit tested directly.
"""
import json
import math

from config import SEGMENT_CATEGORIES, is_pending_review

# Same tolerance the reject path uses to clear held markers
# (_clear_held_marker_on_reject in api/patterns.py).
BOUNDS_TOLERANCE_S = 0.5

# Filter value and summary key for markers no stage classified. Not a member of
# SEGMENT_CATEGORIES: unset is the absence of a category, not a category.
UNSET_CATEGORY = 'none'


def marker_status(marker: dict) -> str:
    """Mirror of the episode endpoint's bucketing (api/episodes.py)."""
    if is_pending_review(marker):
        return 'pending'
    decision = (marker.get('validation') or {}).get('decision', 'ACCEPT')
    if decision == 'REJECT' or not marker.get('was_cut', True):
        return 'rejected'
    return 'accepted'


def marker_resolution(marker: dict, episode_corrections: list[dict]) -> str:
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
            return 'dismissed' if c['correction_type'] == 'false_positive' else 'confirmed'
    return 'unresolved'


def flatten_detections(rows: list[dict], corrections: list[dict]) -> list[dict]:
    by_episode: dict[str, list[dict]] = {}
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
            if not isinstance(marker.get('start'), (int, float)) or not isinstance(marker.get('end'), (int, float)):
                continue
            items.append({
                'feedSlug': row['feed_slug'],
                'feedTitle': row['feed_title'],
                'episodeId': row['episode_id'],
                'episodeTitle': row['episode_title'],
                'publishDate': row.get('published_at') or row.get('created_at'),
                'hasOriginalAudio': bool(row.get('original_file')),
                # The waveform editor slices its window against this; without
                # it the editor assumes a short default and opens on the wrong
                # part of the episode at max zoom.
                'episodeDuration': row.get('original_duration'),
                # Consumed by the endpoint to build processedUrl, then dropped
                # from the response.
                'processedVersion': row.get('processed_version') or 0,
                'start': marker.get('start'),
                'end': marker.get('end'),
                'confidence': marker.get('confidence'),
                'sponsor': marker.get('sponsor'),
                'reason': marker.get('reason'),
                'patternId': marker.get('pattern_id'),
                'detectionStage': marker.get('detection_stage'),
                'category': marker.get('category'),
                'actionApplied': marker.get('action_applied'),
                'reviewerVerdict': marker.get('reviewer_verdict'),
                'reviewerOriginalStart': marker.get('reviewer_original_start'),
                'reviewerOriginalEnd': marker.get('reviewer_original_end'),
                'status': marker_status(marker),
                'resolution': marker_resolution(marker, episode_corrections),
            })
    return items


def summarize_detections(items: list[dict]) -> dict:
    """Pre-filter overview counts for the review tab's stats card."""
    counts = {
        'total': len(items),
        'needsReview': 0,
        'pending': 0,
        'rejected': 0,
        'accepted': 0,
        'confirmed': 0,
        'dismissed': 0,
    }
    for item in items:
        counts[item['status']] += 1
        if item['resolution'] == 'confirmed':
            counts['confirmed'] += 1
        elif item['resolution'] == 'dismissed':
            counts['dismissed'] += 1
        elif awaits_decision(item):
            counts['needsReview'] += 1
    return counts


def summarize_cut_detections(items: list[dict]) -> dict:
    """Totals for the Detected Ads header, over detections that were cut.

    Callers pass the already-cut subset; this does not filter by status itself.
    """
    by_category = {cat: 0 for cat in SEGMENT_CATEGORIES}
    by_category[UNSET_CATEGORY] = 0
    duration = 0.0
    sponsors = set()
    podcasts = set()
    for item in items:
        key = item.get('category') or UNSET_CATEGORY
        by_category[key] = by_category.get(key, 0) + 1
        start, end = item.get('start'), item.get('end')
        if start is not None and end is not None and end > start:
            duration += end - start
        sponsor = (item.get('sponsor') or '').strip().lower()
        if sponsor:
            sponsors.add(sponsor)
        if item.get('feedSlug'):
            podcasts.add(item['feedSlug'])
    return {
        'count': len(items),
        'durationSeconds': round(duration, 3),
        'byCategory': by_category,
        'distinctSponsors': len(sponsors),
        'distinctPodcasts': len(podcasts),
    }


def awaits_decision(item: dict) -> bool:
    """True when a person still has a decision to make about this detection.

    A keep marker is settled by feed policy and the corrections endpoint
    refuses a verdict on it, so listing it would offer an impossible decision.
    """
    return (item['status'] in ('pending', 'rejected')
            and item['resolution'] == 'unresolved'
            and item.get('actionApplied') != 'keep')


def filter_detections(items: list[dict], status: str = 'needs_review',
                      feed: str | None = None,
                      q: str | None = None,
                      category: str | None = None,
                      reviewer: str | None = None) -> list[dict]:
    out = items
    if status == 'needs_review':
        out = [i for i in out if awaits_decision(i)]
    elif status in ('pending', 'rejected', 'accepted'):
        out = [i for i in out if i['status'] == status]
    if category == UNSET_CATEGORY:
        out = [i for i in out if not i.get('category')]
    elif category:
        out = [i for i in out if i.get('category') == category]
    if reviewer == 'adjusted':
        out = [i for i in out if i.get('reviewerVerdict') == 'adjust']
    elif reviewer == 'unadjusted':
        out = [i for i in out if i.get('reviewerVerdict') != 'adjust']
    if feed:
        out = [i for i in out if i['feedSlug'] == feed]
    if q:
        needle = q.lower()
        out = [i for i in out
               if needle in (i['sponsor'] or '').lower()
               or needle in (i['reason'] or '').lower()]
    return out


def sort_detections(items: list[dict], sort: str = 'date',
                    order: str = 'desc') -> list[dict]:
    reverse = order == 'desc'
    if sort == 'confidence':
        # None confidences always sort last regardless of direction, so the
        # group flag must flip with the sort direction.
        def key(i):
            c = i['confidence']
            has = c is not None
            # publishDate/start tie-breaks keep pagination deterministic,
            # matching the other sort branches.
            return (has if reverse else not has, c if has else 0,
                    i['publishDate'] or '', i['start'] or 0)
        return sorted(items, key=key, reverse=reverse)
    if sort == 'podcast':
        key = lambda i: ((i['feedTitle'] or '').lower(),
                         i['publishDate'] or '', i['start'] or 0)
        return sorted(items, key=key, reverse=reverse)
    key = lambda i: (i['publishDate'] or '', i['start'] or 0)
    return sorted(items, key=key, reverse=reverse)


def paginate(items: list[dict], page: int,
             limit: int) -> tuple[list[dict], int, int, int]:
    total = len(items)
    total_pages = math.ceil(total / limit) if total else 1
    page = min(max(1, page), total_pages)
    start = (page - 1) * limit
    return items[start:start + limit], total, total_pages, page
