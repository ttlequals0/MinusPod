"""Fetch upstream podcast:chapters JSON (issue #560 follow-up).

Some feeds publish chapters only as a separate podcast:chapters JSON file
(Podcasting 2.0), not as embedded ID3 CHAP frames. Auto mode's embedded probe
never sees these, so it falls back to AI-generated chapters even though the
publisher's own chapters are available. rss_parser.py captures the file's URL
at feed refresh (episodes.upstream_chapters_url); this module fetches it at
processing time so main_app/processing.py can remap and preserve it the same
way it preserves embedded chapters.
"""
import json
import logging
from typing import Dict, List, Optional

from config import BROWSER_USER_AGENT, HTTP_MAX_REDIRECTS_FEED, HTTP_TIMEOUT_API
from utils.http import safe_url_for_log
from utils.safe_http import URLTrust, read_response_capped, safe_get

logger = logging.getLogger(__name__)

# Chapters JSON files are small text; 1 MB comfortably covers even a
# few-hundred-chapter episode while capping a hostile or misconfigured
# response before it grows unbounded in memory.
MAX_UPSTREAM_CHAPTERS_BYTES = 1024 * 1024


def fetch_upstream_chapters(url: str) -> Optional[List[Dict]]:
    """Fetch and parse a podcast:chapters JSON file.

    Returns None for ANY failure: network error, size-cap trip, JSON parse
    failure, or a payload that is not a dict with a 'chapters' list. None
    means "unknown", not "this episode has no chapters", mirroring
    embedded_chapters.probe_chapters' None-vs-[] contract so callers can tell
    an unreachable or malformed remote file apart from a genuinely empty one
    and fall back to generation instead of skipping the chapter step.

    Returns [] only when the fetch and parse both succeed and the file's
    'chapters' list is empty.

    Each kept entry needs a numeric startTime >= 0; entries failing that are
    dropped rather than failing the whole fetch. title/img/url are carried
    through unchanged when present. A missing title is left absent here;
    the processing step applies the "Chapter N" fallback once it knows each
    chapter's final position in the remapped list.
    """
    if not url:
        return None
    try:
        response = safe_get(
            url,
            trust=URLTrust.FEED_CONTENT,
            max_redirects=HTTP_MAX_REDIRECTS_FEED,
            timeout=HTTP_TIMEOUT_API,
            stream=True,
            headers={
                'User-Agent': BROWSER_USER_AGENT,
                'Accept': 'application/json',
            },
        )
        try:
            response.raise_for_status()
            body = read_response_capped(response, MAX_UPSTREAM_CHAPTERS_BYTES)
        finally:
            response.close()
    except Exception as e:
        logger.warning(
            f"Upstream chapters fetch failed for {safe_url_for_log(url)}: {e}")
        return None

    try:
        payload = json.loads(body)
    except Exception as e:
        logger.warning(
            f"Upstream chapters JSON parse failed for {safe_url_for_log(url)}: {e}")
        return None

    if not isinstance(payload, dict) or not isinstance(payload.get('chapters'), list):
        logger.warning(
            f"Upstream chapters payload missing a 'chapters' list: "
            f"{safe_url_for_log(url)}")
        return None

    chapters = []
    for ch in payload['chapters']:
        if not isinstance(ch, dict):
            continue
        start = ch.get('startTime')
        if (not isinstance(start, (int, float)) or isinstance(start, bool)
                or start < 0):
            continue
        entry = {'startTime': start}
        if isinstance(ch.get('title'), str):
            entry['title'] = ch['title']
        # img/url come from an untrusted file and end up in the served
        # chapters JSON; keep only http(s) values.
        for key in ('img', 'url'):
            val = ch.get(key)
            if isinstance(val, str) and val.startswith(('http://', 'https://')):
                entry[key] = val
        chapters.append(entry)
    return chapters
