"""Fetch upstream podcast:chapters JSON (#560).

Some feeds publish chapters only as a podcast:chapters JSON file (Podcasting 2.0),
which the embedded ID3 probe never sees. rss_parser.py stores its URL at feed
refresh (episodes.upstream_chapters_url); this module fetches it at processing
time so main_app/pipeline/publish.py can remap and keep it like embedded chapters.
"""
import json
import logging

from user_agent import download_user_agent
from utils.http import safe_url_for_log
from utils.safe_http import get_feed_content_with_agents, read_response_capped

logger = logging.getLogger(__name__)

# Chapters JSON files are small text; 1 MB comfortably covers even a
# few-hundred-chapter episode while capping a hostile or misconfigured
# response before it grows unbounded in memory.
MAX_UPSTREAM_CHAPTERS_BYTES = 1024 * 1024
MAX_UPSTREAM_CHAPTER_COUNT = 1000


def chapters_document(chapters: list) -> dict:
    """Wrap chapters in the served chapters JSON shape, filling missing titles."""
    return {'version': '1.2.0',
            'chapters': [{**ch, 'title': ch.get('title') or f"Chapter {i + 1}"}
                         for i, ch in enumerate(chapters)]}


def fetch_upstream_chapters(url: str, user_agent: str | None = None, *,
                            fallback_user_agent: str | None = None) -> list[dict] | None:
    """Fetch and parse a podcast:chapters JSON file.

    Returns None on any failure (network, size cap, bad JSON, payload not a dict
    with a 'chapters' list). None means "unknown", mirroring
    embedded_chapters.probe_chapters' None-vs-[] contract, so callers fall back to
    generation. Returns [] only when the file parses and its 'chapters' is empty.

    Entries need a numeric startTime >= 0 or are dropped. title/img/url pass
    through; a missing title stays absent (the "Chapter N" fallback is applied
    later, once the final position is known).
    """
    if not url:
        return None
    try:
        agents = list(dict.fromkeys(
            ua for ua in (user_agent or download_user_agent(), fallback_user_agent) if ua))
        response = get_feed_content_with_agents(
            url, agents, 'application/json')
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
        if len(chapters) >= MAX_UPSTREAM_CHAPTER_COUNT:
            logger.warning(f"Upstream chapters truncated at {MAX_UPSTREAM_CHAPTER_COUNT}: "
                           f"{safe_url_for_log(url)}")
            break
    return chapters
