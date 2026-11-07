"""Record provider overrides and artwork from a live instance (GET only) into demo fixtures.

An exit code 2 leaves a partial <out> that must not be published.
"""
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
from PIL import Image, ImageOps

from config import (
    PROVIDER_ANTHROPIC, PROVIDER_OLLAMA, PROVIDER_OPENAI_COMPATIBLE, PROVIDER_OPENROUTER,
    SYSTEMONE_PROVIDERS,
)
from scripts.demo.leak_check import check_text, compile_patterns, report_hits

API_PREFIX = '/api/v1'
# mirrors VALID_LLM_PROVIDERS in src/api/settings/common.py (importing it pulls in the Flask app)
VALID_LLM_PROVIDERS = (
    PROVIDER_ANTHROPIC, PROVIDER_OPENROUTER, PROVIDER_OPENAI_COMPATIBLE, PROVIDER_OLLAMA,
    *SYSTEMONE_PROVIDERS,
)
SLUG_OK = re.compile(r'^[A-Za-z0-9._-]+$')
SLOTS = ('primary', 'secondary', 'failover')
QUEUE_KEY = '/episodes/processing?limit=1000&offset=0'
LLM_PLACEHOLDER = 'https://llm.example.com'
TIMEOUT = 60
MAX_SIDE = 600
JPEG_QUALITY = 82
KEY_PARAM = re.compile(r'([?&]key=)[^&"\'\s\\]+')
# bytes JavaScript URLSearchParams leaves unencoded
UNRESERVED = frozenset(b'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789*._-')
# bytes encodeURIComponent leaves unencoded
URI_COMPONENT_OK = frozenset(UNRESERVED | set(b"!~'()"))


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


def encode_uri_component(text: str) -> str:
    return ''.join(chr(b) if b in URI_COMPONENT_OK else f'%{b:02X}' for b in text.encode('utf-8'))


def override_targets() -> list[str]:
    targets = ['/settings/providers']
    targets += [f'/settings/models?provider={p}&slot={slot}' for p in VALID_LLM_PROVIDERS for slot in SLOTS]
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
    if isinstance(body, dict):
        return {k: scrub_body(v) for k, v in body.items()}
    if isinstance(body, list):
        return [scrub_body(v) for v in body]
    if isinstance(body, str):
        return KEY_PARAM.sub(r'\1demo', body)
    return body


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


def round_numbers(value, digits: int = 3):
    if isinstance(value, float):
        return round(value, digits)
    if isinstance(value, list):
        return [round_numbers(v, digits) for v in value]
    if isinstance(value, dict):
        return {k: round_numbers(v, digits) for k, v in value.items()}
    return value


def to_jpeg(data: bytes) -> bytes:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    if img.mode in ('RGBA', 'LA', 'P'):
        rgba = img.convert('RGBA')
        img = Image.new('RGB', rgba.size, 'white')
        img.paste(rgba, mask=rgba.getchannel('A'))
    img = img.convert('RGB')
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
    return parse_json(resp, path)


def parse_json(resp, path: str):
    try:
        return resp.json()
    except ValueError:
        raise Abort(f'GET {path} did not return JSON: refresh the cookie in cookies.txt', 2) from None


def run(args, get=None) -> int:
    get = get or client(args.base_url, args.cookies)
    hosts = [h.strip() for h in args.hosts.split(',') if h.strip()]
    patterns = compile_patterns(hosts)
    excluded = set(json.loads(args.exclusions.read_text())['slugs']) if args.exclusions else set()
    out: Path = args.out
    index = {}

    def record(key: str, status: int, body) -> None:
        body = scrub_body(scrub_providers(body) if key == '/settings/providers' else body)
        text = json.dumps({'status': status, 'body': body})
        source = key.split('?')[0]
        hits = check_text(text, source, hosts, patterns)
        if hits:
            report_hits(hits)
            raise Abort(f'leak check hit in {source}', 2)
        name = file_for(key)
        (out / 'overrides').mkdir(parents=True, exist_ok=True)
        (out / 'overrides' / name).write_text(text)
        index[key] = name
        (out / 'overrides' / 'index.json').write_text(json.dumps(index, indent=2, sort_keys=True))
        print(f'recorded {source} ({status})')

    providers = get_json(get, '/settings/providers')
    record('/settings/providers', 200, providers)
    for key in override_targets()[1:]:
        if key == QUEUE_KEY:
            continue
        resp = get(key)
        if resp.status_code >= 500:
            print(f'skipped {key.split("?")[0]} ({resp.status_code})')
            continue
        record(key, resp.status_code, parse_json(resp, key.split('?')[0]))

    queue_body = [r for r in get_json(get, QUEUE_KEY) if r.get('slug') not in excluded]
    if not queue_body and args.synthesize_queue:
        history = get_json(get, '/history?limit=30').get('history', [])
        queue_body = synthesize_queue(history, excluded, datetime.now())
    record(QUEUE_KEY, 200, queue_body)

    feeds = [f for f in get_json(get, '/feeds?includeLatestEpisodes=false')['feeds']
             if f['slug'] not in excluded]
    for feed in feeds:
        slug = feed['slug']
        if not SLUG_OK.match(slug):
            print('skipped a feed with an unsafe slug')
            continue
        resp = get(f'/feeds/{slug}/artwork')
        if not resp.ok:
            raise Abort(f'feed artwork for {slug} returned {resp.status_code}', 2)
        try:
            save_image(out / 'artwork' / 'feeds' / f'{slug}.jpg', resp.content)
        except Exception as e:
            raise Abort(f'feed artwork for {slug} is unreadable ({type(e).__name__})', 2) from None
        episodes = get_json(get, f'/feeds/{slug}/episodes?limit=500&offset=0&status=all')['episodes']
        for ep in episode_artwork_targets(episodes, args.since_days, date.today()):
            fetch_episode_artwork(get, out, slug, ep['id'])
            if args.peaks and ep.get('status') in ('processed', 'completed'):
                fetch_peaks(get, record, slug, ep['id'])
    return 0


def fetch_peaks(get, record, slug: str, episode_id: str) -> None:
    key = f'/feeds/{slug}/episodes/{quote(episode_id, safe="")}/peaks?resolution_ms=250'
    resp = get(key)
    if resp.status_code == 404:
        return
    if resp.status_code != 200:
        print(f'skipped peaks {slug} ({resp.status_code})')
        return
    record(key, 200, round_numbers(parse_json(resp, f'/feeds/{slug}/episodes/<id>/peaks')))


def fetch_episode_artwork(get, out: Path, slug: str, episode_id: str) -> None:
    name = encode_uri_component(episode_id)
    resp = get(f'/feeds/{slug}/episodes/{quote(episode_id, safe="")}/artwork')
    if not resp.ok:
        print(f'skipped episode artwork {slug}/{name} ({resp.status_code})')
        return
    try:
        save_image(out / 'artwork' / 'episodes' / slug / f'{name}.jpg', resp.content)
    except Exception:
        print(f'skipped episode artwork {slug}/{name} (unreadable image)')


def save_image(path: Path, data: bytes) -> None:
    jpeg = to_jpeg(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(jpeg)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--base-url', required=True)
    p.add_argument('--cookies', type=Path, required=True)
    p.add_argument('--out', type=Path, required=True)
    p.add_argument('--hosts', required=True)
    p.add_argument('--exclusions', type=Path)
    p.add_argument('--since-days', type=int, default=30)
    p.add_argument('--peaks', action=argparse.BooleanOptionalAction, default=True)
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
