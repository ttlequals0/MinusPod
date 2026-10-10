"""Bounded publisher-transcript fetching, parsing, and alignment against Whisper."""
import difflib
import html
import json
import logging
import re
import time
from bisect import bisect_left
from dataclasses import dataclass

from config import HTTP_MAX_REDIRECTS_FEED, HTTP_TIMEOUT_API
from text_recurrence import SHINGLE_SIZE, WORD_RE
from user_agent import download_user_agent
from utils.http import safe_url_for_log
from utils.safe_http import URLTrust, read_response_capped, safe_get

logger = logging.getLogger(__name__)

MAX_UPSTREAM_TRANSCRIPT_BYTES = 5 * 1024 * 1024
# Untimed text and HTML carry no cue overhead, so a real transcript is far smaller than this.
MAX_UNTIMED_TRANSCRIPT_CHARS = 1024 * 1024
MAX_LINE_CHARS = 2000
MAX_CUES = 50_000
MAX_UPSTREAM_TOKENS = 60_000
FETCH_DEADLINE_S = HTTP_TIMEOUT_API * 3
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

_TIME_RE = r'(?:(\d{1,3}):)?(\d{1,2}):(\d{2})[.,](\d{1,3})'
_CUE_TIMING_RE = re.compile(_TIME_RE + r'\s*-->\s*' + _TIME_RE)
_SRT_SNIFF_RE = re.compile(r'\d{1,9}[ \t\r]*\n\s*' + _TIME_RE + r'\s*-->')
_HTML_SNIFF_RE = re.compile(r'<(?:html|body|p|div|br|span)\b', re.I)
_TAG_RE = re.compile(r'<[^<>]{0,1024}>')
_HTML_DROP_OPEN_RE = re.compile(r'<(head|script|style)\b', re.I)
_HTML_CLOSE_RES = {n: re.compile(rf'</{n}\s*>', re.I) for n in ('head', 'script', 'style')}
_HTML_BREAK_RE = re.compile(r'<(?:br|/?p|/?div|/?li|/?h[1-6]|/?tr)\b[^<>]{0,1024}>', re.I)
_INLINE_TIMESTAMP_RE = re.compile(r'\b\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?\b')
_SPEAKER_RE = re.compile(r"^(?:-\s*)?[A-Z][\w.'-]*(?: [\w.'-]+){0,3}:\s+")
_SPACE_RE = re.compile(r'\s+')
_BLANK_RUN_RE = re.compile(r'\n\s*\n')


class _TooLarge(ValueError):
    pass


@dataclass(frozen=True)
class UpstreamTranscript:
    cues: list[dict]
    timed: bool
    source_url: str
    mime: str


def fetch_upstream_transcript(url: str, mime: str | None,
                              user_agent: str | None = None) -> UpstreamTranscript | None:
    """Fetch and parse an upstream transcript; None on any failure."""
    if not url:
        return None
    deadline = time.monotonic() + FETCH_DEADLINE_S
    try:
        response = safe_get(
            url,
            trust=URLTrust.FEED_CONTENT,
            max_redirects=HTTP_MAX_REDIRECTS_FEED,
            timeout=HTTP_TIMEOUT_API,
            stream=True,
            headers={
                'User-Agent': user_agent or download_user_agent(),
                'Accept': f'{mime}, */*;q=0.5' if mime else '*/*',
            },
        )
        try:
            response.raise_for_status()
            body = read_response_capped(response, MAX_UPSTREAM_TRANSCRIPT_BYTES,
                                        deadline=deadline)
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
    text = body.decode('utf-8-sig', errors='replace').replace('\r\n', '\n').replace('\r', '\n')
    kind, sniffed_json = _strong_sniff(text)
    if kind is None:
        kind = _MIME_KIND.get(_base_mime(mime)) or _weak_sniff(text)
    try:
        if kind in ('vtt', 'srt'):
            cues = _parse_timed_blocks(text)
        elif kind == 'json':
            # Reuse the payload _strong_sniff already parsed when it classified
            # this as json; only a mime-fallback classification parses here.
            cues = (_cues_from_json_payload(sniffed_json) if sniffed_json is not None
                   else _parse_json(text))
        else:
            if len(text) > MAX_UNTIMED_TRANSCRIPT_CHARS:
                raise _TooLarge(f'{len(text)} chars exceeds {MAX_UNTIMED_TRANSCRIPT_CHARS}')
            cues = _parse_lines(_html_to_text(text) if kind == 'html' else text)
    except _TooLarge as e:
        logger.warning(f"Upstream transcript too large, ignored ({kind}): {e}")
        return None
    if not cues:
        return None
    timed = all(c['start'] is not None for c in cues)
    return UpstreamTranscript(cues=cues, timed=timed, source_url=source_url,
                              mime=_KIND_MIME[kind])


def _base_mime(mime: str | None) -> str:
    return (mime or '').split(';', 1)[0].strip().lower()


def _strong_sniff(text: str) -> tuple[str | None, dict | None]:
    """Returns (kind, parsed_json_payload); payload is set only for a json match,
    so the json branch above can reuse it instead of parsing the body again."""
    head = text.lstrip()
    if head.startswith('WEBVTT'):
        return 'vtt', None
    if _SRT_SNIFF_RE.match(head):
        return 'srt', None
    if head.startswith('{'):
        try:
            payload = json.loads(head)
        except ValueError:
            return None, None
        if isinstance(payload, dict) and isinstance(payload.get('segments'), list):
            return 'json', payload
    return None, None


def _weak_sniff(text: str) -> str:
    return 'html' if _HTML_SNIFF_RE.search(text) else 'text'


def _iter_lines(text: str, max_len: int | None = None):
    """Lines without building a list; with max_len, long lines come back in pieces split at spaces."""
    pos, n = 0, len(text)
    while pos < n:
        nl = text.find('\n', pos)
        end = n if nl < 0 else nl
        while max_len and end - pos > max_len:
            cut = text.rfind(' ', pos + 1, pos + max_len)
            cut = cut if cut > 0 else pos + max_len
            yield text[pos:cut]
            pos = cut
        yield text[pos:end]
        pos = end + 1


def _add_cue(cues: list[dict], start, end, text: str) -> None:
    if not WORD_RE.search(text.lower()):
        return
    if len(cues) >= MAX_CUES:
        raise _TooLarge(f'more than {MAX_CUES} cues')
    cues.append({'start': start, 'end': end, 'text': text})


def _clean_cue_text(raw: str) -> str:
    lines = []
    for line in _iter_lines(raw, MAX_LINE_CHARS):
        line = html.unescape(_TAG_RE.sub('', line)).strip()
        line = _SPEAKER_RE.sub('', line)
        if line:
            lines.append(line)
    return _SPACE_RE.sub(' ', ' '.join(lines)).strip()


def _seconds(h, m, s, frac) -> float:
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + int(frac.ljust(3, '0')) / 1000


def _parse_timed_blocks(text: str) -> list[dict]:
    """VTT and SRT cues: a timing line followed by text lines, blocks split by blank lines."""
    cues: list[dict] = []
    timing = None
    body: list[str] = []
    for line in _iter_lines(_BLANK_RUN_RE.sub('\n\n', text) + '\n'):
        if not line.strip():
            if timing:
                _add_cue(cues, _seconds(*timing[:4]), _seconds(*timing[4:]),
                         _clean_cue_text('\n'.join(body)))
            timing, body = None, []
        elif timing:
            body.append(line)
        else:
            m = _CUE_TIMING_RE.search(line)
            if m:
                timing = m.groups()
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
    return _cues_from_json_payload(payload)


def _cues_from_json_payload(payload) -> list[dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get('segments'), list):
        return []
    cues = []
    for seg in payload['segments']:
        if not isinstance(seg, dict):
            continue
        # Some hosts emit start/end/text instead of the namespace's startTime/endTime/body.
        body = seg.get('body', seg.get('text'))
        if isinstance(body, str):
            _add_cue(cues, _num(seg.get('startTime', seg.get('start'))),
                     _num(seg.get('endTime', seg.get('end'))), _clean_cue_text(body))
    return cues


def _drop_html_blocks(text: str) -> str:
    """Remove head, script and style elements; linear, an unclosed element is kept."""
    out = []
    pos = 0
    unclosed: set[str] = set()
    for m in _HTML_DROP_OPEN_RE.finditer(text):
        name = m.group(1).lower()
        if m.start() < pos or name in unclosed:
            continue
        close = _HTML_CLOSE_RES[name].search(text, m.end())
        if not close:
            unclosed.add(name)
            continue
        out.append(text[pos:m.start()])
        out.append(' ')
        pos = close.end()
    out.append(text[pos:])
    return ''.join(out)


def _html_to_text(text: str) -> str:
    return _HTML_BREAK_RE.sub('\n', _drop_html_blocks(text))


def _parse_lines(text: str) -> list[dict]:
    cues: list[dict] = []
    for line in _iter_lines(_BLANK_RUN_RE.sub('\n', text), MAX_LINE_CHARS):
        if line.strip():
            _add_cue(cues, None, None, _clean_cue_text(_INLINE_TIMESTAMP_RE.sub('', line)))
    return cues


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
                tokens.extend((t, start, end) for t in WORD_RE.findall(str(w.get('word', '')).lower()))
        else:
            seg_start, seg_end = _num(seg.get('start')), _num(seg.get('end'))
            if seg_start is None or seg_end is None:
                continue
            tokens.extend(_spread(WORD_RE.findall(seg.get('text', '').lower()),
                                  seg_start, seg_end))
    return tokens


def upstream_tokens(transcript: UpstreamTranscript) -> list[tuple[str, float | None]]:
    """(token, time) per word; time interpolated inside a timed cue, None when untimed."""
    tokens = []
    for cue in transcript.cues:
        words = WORD_RE.findall(cue['text'].lower())
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
    if len(b) > MAX_UPSTREAM_TOKENS:
        return {'status': 'unreliable', 'coverage': 0.0, 'timed': timed, 'spans': []}

    aw = [t[0] for t in a]
    bw = [t[0] for t in b]
    match = [-1] * len(aw)
    _match_range(aw, bw, match, 0, len(aw), 0, len(bw), SHINGLE_SIZE)
    matched_idx = [i for i, j in enumerate(match) if j >= 0]
    coverage = len(matched_idx) / len(aw)
    if coverage < min_coverage:
        return {'status': 'unreliable', 'coverage': coverage, 'timed': timed, 'spans': []}

    # Merge before filtering on purpose: sub-threshold runs split by noise can form one ad.
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
                      min_fraction: float = 0.5, min_fraction_of: str = 'shorter') -> list[dict]:
    """Spans overlapping [start, end) by at least min_fraction of the query, the span, or the shorter."""
    if min_fraction_of not in ('query', 'span', 'shorter'):
        raise ValueError(f'unknown min_fraction_of: {min_fraction_of!r}')
    if end <= start:
        return []
    hits = []
    for span in spans:
        s, e = float(span['start']), float(span['end'])
        overlap = min(e, end) - max(s, start)
        if e <= s or overlap <= 0:
            continue
        if min_fraction_of == 'query':
            denom = end - start
        elif min_fraction_of == 'span':
            denom = e - s
        else:
            denom = min(e - s, end - start)
        if overlap / denom >= min_fraction:
            hits.append(span)
    return hits
