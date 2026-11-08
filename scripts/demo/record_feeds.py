"""Record each snapshot feed's rendered RSS from a live instance (GET only) as the demo app's RSS cache.

The demo pipeline runs the app on a network without egress and the snapshot carries no cached RSS, so
GET /<slug> would try to fetch every upstream feed and answer 503. This script fetches the rendered
feeds from the live instance, rewrites the instance URLs to the demo host, drops the feed auth key and
the episodes the snapshot does not hold, scrubs emails, runs the leak check and writes
podcasts/<slug>/modified-rss.xml. Unpacked into the app's data directory, serve_rss treats the files
as its own cache and serves them without a refresh.
"""
import argparse
import re
import sqlite3
import sys
import tarfile
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import requests

from scripts.demo.leak_check import check_text, compile_patterns, load_exclusions, parse_hosts, report_hits
from scripts.demo.scrub import replace_emails, replace_hosts

DEMO_HOST = 'demo.minuspod.app'
TIMEOUT = 60
ITEM = re.compile(r'<item>.*?</item>\s*', re.S)
EPISODE_URL = re.compile(r'/episodes/([A-Za-z0-9._-]+)/([A-Za-z0-9]+)(?:-v\d+)?\.mp3')
RENDER_VERSION = re.compile(r'<!-- minuspod-rss-render-version:\d+ -->')


class Abort(Exception):
    def __init__(self, message: str, code: int):
        super().__init__(message)
        self.code = code


def snapshot_episodes(db: Path) -> tuple[list[str], set[tuple[str, str]]]:
    """Feed slugs in the snapshot and every (slug, episode id) it holds."""
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    try:
        slugs = [row[0] for row in con.execute('SELECT slug FROM podcasts ORDER BY slug')]
        known = set(con.execute(
            'SELECT p.slug, e.episode_id FROM episodes e JOIN podcasts p ON p.id = e.podcast_id'))
    finally:
        con.close()
    return slugs, known


def strip_feed_keys(text: str) -> str:
    """Remove ?key= query values and the keyed cover token; the demo runs with feed auth off."""
    text = re.sub(r'\?key=[0-9a-f]{64}(?:&amp;|&)', '?', text)
    text = re.sub(r'(?:\?|&amp;|&)key=[0-9a-f]{64}', '', text)
    return re.sub(r'(/cover-minuspod(?:-[0-9a-f]{8})?)-[0-9a-f]{64}\.jpg', r'\1.jpg', text)


def keep_snapshot_items(text: str, known: set[tuple[str, str]]) -> tuple[str, int]:
    """Drop items whose enclosure points at an episode the snapshot does not have."""
    dropped = 0

    def item(m: re.Match) -> str:
        nonlocal dropped
        refs = EPISODE_URL.findall(m.group(0))
        if all(ref in known for ref in refs):
            return m.group(0)
        dropped += 1
        return ''

    return ITEM.sub(item, text), dropped


def record_feed(text: str, hosts: list[str], known: set[tuple[str, str]]) -> tuple[str, int]:
    if not RENDER_VERSION.search(text):
        raise Abort('feed has no render-version marker; the app would refresh it', 2)
    text = replace_hosts(text, hosts, DEMO_HOST)
    text = strip_feed_keys(text)
    text, dropped = keep_snapshot_items(text, known)
    return replace_emails(text), dropped


def feed_key(feeds: list[dict]) -> str | None:
    for feed in feeds:
        key = parse_qs(urlsplit(feed.get('feedUrl') or '').query).get('key')
        if key:
            return key[0]
    return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument('--base-url', required=True)
    p.add_argument('--cookies', type=Path, required=True)
    p.add_argument('--sqlite', type=Path, required=True, help='the scrubbed snapshot database')
    p.add_argument('--out', type=Path, required=True, help='directory that receives podcasts/<slug>/modified-rss.xml')
    p.add_argument('--tar', type=Path, help='also write the directory as this tar (podcasts/ at the root)')
    p.add_argument('--hosts', required=True)
    p.add_argument('--exclusions', type=Path, required=True)
    args = p.parse_args(argv)
    try:
        hosts = parse_hosts(args.hosts)
        excluded = load_exclusions(args.exclusions)
        if urlsplit(args.base_url).hostname not in hosts:
            raise Abort('--hosts must include the --base-url host', 1)
        slugs, known = snapshot_episodes(args.sqlite)
        leaked = excluded & set(slugs)
        if leaked:
            raise Abort(f'{len(leaked)} excluded slug(s) are in the snapshot database', 1)

        jar = MozillaCookieJar(str(args.cookies))
        jar.load(ignore_discard=True, ignore_expires=True)
        session = requests.Session()
        session.cookies = jar
        base = args.base_url.rstrip('/')
        resp = session.get(f'{base}/api/v1/feeds?includeLatestEpisodes=false', timeout=TIMEOUT)
        if resp.status_code != 200:
            raise Abort(f'GET /api/v1/feeds returned {resp.status_code}: refresh the cookie', 1)
        key = feed_key(resp.json()['feeds'])

        patterns = compile_patterns(hosts, sorted(excluded))
        kept = dropped = 0
        hits = []
        texts = {}
        for slug in slugs:
            resp = session.get(f'{base}/{slug}', params={'key': key} if key else None, timeout=TIMEOUT)
            if resp.status_code != 200 or '<rss' not in resp.text[:2000]:
                raise Abort(f'GET /{slug} returned {resp.status_code} without an RSS document', 2)
            text, gone = record_feed(resp.text, hosts, known)
            dropped += gone
            kept += len(ITEM.findall(text))
            hits += check_text(text, f'{slug}/modified-rss.xml', hosts, patterns)
            texts[slug] = text
        if hits:
            report_hits(hits, hosts=hosts, slugs=sorted(excluded))
            raise Abort(f'{len(hits)} leak check hit(s); nothing written or uploaded', 2)
        for slug, text in texts.items():
            dest = args.out / 'podcasts' / slug / 'modified-rss.xml'
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding='utf-8')
        if args.tar:
            with tarfile.open(args.tar, 'w') as tar:
                tar.add(args.out / 'podcasts', arcname='podcasts')
        print(f'feeds: {len(slugs)} recorded, {kept} items kept, {dropped} items not in the snapshot dropped')
        return 0
    except Abort as e:
        print(str(e), file=sys.stderr)
        return e.code


if __name__ == '__main__':
    sys.exit(main())
