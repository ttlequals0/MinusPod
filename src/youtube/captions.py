"""YouTube caption tracks (json3) as transcript segments."""
import glob
import json
import os

MIN_EVENT_S = 0.5


def pick_file(directory: str, video_id: str, language: str) -> str | None:
    """Prefer the creator track, then the original auto track, then any json3 file."""
    lang = (language or 'en').split('-')[0]
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
        segs = event.get('segs') or []
        start_ms = int(event.get('tStartMs') or 0)
        dur_ms = int(event.get('dDurationMs') or 0)
        words = []
        for i, seg in enumerate(segs):
            text = (seg.get('utf8') or '').strip()
            if not text:
                continue
            w_start = start_ms + int(seg.get('tOffsetMs') or 0)
            nxt = next((s for s in segs[i + 1:] if (s.get('utf8') or '').strip()), None)
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
