"""Upstream podcast:transcript fetch, parse and alignment against Whisper.

Publishers that ship an ad-free transcript let us locate dynamically inserted
ads as runs of Whisper words the upstream text lacks. Pure functions, no DB.
"""
import difflib
import html
import json
import logging
import re
from bisect import bisect_left
from dataclasses import dataclass

from config import HTTP_MAX_REDIRECTS_FEED, HTTP_TIMEOUT_API
from text_recurrence import _WORD_RE, SHINGLE_SIZE
from user_agent import download_user_agent
from utils.http import safe_url_for_log
from utils.safe_http import URLTrust, read_response_capped, safe_get

logger = logging.getLogger(__name__)

MAX_UPSTREAM_TRANSCRIPT_BYTES = 5 * 1024 * 1024
FALLBACK_SHINGLE_SIZE = 4
# SequenceMatcher is roughly quadratic; larger anchor-free chunks are re-anchored or left unmatched.
MAX_CHUNK_CELLS = 1_000_000
MAX_NOISE_TOKENS = 3
OFFSET_SAMPLE_TOKENS = 10
OFFSET_TOLERANCE_S = 5.0
OFFSET_TOLERANCE_FRACTION = 0.2
PREVIEW_WORDS = 12

_KIND_MIME = {
    'vtt': 'text/vtt',
    'srt': 'application/srt',
    'json': 'application/json',
    'html': 'text/html',
    'text': 'text/plain',
}
_MIME_KIND = {
    'text/vtt': 'vtt',
    'application/srt': 'srt',
    'application/x-subrip': 'srt',
    'text/srt': 'srt',
    'application/json': 'json',
    'text/html': 'html',
    'text/plain': 'text',
}

_TIME_RE = r'(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})'
_CUE_TIMING_RE = re.compile(_TIME_RE + r'\s*-->\s*' + _TIME_RE)
_SRT_SNIFF_RE = re.compile(r'\d+\s*\r?\n\s*' + _TIME_RE + r'\s*-->')
_HTML_SNIFF_RE = re.compile(r'<(?:html|body|p|div|br|span)\b', re.I)
_TAG_RE = re.compile(r'<[^>]*>')
_HTML_DROP_RE = re.compile(r'<(head|script|style)\b.*?</\1\s*>', re.I | re.S)
_HTML_BREAK_RE = re.compile(r'<(?:br|/?p|/?div|/?li|/?h[1-6]|/?tr)\b[^>]*>', re.I)
_INLINE_TIMESTAMP_RE = re.compile(r'\b\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?\b')
_SPEAKER_RE = re.compile(r"^(?:-\s*)?[A-Z][\w.'-]*(?: [\w.'-]+){0,3}:\s+")
_SPACE_RE = re.compile(r'\s+')


@dataclass(frozen=True)
class UpstreamTranscript:
    cues: list[dict]
    timed: bool
    source_url: str
    mime: str


def fetch_upstream_transcript(url: str, mime: str | None) -> UpstreamTranscript | None:
    """Fetch and parse an upstream transcript; None on any failure."""
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
                'User-Agent': download_user_agent(),
                'Accept': f'{mime}, */*;q=0.5' if mime else '*/*',
            },
        )
        try:
            response.raise_for_status()
            body = read_response_capped(response, MAX_UPSTREAM_TRANSCRIPT_BYTES)
            declared = mime or response.headers.get('Content-Type')
        finally:
            response.close()
    except Exception as e:
        logger.warning(f"Upstream transcript fetch failed for {safe_url_for_log(url)}: {e}")
        return None

    try:
        transcript = parse_transcript(body, declared, url)
    except Exception as e:
        logger.warning(f"Upstream transcript parse failed for {safe_url_for_log(url)}: {e}")
        return None
    if transcript is None:
        logger.warning(f"Upstream transcript has no usable cues: {safe_url_for_log(url)}")
    return transcript


def parse_transcript(body: bytes, mime: str | None, source_url: str) -> UpstreamTranscript | None:
    """Parse VTT, SRT, podcast JSON, HTML or plain text; None when no cue has words."""
    if not body:
        return None
    text = body.decode('utf-8-sig', errors='replace')
    kind = _strong_sniff(text) or _MIME_KIND.get(_base_mime(mime)) or _weak_sniff(text)
    if kind in ('vtt', 'srt'):
        cues = _parse_timed_blocks(text)
    elif kind == 'json':
        cues = _parse_json(text)
    elif kind == 'html':
        cues = _parse_lines(_html_to_text(text))
    else:
        cues = _parse_lines(text)
    cues = [c for c in cues if _WORD_RE.search(c['text'].lower())]
    if not cues:
        return None
    timed = all(c['start'] is not None for c in cues)
    return UpstreamTranscript(cues=cues, timed=timed, source_url=source_url,
                              mime=_KIND_MIME[kind])


def _base_mime(mime: str | None) -> str:
    return (mime or '').split(';', 1)[0].strip().lower()


def _strong_sniff(text: str) -> str | None:
    head = text.lstrip()
    if head.startswith('WEBVTT'):
        return 'vtt'
    if _SRT_SNIFF_RE.match(head):
        return 'srt'
    if head.startswith('{'):
        try:
            payload = json.loads(head)
        except ValueError:
            return None
        if isinstance(payload, dict) and isinstance(payload.get('segments'), list):
            return 'json'
    return None


def _weak_sniff(text: str) -> str:
    return 'html' if _HTML_SNIFF_RE.search(text) else 'text'


def _clean_cue_text(raw: str) -> str:
    lines = []
    for line in raw.splitlines():
        line = html.unescape(_TAG_RE.sub('', line)).strip()
        line = _SPEAKER_RE.sub('', line)
        if line:
            lines.append(line)
    return _SPACE_RE.sub(' ', ' '.join(lines)).strip()


def _seconds(h, m, s, frac) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(frac.ljust(3, '0')) / 1000


def _parse_timed_blocks(text: str) -> list[dict]:
    """VTT and SRT cues: a timing line followed by text lines, blocks split by blank lines."""
    cues = []
    for block in re.split(r'\r?\n\s*\r?\n', text):
        lines = block.strip().splitlines()
        for idx, line in enumerate(lines):
            m = _CUE_TIMING_RE.search(line)
            if m:
                cues.append({'start': _seconds(*m.groups()[:4]),
                             'end': _seconds(*m.groups()[4:]),
                             'text': _clean_cue_text('\n'.join(lines[idx + 1:]))})
                break
    return cues


def _num(value) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_json(text: str) -> list[dict]:
    try:
        payload = json.loads(text)
    except ValueError:
        return []
    if not isinstance(payload, dict) or not isinstance(payload.get('segments'), list):
        return []
    cues = []
    for seg in payload['segments']:
        if isinstance(seg, dict) and isinstance(seg.get('body'), str):
            cues.append({'start': _num(seg.get('startTime')), 'end': _num(seg.get('endTime')),
                         'text': _clean_cue_text(seg['body'])})
    return cues


def _html_to_text(text: str) -> str:
    text = _HTML_DROP_RE.sub(' ', text)
    return _HTML_BREAK_RE.sub('\n', text)


def _parse_lines(text: str) -> list[dict]:
    return [{'start': None, 'end': None,
             'text': _clean_cue_text(_INLINE_TIMESTAMP_RE.sub('', line))}
            for line in text.splitlines()]


def _spread(words: list[str], start: float, end: float) -> list[tuple[str, float, float]]:
    n = len(words)
    step = (end - start) / n if n else 0.0
    return [(w, start + step * k, start + step * (k + 1)) for k, w in enumerate(words)]


def whisper_tokens(segments: list[dict]) -> list[tuple[str, float, float]]:
    """(token, start, end) per word; interpolated across the segment when word times are missing."""
    tokens = []
    for seg in segments:
        words = seg.get('words') or []
        if words and all(_num(w.get('start')) is not None and _num(w.get('end')) is not None
                         for w in words):
            for w in words:
                start, end = float(w['start']), float(w['end'])
                tokens.extend((t, start, end) for t in _WORD_RE.findall(str(w.get('word', '')).lower()))
        else:
            tokens.extend(_spread(_WORD_RE.findall(seg.get('text', '').lower()),
                                  float(seg['start']), float(seg['end'])))
    return tokens


def upstream_tokens(transcript: UpstreamTranscript) -> list[tuple[str, float | None]]:
    """(token, time) per word; time interpolated inside a timed cue, None when untimed."""
    tokens = []
    for cue in transcript.cues:
        words = _WORD_RE.findall(cue['text'].lower())
        start, end = cue.get('start'), cue.get('end')
        if start is None:
            tokens.extend((w, None) for w in words)
        else:
            end = end if end is not None and end > start else start
            tokens.extend((w, t) for w, t, _ in _spread(words, start, end))
    return tokens


def _unique_shingles(words: list[str], lo: int, hi: int, k: int) -> dict[tuple, int]:
    seen: dict[tuple, int] = {}
    for i in range(lo, hi - k + 1):
        key = tuple(words[i:i + k])
        seen[key] = -1 if key in seen else i
    return seen


def _longest_chain(pairs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Longest chain with strictly increasing second element; pairs are sorted by the first."""
    tails: list[int] = []
    tail_idx: list[int] = []
    prev = [-1] * len(pairs)
    for n, (_, j) in enumerate(pairs):
        pos = bisect_left(tails, j)
        if pos == len(tails):
            tails.append(j)
            tail_idx.append(n)
        else:
            tails[pos] = j
            tail_idx[pos] = n
        prev[n] = tail_idx[pos - 1] if pos else -1
    chain = []
    n = tail_idx[-1] if tail_idx else -1
    while n >= 0:
        chain.append(pairs[n])
        n = prev[n]
    return chain[::-1]


def _match_range(aw, bw, match, a_lo, a_hi, b_lo, b_hi, k):
    """Fill match[i] = j for aligned tokens in aw[a_lo:a_hi] vs bw[b_lo:b_hi]."""
    if a_hi <= a_lo or b_hi <= b_lo:
        return
    if (a_hi - a_lo) * (b_hi - b_lo) <= MAX_CHUNK_CELLS:
        sm = difflib.SequenceMatcher(None, aw[a_lo:a_hi], bw[b_lo:b_hi], autojunk=False)
        for tag, i1, i2, j1, _ in sm.get_opcodes():
            if tag == 'equal':
                for t in range(i2 - i1):
                    match[a_lo + i1 + t] = b_lo + j1 + t
        return
    if k < FALLBACK_SHINGLE_SIZE:
        return
    a_sh = _unique_shingles(aw, a_lo, a_hi, k)
    b_sh = _unique_shingles(bw, b_lo, b_hi, k)
    pairs = sorted((i, b_sh[key]) for key, i in a_sh.items()
                   if i >= 0 and b_sh.get(key, -1) >= 0)
    next_k = FALLBACK_SHINGLE_SIZE if k > FALLBACK_SHINGLE_SIZE else 0
    pa, pb = a_lo, b_lo
    for i, j in _longest_chain(pairs):
        shift = max(pa - i, pb - j, 0)
        if shift >= k:
            continue
        _match_range(aw, bw, match, pa, i + shift, pb, j + shift, next_k)
        for t in range(shift, k):
            match[i + t] = j + t
        pa, pb = i + k, j + k
    _match_range(aw, bw, match, pa, a_hi, pb, b_hi, next_k)


def _gap_runs(match: list[int]) -> list[list[int]]:
    """[first, last, whisper_only_count] runs of unmatched tokens, absorbing short matched islands."""
    runs: list[list[int]] = []
    i, n = 0, len(match)
    while i < n:
        if match[i] >= 0:
            i += 1
            continue
        j = i
        while j < n and match[j] < 0:
            j += 1
        if runs and i - runs[-1][1] - 1 <= MAX_NOISE_TOKENS:
            runs[-1][1] = j - 1
            runs[-1][2] += j - i
        else:
            runs.append([i, j - 1, j - i])
        i = j
    return runs


def _median(values: list[float]) -> float:
    s = sorted(values)
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


def _offset_confirmed(first, last, start, end, a, b, match, matched_idx) -> bool:
    """True when (whisper - upstream) time jumps across the gap by about the gap's duration."""
    pos = bisect_left(matched_idx, first)
    before = matched_idx[max(0, pos - OFFSET_SAMPLE_TOKENS):pos]
    after_pos = bisect_left(matched_idx, last + 1)
    after = matched_idx[after_pos:after_pos + OFFSET_SAMPLE_TOKENS]
    if not after:
        return False
    # A pre-roll has nothing before it; both time axes start at zero.
    off_before = _median([a[i][1] - b[match[i]][1] for i in before]) if before else 0.0
    off_after = _median([a[i][1] - b[match[i]][1] for i in after])
    duration = end - start
    return abs((off_after - off_before) - duration) <= max(
        OFFSET_TOLERANCE_S, OFFSET_TOLERANCE_FRACTION * duration)


def align(segments: list[dict], transcript: UpstreamTranscript, *, min_gap_words: int = 40,
          min_gap_seconds: float = 10.0, merge_within_seconds: float = 3.0,
          min_coverage: float = 0.6) -> dict:
    """Spans of Whisper audio whose words the upstream transcript omits."""
    a = whisper_tokens(segments)
    b = upstream_tokens(transcript)
    timed = transcript.timed
    if not a or not b:
        return {'status': 'empty', 'coverage': 0.0, 'timed': timed, 'spans': []}

    aw = [t[0] for t in a]
    bw = [t[0] for t in b]
    match = [-1] * len(aw)
    _match_range(aw, bw, match, 0, len(aw), 0, len(bw), SHINGLE_SIZE)
    matched_idx = [i for i, j in enumerate(match) if j >= 0]
    coverage = len(matched_idx) / len(aw)
    if coverage < min_coverage:
        return {'status': 'unreliable', 'coverage': coverage, 'timed': timed, 'spans': []}

    merged: list[list] = []
    for first, last, count in _gap_runs(match):
        if merged and a[first][1] - merged[-1][3] <= merge_within_seconds:
            merged[-1][1] = last
            merged[-1][2] += count
            merged[-1][3] = a[last][2]
        else:
            merged.append([first, last, count, a[last][2]])

    spans = []
    for first, last, count, end in merged:
        start = a[first][1]
        if count < min_gap_words or end - start < min_gap_seconds:
            continue
        confirmed = timed and _offset_confirmed(first, last, start, end, a, b, match, matched_idx)
        spans.append({
            'start': round(start, 3),
            'end': round(end, 3),
            'words': count,
            'offset_confirmed': confirmed,
            'text_preview': ' '.join(aw[first:first + PREVIEW_WORDS]),
        })
    return {'status': 'ok', 'coverage': coverage, 'timed': timed, 'spans': spans}


def spans_overlapping(spans: list[dict], start: float, end: float,
                      min_fraction: float = 0.5) -> list[dict]:
    """Spans whose overlap with [start, end) is at least min_fraction of the shorter interval."""
    if end <= start:
        return []
    hits = []
    for span in spans:
        s, e = float(span['start']), float(span['end'])
        overlap = min(e, end) - max(s, start)
        if e > s and overlap > 0 and overlap / min(e - s, end - start) >= min_fraction:
            hits.append(span)
    return hits
