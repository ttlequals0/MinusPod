"""RSS feed fetching with local file cache.

Cache layout: ~/.cache/minuspod-cuebench/<sha1(rss_url)[:12]>/<sha1(enclosure_url)>.<ext>
500 MB per-enclosure cap; files already present are reused without re-downloading.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlparse

import feedparser
import requests

from config import APP_USER_AGENT, BROWSER_USER_AGENT

logger = logging.getLogger("cuebench.feeds")

_CACHE_ROOT = Path.home() / ".cache" / "minuspod-cuebench"
_MAX_BYTES = 500 * 1024 * 1024  # 500 MB


def cache_dir_for(rss_url: str) -> Path:
    key = hashlib.sha1(rss_url.encode()).hexdigest()[:12]
    return _CACHE_ROOT / key


def fetch(
    rss_url: str,
    max_episodes: int = 5,
    audio_files: Optional[List[Path]] = None,
) -> List[Path]:
    """Return local paths to episode audio.

    If *audio_files* is provided those paths are used directly (no RSS fetch).
    Otherwise the RSS feed is parsed and up to *max_episodes* enclosures are
    downloaded to the cache. Files already cached are not re-downloaded.
    """
    if audio_files:
        missing = [p for p in audio_files if not Path(p).exists()]
        if missing:
            raise FileNotFoundError(f"audio file(s) not found: {missing}")
        return [Path(p) for p in audio_files]

    return _fetch_from_rss(rss_url, max_episodes)


def _fetch_from_rss(rss_url: str, max_episodes: int) -> List[Path]:
    logger.info("parsing RSS: %s", rss_url)
    feed = feedparser.parse(rss_url, request_headers={'User-Agent': APP_USER_AGENT})
    if feed.bozo and not feed.entries:
        raise RuntimeError(f"could not parse feed: {rss_url}")

    enclosures = []
    for entry in feed.entries:
        for enc in getattr(entry, "enclosures", []):
            url = enc.get("href") or enc.get("url")
            if url:
                enclosures.append(url)
        if len(enclosures) >= max_episodes:
            break
    enclosures = enclosures[:max_episodes]
    if not enclosures:
        raise RuntimeError(f"no audio enclosures found in feed: {rss_url}")

    feed_cache = cache_dir_for(rss_url)
    feed_cache.mkdir(parents=True, exist_ok=True)

    paths = []
    for url in enclosures:
        path = _download(url, feed_cache)
        if path:
            paths.append(path)
    return paths


def _download(url: str, dest_dir: Path) -> Optional[Path]:
    parsed = urlparse(url)
    ext = Path(parsed.path).suffix or ".mp3"
    key = hashlib.sha1(url.encode()).hexdigest()
    local = dest_dir / f"{key}{ext}"

    if local.exists():
        logger.info("cache hit: %s", local.name)
        return local

    logger.info("downloading: %s", url)
    try:
        with requests.get(
            url,
            stream=True,
            timeout=60,
            headers={'User-Agent': BROWSER_USER_AGENT},
        ) as resp:
            resp.raise_for_status()
            written = 0
            with open(local, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=65536):
                    written += len(chunk)
                    if written > _MAX_BYTES:
                        logger.warning(
                            "skipping %s: exceeds 500 MB cap at %d bytes", url, written
                        )
                        local.unlink(missing_ok=True)
                        return None
                    fh.write(chunk)
    except Exception as e:
        logger.warning("download failed for %s: %s", url, e)
        local.unlink(missing_ok=True)
        return None

    logger.info("saved %s (%.1f MB)", local.name, local.stat().st_size / 1e6)
    return local
