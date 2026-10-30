"""SponsorBlock lookups (browser-extension style, hashed prefix) and marker mapping."""
import hashlib
import json
import logging
import math

from utils.app_version import APP_VERSION
from utils.safe_http import URLTrust, safe_get

logger = logging.getLogger(__name__)

API = 'https://sponsor.ajay.app/api/skipSegments'
CATEGORY_MAP = {'sponsor': 'sponsor', 'selfpromo': 'self_promo', 'interaction': 'interaction',
                'intro': 'intro', 'outro': 'outro', 'preview': 'recap'}
# Pattern label for a mark whose caption text names no sponsor.
SPONSORBLOCK_CATEGORY_LABELS = {
    'sponsor': 'Sponsor read', 'self_promo': 'Self promotion', 'intro': 'Intro',
    'outro': 'Outro', 'interaction': 'Interaction reminder', 'recap': 'Recap',
}
REQUEST_CATEGORIES = list(CATEGORY_MAP)
ACTION_TYPES = ['skip', 'mute', 'full']
DURATION_TOLERANCE_S = 2.0
TIMEOUT_S = 15


def hash_prefix(video_id: str) -> str:
    return hashlib.sha256(video_id.encode('utf-8')).hexdigest()[:4]


def fetch_segments(video_id: str) -> list[dict] | None:
    url = (f"{API}/{hash_prefix(video_id)}?categories={json.dumps(REQUEST_CATEGORIES, separators=(',', ':'))}"
           f"&actionTypes={json.dumps(ACTION_TYPES, separators=(',', ':'))}&service=YouTube")
    headers = {'X-CLIENT-NAME': f'MinusPod/{APP_VERSION}', 'User-Agent': f'MinusPod/{APP_VERSION}'}
    for _ in range(2):
        try:
            resp = safe_get(url, URLTrust.OPERATOR_CONFIGURED, timeout=TIMEOUT_S, headers=headers)
        except Exception as exc:
            logger.warning(f"SponsorBlock request failed: {exc}")
            return None
        if resp.status_code == 404:
            return []
        if resp.status_code >= 500:
            continue
        if resp.status_code != 200:
            logger.warning(f"SponsorBlock returned HTTP {resp.status_code}")
            return None
        try:
            payload = resp.json()
        except ValueError:
            return None
        for entry in payload if isinstance(payload, list) else []:
            if isinstance(entry, dict) and entry.get('videoID') == video_id:
                return [seg for seg in (entry.get('segments') or []) if isinstance(seg, dict)]
        return []
    logger.warning("SponsorBlock server errors on both attempts")
    return None


def _parse_segment(seg, limit):
    """Return (start, end, votes) for a usable segment, else None."""
    try:
        votes = int(seg.get('votes') or 0)
        start, end = (float(x) for x in seg['segment'])
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not (math.isfinite(start) and math.isfinite(end)):
        return None
    if limit:
        start, end = max(0.0, min(start, limit)), max(0.0, min(end, limit))
    if end <= start:
        return None
    return start, end, votes


def _duration_mismatch(segments, downloaded_duration) -> bool:
    for seg in segments:
        try:
            declared = float(seg.get('videoDuration') or 0)
        except (TypeError, ValueError):
            continue
        if declared and abs(declared - downloaded_duration) > DURATION_TOLERANCE_S:
            logger.info(f"SponsorBlock duration {declared} differs from download {downloaded_duration:.1f}; ignoring marks")
            return True
    return False


def to_markers(segments: list[dict], downloaded_duration: float | None) -> tuple[list[dict], str | None]:
    """Map segments to markers; note is 'duration_mismatch' when the response was dropped, 'full' for a full-video label."""
    segments = [seg for seg in segments if isinstance(seg, dict)]
    if downloaded_duration and _duration_mismatch(segments, downloaded_duration):
        return [], 'duration_mismatch'
    note = None
    markers = []
    for seg in segments:
        action = seg.get('actionType') or 'skip'
        if action == 'full':
            note = 'full'
            continue
        category = CATEGORY_MAP.get(seg.get('category'))
        if category is None or action not in ('skip', 'mute'):
            continue
        parsed = _parse_segment(seg, downloaded_duration)
        if parsed is None:
            continue
        start, end, votes = parsed
        locked = bool(seg.get('locked'))
        if votes < 0 and not locked:
            continue
        markers.append({
            'start': start, 'end': end, 'category': category, 'confidence': 1.0,
            'source': 'sponsorblock', 'sb_uuid': seg.get('UUID'), 'sb_action': action,
            'sb_votes': votes, 'sb_locked': locked,
            'reason': f"SponsorBlock {seg.get('category')}", 'sponsor': None,
            'detection_stage': 'sponsorblock', 'was_cut': True,
        })
    markers.sort(key=lambda m: m['start'])
    return markers, note


def uuid_set(markers: list[dict]) -> list[str]:
    return sorted(m['sb_uuid'] for m in markers if m.get('sb_uuid'))
