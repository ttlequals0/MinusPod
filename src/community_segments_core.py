"""Pure core of show-bound segments: normalisation, keys, stable cores, entry bodies, matcher; no DB or network.
Shingle hashes are keyed by the show's guid, so only a receiver that has the show can match an entry."""
from __future__ import annotations

import functools
import hashlib
import hmac
import re
import unicodedata
from dataclasses import dataclass

from text_pattern_matcher import _align_segment_words
from tools.show_segment_validator import FORMAT_VERSION, HASH_BITS, KDF, KIND, LONG_SIZES, SHINGLE_K, SHORT_SIZES
from utils.short_asks import SHORT_ASK_EDGE_CAP_S, SHORT_ASK_MAX_S, SHORT_ASK_MIN_S, _edges

PROMO_FAMILY = frozenset({'interaction', 'self_promo', 'cross_promo'})
SHOW_FAMILY = frozenset({'intro', 'outro', 'recap'})
# scrypt needs 128*r*(n+p+2) bytes (about 33.6 MB here), just above OpenSSL's 32 MiB default.
KDF_MAXMEM = 64 * 1024 * 1024
BUCKET_BITS = 10
POSITION_WINDOW_S = 120


@dataclass(frozen=True)
class Shape:
    """Size, stability and match thresholds of one entry shape."""
    core_min: int
    core_max: int
    anchors: int
    stable_share: float
    c_body: float
    head: float
    tail: float
    both_anchors: bool


LONG = Shape(*LONG_SIZES, stable_share=0.5, c_body=0.55, head=0.5, tail=0.5, both_anchors=False)
# Short asks are near-exact or nothing, like the short-ask show patterns (score >= 90).
SHORT = Shape(*SHORT_SIZES, stable_share=0.8, c_body=0.8, head=0.67, tail=0.67, both_anchors=True)
# Snap cap for long spans; spans under 40 s use the short-ask cap.
EDGE_CAP_S = 4.0
SHORT_SPAN_S = 40.0
ANCHOR_BY_CATEGORY = {'intro': 'start', 'outro': 'end', 'recap': 'break'}


class SegmentKeyError(RuntimeError):
    """The show secret could not be derived; callers must surface it, never treat it as no match."""


def shape_for(short_ask: bool) -> Shape:
    return SHORT if short_ask else LONG


# ---------------------------------------------------------------- normalisation

_EMAIL = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
_URL = re.compile(r'(?:https?://|www\.)\S+', re.I)
_DOMAIN = re.compile(r'\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.[a-z]{2,24}\b(?:/\S*)?', re.I)
_HANDLE = re.compile(r'(?<![\w@])@\w+')
_PHONE = re.compile(r'(?<!\w)\+?\d[\d\s().-]{5,}\d(?!\w)|(?<!\w)\(\d{3}\)\s*\d[\d\s.-]{4,}\d(?!\w)')
_NUMBER = re.compile(r'\d+')
_PLACEHOLDERS = {'zzemailzz': '[email]', 'zzurlzz': '[url]', 'zzhandlezz': '[handle]',
                 'zzphonezz': '[phone]', 'zznumzz': '[num]'}


def normalize_for_segments(text: str | None) -> str:
    """Lowercase NFKC text with every email, URL, domain, handle, phone and number replaced by a placeholder.
    Stricter than strip_pii so no contact detail ever reaches a hash."""
    out = unicodedata.normalize('NFKC', text or '')
    out = _EMAIL.sub(' zzemailzz ', out)
    out = _URL.sub(' zzurlzz ', out)
    out = _DOMAIN.sub(' zzurlzz ', out)
    out = _HANDLE.sub(' zzhandlezz ', out)
    out = _PHONE.sub(' zzphonezz ', out)
    out = _NUMBER.sub(' zznumzz ', out)
    out = re.sub(r'[^\w\s]|_', ' ', out.lower())
    return ' '.join(_PLACEHOLDERS.get(w, w) for w in out.split())


def tokens(text: str | None) -> list[str]:
    return normalize_for_segments(text).split()


def shingles(words: list[str]) -> list[str]:
    return [' '.join(words[i:i + SHINGLE_K]) for i in range(len(words) - SHINGLE_K + 1)]


# ---------------------------------------------------------------- keys and fingerprints

def bucket_for(show_key: str) -> int:
    """The only show-derived value published in the clear: 10 bits of a hash of the key."""
    digest = hashlib.sha256(b'mpss1|bucket|' + show_key.encode()).digest()
    return int.from_bytes(digest[:2], 'big') >> (16 - BUCKET_BITS)


@functools.lru_cache(maxsize=256)
def show_secret(show_key: str) -> bytes:
    """scrypt(show_key) salted with its bucket: about 0.1 s once per key, then cached in memory only."""
    salt = b'mpss1|' + bucket_for(show_key).to_bytes(2, 'big')
    try:
        return hashlib.scrypt(show_key.encode(), salt=salt, n=KDF['n'], r=KDF['r'], p=KDF['p'],
                              dklen=32, maxmem=KDF_MAXMEM)
    except (ValueError, MemoryError) as e:
        raise SegmentKeyError(f'show secret derivation failed: {e}') from e


def fingerprint(secret: bytes, shingle: str) -> str:
    """HMAC-SHA256 of one shingle under the show secret, truncated to 24 bits (6 hex digits)."""
    return hmac.new(secret, shingle.encode(), hashlib.sha256).digest()[:HASH_BITS // 8].hex()


# ---------------------------------------------------------------- stable core and entry body

def stable_core(texts: list[str], short_ask: bool) -> list[str]:
    """Shingles seen in at least two confirmed texts and the shape's share of them, in spoken order."""
    if len(texts) < 2:
        return []
    per_text = [shingles(tokens(t)) for t in texts]
    counts: dict[str, int] = {}
    for sh in per_text:
        for s in set(sh):
            counts[s] = counts.get(s, 0) + 1
    need = max(2, shape_for(short_ask).stable_share * len(texts))
    ordered, seen = [], set()
    for sh in per_text:
        for s in sh:
            if s not in seen and counts[s] >= need:
                seen.add(s)
                ordered.append(s)
    return ordered


def _half_second(value: float) -> float:
    return round(value * 2) / 2


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))]


def entry_body(core: list[str], show_key: str, key_kind: str, *, category: str, short_ask: bool,
               durations: list[float], language: str | None, confirms: int, episodes: int, span_days: int,
               community_id: str | None = None, version: int = 1, submitted_at: str | None = None,
               app_version: str | None = None) -> dict:
    """The published entry: bucket, keyed core hashes and spoken-order anchors, coarse priors and evidence."""
    secret = show_secret(show_key)
    ordered: list[str] = []
    for s in core:
        h = fingerprint(secret, s)
        if h not in ordered:
            ordered.append(h)
    n = shape_for(short_ask).anchors
    return {
        'kind': KIND, 'format_version': FORMAT_VERSION,
        'community_id': community_id, 'version': version,
        'submitted_at': submitted_at, 'submitted_app_version': app_version,
        'category': category, 'short_ask': bool(short_ask), 'source_language': language,
        'bucket': bucket_for(show_key), 'key_kind': key_kind,
        'kdf': dict(KDF), 'shingle_k': SHINGLE_K, 'hash_bits': HASH_BITS,
        'core': sorted(ordered), 'head': ordered[:n], 'tail': ordered[-n:],
        'duration': {'p10': _half_second(_percentile(durations, 0.1)),
                     'p50': _half_second(_percentile(durations, 0.5)),
                     'p90': _half_second(_percentile(durations, 0.9))},
        'position': {'anchor': ANCHOR_BY_CATEGORY.get(category, 'any'), 'window_s': POSITION_WINDOW_S},
        'evidence': {'confirms': min(10, confirms), 'episodes': min(10, episodes),
                     'span_weeks': max(0, span_days) // 7},
    }


# ---------------------------------------------------------------- matching

@dataclass(frozen=True)
class Match:
    start: float
    end: float
    c_body: float
    anchored_start: bool
    anchored_end: bool


def _timed_tokens(segments: list[dict]) -> list[tuple[str, float, float]]:
    """(token, start, end) over the episode: word times where the words align to the text, else spread evenly."""
    out: list[tuple[str, float, float]] = []
    for seg in segments or []:
        toks = tokens(seg.get('text'))
        if not toks:
            continue
        start, end = float(seg.get('start') or 0.0), float(seg.get('end') or 0.0)
        aligned = _align_segment_words(seg) if seg.get('words') else None
        n = len(toks)
        for j, tok in enumerate(toks):
            if aligned:
                _cs, _ce, ws, we = aligned[min(len(aligned) - 1, j * len(aligned) // n)]
                out.append((tok, float(ws), float(we)))
            else:
                step = (end - start) / n
                out.append((tok, start + j * step, start + (j + 1) * step))
    return out


def hashed_shingles(secret: bytes, segments: list[dict]) -> list[tuple[str, float, float]]:
    """(hash, start, end) of every word 3-shingle of the episode under one show secret."""
    timed = _timed_tokens(segments)
    return [(fingerprint(secret, ' '.join(t[0] for t in timed[i:i + SHINGLE_K])),
             timed[i][1], timed[i + SHINGLE_K - 1][2])
            for i in range(len(timed) - SHINGLE_K + 1)]


def _duration_bounds(entry: dict) -> tuple[float, float]:
    d = entry.get('duration') or {}
    lo, hi = 0.7 * float(d.get('p10') or 0.0), 1.3 * float(d.get('p90') or 0.0)
    if entry.get('short_ask'):
        lo, hi = max(lo, SHORT_ASK_MIN_S), min(hi, SHORT_ASK_MAX_S)
    return lo, hi


def _position_ok(entry: dict, start: float, end: float, episode_duration: float | None) -> bool:
    position = entry.get('position') or {}
    window = float(position.get('window_s') or POSITION_WINDOW_S)
    anchor = position.get('anchor')
    if anchor == 'start':
        return start <= window
    if anchor == 'end':
        return not episode_duration or end >= episode_duration - window
    return True  # 'break' and 'any': the receiver has no break positions here, so no prior applies


def _best_window(hashed, positions, core, head, tail, shape, lo, hi):
    """Best (score, i, j, c_body) window starting and ending on core hits, with running hit counts."""
    best = None
    for a, i in enumerate(positions):
        seen: set[str] = set()
        c = h = t = 0
        for j in positions[a:]:
            if hashed[j][2] - hashed[i][1] > hi:
                break
            value = hashed[j][0]
            if value not in seen:
                seen.add(value)
                c += 1  # positions are core hits only
                h += value in head
                t += value in tail
            if hashed[j][2] - hashed[i][1] < lo:
                continue
            c_body = c / len(core)
            h_share = h / len(head) if head else 0.0
            t_share = t / len(tail) if tail else 0.0
            anchors_ok = (h_share >= shape.head and t_share >= shape.tail) if shape.both_anchors else (
                h_share >= shape.head or t_share >= shape.tail)
            if c_body < shape.c_body or not anchors_ok:
                continue
            key = (c_body, -(hashed[j][2] - hashed[i][1]))
            if best is None or key > best[0]:
                best = (key, i, j, c_body)
    return best


def _snap(value: float, candidates: list[float], cap: float) -> float:
    near = [c for c in candidates if abs(c - value) <= cap]
    return min(near, key=lambda c: abs(c - value)) if near else value


def find_matches(entry: dict, secret: bytes, segments: list[dict], episode_duration: float | None,
                 hashed: list[tuple[str, float, float]] | None = None) -> list[Match]:
    """Non-overlapping spans matching the entry, best first; edges come from the receiver's own transcript."""
    core = set(entry.get('core') or [])
    if not core:
        return []
    head, tail = set(entry.get('head') or []), set(entry.get('tail') or [])
    shape = shape_for(bool(entry.get('short_ask')))
    lo, hi = _duration_bounds(entry)
    if hi <= 0 or lo > hi:
        return []
    if hashed is None:
        hashed = hashed_shingles(secret, segments)
    positions = [i for i, (h, _s, _e) in enumerate(hashed) if h in core]
    starts, ends = _edges(segments) if positions else ([], [])
    found: list[Match] = []
    while positions:
        best = _best_window(hashed, positions, core, head, tail, shape, lo, hi)
        if best is None:
            break
        _key, i, j, c = best
        window = [k for k in positions if i <= k <= j]
        head_hits = [k for k in window if hashed[k][0] in head]
        tail_hits = [k for k in window if hashed[k][0] in tail]
        start = hashed[head_hits[0]][1] if head_hits else hashed[i][1]
        end = hashed[tail_hits[-1]][2] if tail_hits else hashed[j][2]
        cap = SHORT_ASK_EDGE_CAP_S if end - start < SHORT_SPAN_S else EDGE_CAP_S
        if head_hits:
            start = _snap(start, starts, cap)
        if tail_hits:
            end = _snap(end, ends, cap)
        if _position_ok(entry, start, end, episode_duration):
            found.append(Match(start, end, round(c, 3), bool(head_hits), bool(tail_hits)))
        positions = [k for k in positions if k < i or k > j]
    return sorted(found, key=lambda m: m.start)
