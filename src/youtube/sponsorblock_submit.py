"""Build and send human-reviewed ad segments to SponsorBlock; submission is always manual."""
import requests

from config import is_known_ad, is_pending_review, normalize_segment_category
from utils.app_version import APP_VERSION
from utils.safe_http import URLTrust, safe_post
from utils.time import overlap_ratio, utc_now_iso
from youtube.sponsorblock import API

SUBMIT_CATEGORIES = {'sponsor': 'sponsor', 'self_promo': 'selfpromo'}
MATCH_MIN_OVERLAP = 0.5
_OUTCOMES = {200: 'submitted', 409: 'duplicate', 403: 'rejected', 429: 'rate_limited'}
# A segment with one of these records is not offered again; other outcomes may be retried.
FINAL_STATUSES = frozenset({'submitted', 'duplicate'})
_TIMEOUT_S = 15


def eligible_markers(markers: list[dict]) -> list[dict]:
    """Cut sponsor and self promotion markers with evidence beyond the LLM, in time order."""
    out = [m for m in markers
           if m.get('was_cut') and not is_pending_review(m)
           and m.get('source') != 'sponsorblock'
           and normalize_segment_category(m.get('category')) in SUBMIT_CATEGORIES
           and is_known_ad(m)
           and m.get('end', 0) > m.get('start', 0)]
    return sorted(out, key=lambda m: m['start'])


def _overlaps(record: dict, start: float, end: float) -> bool:
    return overlap_ratio(record.get('start', 0), record.get('end', 0), start, end) >= MATCH_MIN_OVERLAP


def find_submission(records: list[dict], marker: dict) -> dict | None:
    """The latest record overlapping the marker; a final one wins over a retried failure."""
    matches = [r for r in records if _overlaps(r, marker['start'], marker['end'])]
    final = [r for r in matches if r.get('status') in FINAL_STATUSES]
    return (final or matches or [None])[-1]


def build_payload(video_id: str, user_id: str, markers: list[dict]) -> dict:
    return {
        'videoID': video_id,
        'userID': user_id,
        'userAgent': f'MinusPod/{APP_VERSION}',
        'segments': [{
            'segment': [round(m['start'], 3), round(m['end'], 3)],
            'category': SUBMIT_CATEGORIES[normalize_segment_category(m.get('category'))],
            'actionType': 'skip',
        } for m in markers],
    }


def submit(payload: dict, *, timeout: float = _TIMEOUT_S) -> tuple[str, str, list[dict]]:
    """POST the payload; returns (status, detail, created segments) and never raises on HTTP or network errors."""
    try:
        resp = safe_post(API, URLTrust.OPERATOR_CONFIGURED, timeout=timeout, json=payload,
                         headers={'X-CLIENT-NAME': payload['userAgent'], 'User-Agent': payload['userAgent']})
    except (requests.RequestException, OSError) as exc:
        return 'error', f'request failed: {type(exc).__name__}', []
    status = _OUTCOMES.get(resp.status_code)
    if status == 'submitted':
        try:
            body = resp.json()
        except ValueError:
            body = []
        return status, '', [s for s in body if isinstance(s, dict)] if isinstance(body, list) else []
    detail = (resp.text or '')[:200]
    return status or 'error', detail if status else f'HTTP {resp.status_code}: {detail}', []


def build_records(markers: list[dict], status: str, detail: str, segments: list[dict]) -> list[dict]:
    """One recorded outcome per marker, carrying the server UUID when one came back."""
    at = utc_now_iso()
    records = [{'start': m['start'], 'end': m['end'],
                'category': SUBMIT_CATEGORIES[normalize_segment_category(m.get('category'))],
                'status': status, 'at': at} for m in markers]
    if status in ('rejected', 'error'):
        for record in records:
            record['detail'] = detail
    for record in records:
        # The server echoes each segment's span; pair by span, never by position.
        hit = next((seg for seg in segments if _span_close(seg.get('segment'), record)), None)
        record['uuid'] = hit.get('UUID') if hit else None
    return records


def _span_close(span, record) -> bool:
    try:
        return abs(float(span[0]) - record['start']) < 0.5 and abs(float(span[1]) - record['end']) < 0.5
    except (TypeError, ValueError, IndexError):
        return False


def merge_records(records: list[dict], results: list[dict]) -> list[dict]:
    """Replace any earlier record overlapping a new result."""
    kept = [r for r in records if not any(_overlaps(r, n['start'], n['end']) for n in results)]
    return kept + results
