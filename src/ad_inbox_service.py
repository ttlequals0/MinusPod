"""Pure helpers for the Ad Inbox.

Status derivation lives here (no Flask import) so unit tests can exercise the
logic without standing up the full API blueprint stack.
"""
import json
from typing import Iterator


# Mirrors the threshold used by ``delete_conflicting_corrections`` in
# ``database/patterns.py`` so a confirm/reject/adjust the user submitted via
# the existing AdEditor maps to the same ad row in the Inbox.
OVERLAP_THRESHOLD = 0.5


# Map pattern_corrections.correction_type → user-facing inbox status.
CORRECTION_TYPE_TO_STATUS = {
    'confirm': 'confirmed',
    'false_positive': 'rejected',
    'boundary_adjustment': 'adjusted',
    'promotion': 'confirmed',
}

VALID_INBOX_STATUSES = {'pending', 'confirmed', 'rejected', 'adjusted', 'all'}


def bounds_overlap_50(a_start: float, a_end: float,
                      b_start: float, b_end: float) -> bool:
    """Return True if the two ranges overlap by ≥50% of the shorter one."""
    a_len = max(0.0, a_end - a_start)
    b_len = max(0.0, b_end - b_start)
    if a_len == 0 or b_len == 0:
        return False
    overlap = max(0.0, min(a_end, b_end) - max(a_start, b_start))
    return overlap / min(a_len, b_len) >= OVERLAP_THRESHOLD


def enumerate_inbox_items(db) -> Iterator[dict]:
    """Yield one dict per detected ad across all episodes.

    Pulls episode_details.ad_markers_json + pattern_corrections in two queries
    (no N+1) and joins them in Python. Results are emitted in the same order
    as ``get_all_ad_markers`` (newest published episodes first), with ad index
    preserved so the UI can stably address each item by ``episode_id`` + idx.
    """
    rows = db.get_all_ad_markers()
    if not rows:
        return

    episode_ids = [r['episode_id'] for r in rows]
    corrections_by_episode: dict[str, list[dict]] = {}
    for c in db.get_corrections_for_episodes(episode_ids):
        corrections_by_episode.setdefault(c['episode_id'], []).append(c)

    for row in rows:
        try:
            markers = json.loads(row['ad_markers_json'] or '[]')
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(markers, list):
            continue

        ep_corrections = corrections_by_episode.get(row['episode_id'], [])

        for idx, ad in enumerate(markers):
            if not isinstance(ad, dict):
                continue
            try:
                start = float(ad.get('start'))
                end = float(ad.get('end'))
            except (TypeError, ValueError):
                continue

            status = 'pending'
            matched_correction = None
            for c in ep_corrections:
                bounds_raw = c.get('original_bounds')
                if not bounds_raw:
                    continue
                try:
                    parsed = json.loads(bounds_raw)
                    c_start = float(parsed.get('start'))
                    c_end = float(parsed.get('end'))
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if bounds_overlap_50(start, end, c_start, c_end):
                    derived = CORRECTION_TYPE_TO_STATUS.get(c['correction_type'])
                    if derived:
                        status = derived
                        matched_correction = c
                        break

            corrected_bounds = None
            if matched_correction and matched_correction.get('corrected_bounds'):
                try:
                    corrected_bounds = json.loads(matched_correction['corrected_bounds'])
                except (TypeError, json.JSONDecodeError):
                    pass

            yield {
                'podcastSlug': row['podcast_slug'],
                'podcastTitle': row['podcast_title'],
                'episodeId': row['episode_id'],
                'episodeTitle': row['episode_title'],
                'publishedAt': row['published_at'],
                'processedVersion': row['processed_version'],
                'adIndex': idx,
                'start': start,
                'end': end,
                'duration': max(0.0, end - start),
                'sponsor': ad.get('sponsor'),
                'reason': ad.get('reason'),
                'confidence': ad.get('confidence'),
                'detectionStage': ad.get('detection_stage'),
                'patternId': ad.get('pattern_id'),
                'status': status,
                'correctedBounds': corrected_bounds,
            }
