"""Record provider overrides and artwork from a live instance (GET only) into demo fixtures."""
import argparse
import hashlib
import io
import json
import re
import sys
from datetime import date, datetime, timedelta
from http.cookiejar import MozillaCookieJar
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlsplit

import requests
from PIL import Image

from scripts.demo.leak_check import check_text, compile_patterns, report_hits

API_PREFIX = '/api/v1'
SLOTS = ('primary', 'secondary', 'failover')
QUEUE_KEY = '/episodes/processing?limit=1000&offset=0'
LLM_PLACEHOLDER = 'https://llm.example.com'
TIMEOUT = 60
MAX_SIDE = 600
JPEG_QUALITY = 82
KEY_PARAM = re.compile(r'([?&]key=)[^&"\'\s]+')
# bytes JavaScript URLSearchParams leaves unencoded
UNRESERVED = frozenset(b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789*._-')


class Abort(Exception):
    def __init__(self, message: str, code: int):
        super().__init__(message)
        self.code = code


def form_encode(text: str) -> str:
    out = []
    for b in text.encode('utf-8'):
        if b in UNRESERVED:
            out.append(chr(b))
        elif b == 0x20:
            out.append('+')
        else:
            out.append(f'%{b:02X}')
    return ''.join(out)


def fixture_key(url: str) -> str:
    """Port of fixtureKey in frontend/src/demo/fixtures.ts."""
    parts = urlsplit(url)
    path = parts.path[len(API_PREFIX):] if parts.path.startswith(API_PREFIX) else parts.path
    pairs = sorted(parse_qsl(parts.query, keep_blank_values=True))
    qs = '&'.join(f'{form_encode(k)}={form_encode(v)}' for k, v in pairs)
    return path + (f'?{qs}' if qs else '')


def file_for(key: str) -> str:
    return hashlib.sha1(key.encode('utf-8')).hexdigest() + '.json'


def override_targets(providers: dict) -> list[str]:
    targets = ['/settings/providers']
    for provider, value in providers.items():
        if isinstance(value, dict):
            targets += [f'/settings/models?provider={provider}&slot={slot}' for slot in SLOTS]
    return targets + ['/settings/whisper-models', '/settings/whisper/capacity', QUEUE_KEY]


def scrub_providers(body):
    if isinstance(body, dict):
        return {
            k: LLM_PLACEHOLDER if k == 'baseUrl' and v else scrub_providers(v)
            for k, v in body.items()
        }
    if isinstance(body, list):
        return [scrub_providers(v) for v in body]
    return body


def scrub_body(body):
    return json.loads(KEY_PARAM.sub(r'\1demo', json.dumps(body)))


def parse_day(value) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).date()
    except ValueError:
        return None


def episode_artwork_targets(episodes: list[dict], since_days: int, today: date) -> list[dict]:
    cutoff = today - timedelta(days=since_days)
    keep = []
    for ep in episodes:
        if not ep.get('artworkUrl'):
            continue
        day = parse_day(ep.get('published')) or parse_day(ep.get('createdAt'))
        if day and day >= cutoff:
            keep.append(ep)
    return keep


def to_jpeg(data: bytes) -> bytes:
    img = Image.open(io.BytesIO(data)).convert('RGB')
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    out = io.BytesIO()
    img.save(out, 'JPEG', quality=JPEG_QUALITY)
    return out.getvalue()


def synthesize_queue(history: list[dict], excluded: set[str], now: datetime) -> list[dict]:
    stamp = now.strftime('%Y-%m-%dT%H:%M:%SZ')
    entries = [h for h in history if h.get('podcastSlug') not in excluded][:3]
    rows = []
    for pos, (entry, stage) in enumerate(zip(entries, ('queued', 'transcribing', 'completed'), strict=False), 1):
        rows.append({
            'episodeId': entry['episodeId'],
            'slug': entry['podcastSlug'],
            'title': entry['episodeTitle'],
            'podcast': entry['podcastTitle'],
            'startedAt': None if stage == 'queued' else stamp,
            'stage': stage,
            'queuedAt': stamp if stage == 'queued' else None,
            'queuePosition': pos if stage == 'queued' else None,
            'queueTotal': len(entries),
            'priority': None,
        })
    return rows


def client(base_url: str, cookies: Path):
    jar = MozillaCookieJar(str(cookies))
    jar.load(ignore_discard=True, ignore_expires=True)
    session = requests.Session()
    session.cookies = jar
    root = base_url.rstrip('/') + API_PREFIX

    def get(path: str) -> requests.Response:
        resp = session.get(root + path, timeout=TIMEOUT)
        if resp.status_code == 401:
            raise Abort('401 from the instance: refresh the cookie in cookies.txt', 1)
        return resp
    return get


def get_json(get, path: str):
    resp = get(path)
    if not resp.ok:
        raise Abort(f'GET {path} returned {resp.status_code}', 2)
    return resp.json()


def run(args) -> int:
    get = client(args.base_url, args.cookies)
    hosts = [h.strip() for h in args.hosts.split(',') if h.strip()]
    patterns = compile_patterns(hosts)
    excluded = set(json.loads(args.exclusions.read_text())['slugs']) if args.exclusions else set()
    out: Path = args.out

    def guard(text: str, source: str) -> None:
        hits = check_text(text, source, hosts, patterns)
        if hits:
            report_hits(hits)
            raise Abort(f'leak check hit in {source}', 2)

    index = {}

    def record(key: str, status: int, body) -> None:
        body = scrub_body(scrub_providers(body) if key == '/settings/providers' else body)
        text = json.dumps({'status': status, 'body': body})
        guard(text, key.split('?')[0])
        name = file_for(key)
        (out / 'overrides').mkdir(parents=True, exist_ok=True)
        (out / 'overrides' / name).write_text(text)
        index[key] = name
        print(f'recorded {key.split("?")[0]} ({status})')

    providers = get_json(get, '/settings/providers')
    record('/settings/providers', 200, providers)
    for key in override_targets(providers)[1:]:
        if key == QUEUE_KEY:
            continue
        resp = get(key)
        if resp.status_code >= 500:
            print(f'skipped {key.split("?")[0]} ({resp.status_code})')
            continue
        record(key, resp.status_code, resp.json())

    queue = get(QUEUE_KEY)
    queue_body = queue.json() if queue.ok else []
    if not queue_body and args.synthesize_queue:
        history = get_json(get, '/history?limit=3').get('history', [])
        queue_body = synthesize_queue(history, excluded, datetime.now())
    record(QUEUE_KEY, 200, queue_body)
    (out / 'overrides' / 'index.json').write_text(json.dumps(index, indent=2, sort_keys=True))

    feeds = [f for f in get_json(get, '/feeds?includeLatestEpisodes=false')['feeds']
             if f['slug'] not in excluded]
    for feed in feeds:
        slug = feed['slug']
        resp = get(f'/feeds/{slug}/artwork')
        if not resp.ok:
            raise Abort(f'feed artwork for {slug} returned {resp.status_code}', 2)
        save_image(out / 'artwork' / 'feeds' / f'{slug}.jpg', resp.content)
        episodes = get_json(get, f'/feeds/{slug}/episodes?limit=500&offset=0&status=all')['episodes']
        for ep in episode_artwork_targets(episodes, args.since_days, date.today()):
            fetch_episode_artwork(get, out, slug, ep['id'])
    return 0


def fetch_episode_artwork(get, out: Path, slug: str, episode_id: str) -> None:
    resp = get(f'/feeds/{slug}/episodes/{quote(episode_id, safe="")}/artwork')
    if not resp.ok:
        print(f'skipped episode artwork in {slug} ({resp.status_code})')
        return
    try:
        save_image(out / 'artwork' / 'episodes' / slug / f'{quote(episode_id, safe="")}.jpg', resp.content)
    except OSError:
        print(f'skipped episode artwork in {slug} (unreadable image)')


def save_image(path: Path, data: bytes) -> None:
    jpeg = to_jpeg(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(jpeg)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-url', required=True)
    p.add_argument('--cookies', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--hosts', default='')
    p.add_argument('--exclusions', type=Path)
    p.add_argument('--since-days', type=int, default=30)
    p.add_argument('--synthesize-queue', action='store_true')
    try:
        return run(p.parse_args(argv))
    except Abort as e:
        print(str(e), file=sys.stderr)
        return e.code
    except requests.RequestException as e:
        print(f'request failed: {type(e).__name__}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
