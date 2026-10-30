"""YouTube caption tracks (json3) as transcript segments."""
import glob
import json
import os
import re

MIN_EVENT_S = 0.5
_CUE_WORDS = (r'music|applause|laughter|laughs|laughing|inaudible|crosstalk|cheering|singing|sighs|'
              r'foreign language|silence|noise')
_NOISE = re.compile(
    rf'\[[^\]]*(?:{_CUE_WORDS})[^\]]*\]|\([^)]*(?:{_CUE_WORDS})[^)]*\)|>{{2,}}', re.IGNORECASE)


def _clean(text: str) -> str:
    """Drop speaker-change markers and bracketed or parenthesized sound cues (not spoken asides)."""
    return ' '.join(_NOISE.sub(' ', text or '').split())


def pick_file(directory: str, video_id: str, language: str) -> str | None:
    """Prefer the creator track, then the original auto track, then any json3 file."""
    if language and language != 'auto':
        lang = language.split('-')[0]
        for name in (f'{video_id}.{lang}.json3', f'{video_id}.{lang}-orig.json3'):
            path = os.path.join(directory, name)
            if os.path.exists(path):
                return path
    others = sorted(glob.glob(os.path.join(directory, f'{video_id}.*.json3')))
    return others[0] if others else None


def json3_to_segments(data: dict) -> list[dict]:
    """Convert json3 events to Whisper-shaped segments; events under MIN_EVENT_S join the previous one."""
    segments: list[dict] = []
    for event in data.get('events') or []:
        segs = [(seg, _clean(seg.get('utf8'))) for seg in event.get('segs') or []]
        start_ms = int(event.get('tStartMs') or 0)
        dur_ms = int(event.get('dDurationMs') or 0)
        words = []
        for i, (seg, text) in enumerate(segs):
            if not text:
                continue
            w_start = start_ms + int(seg.get('tOffsetMs') or 0)
            nxt = next((s for s, t in segs[i + 1:] if t), None)
            w_end = start_ms + int(nxt.get('tOffsetMs') or 0) if nxt and nxt.get('tOffsetMs') is not None else start_ms + dur_ms
            words.append({'word': text, 'start': w_start / 1000.0, 'end': max(w_end, w_start) / 1000.0})
        if not words:
            continue
        if segments and dur_ms / 1000.0 < MIN_EVENT_S:
            prev = segments[-1]
            prev['words'].extend(words)
            prev['end'] = max(prev['end'], words[-1]['end'])
            prev['text'] = ' '.join(w['word'] for w in prev['words'])
            continue
        segments.append({'start': start_ms / 1000.0, 'end': max((start_ms + dur_ms) / 1000.0, words[-1]['end']),
                         'text': ' '.join(w['word'] for w in words), 'words': words})
    return segments


def load_segments(path: str | None) -> list[dict]:
    if not path or not os.path.exists(path):
        return []
    with open(path, encoding='utf-8') as fh:
        return json3_to_segments(json.load(fh))
