"""Local (archive-import) feed import service.

This module has two halves. The top half (this file, for now) is pure
planning: parsing the ``sNNeNN`` naming scheme, validating sidecar JSON,
synthesizing missing publish dates, and building a dry-run import plan --
no DB, no storage, no threads. The commit engine (staging, moves, embedded
artwork extraction, background job registry) is added below this section
in a later task; the planning half must stand alone.

Stdlib only (``re``, ``json``, ``hashlib``, ``datetime``, ``pathlib``), plus
``utils.validation.is_valid_episode_id`` for a sanity assert on minted ids.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from utils.validation import is_valid_episode_id

# ---------------------------------------------------------------------------
# Planning half -- pure functions only, see module docstring.
# ---------------------------------------------------------------------------

AUDIO_EXTS = {'.mp3'}
ARTWORK_EXTS = {'.jpg', '.jpeg', '.png'}
SIDECAR_KEYS = {'title', 'description', 'published_at', 'season', 'episode'}
FILENAME_RE = re.compile(r'^(s\d{2,3}e\d{2,4})(?: - (.+))?$', re.IGNORECASE)
SYNTH_STEP = timedelta(days=1)

# Common non-mp3 audio containers a user might drop into the import
# directory by mistake -- rejected with a hint to convert rather than
# silently ignored, since an unmatched title/basename would otherwise look
# like a plain naming-scheme mismatch.
AUDIO_LIKE_EXTS = {'.m4a', '.wav', '.flac', '.aac', '.ogg', '.wma', '.opus',
                    '.aiff', '.aif'}
DESCRIPTION_EXT = '.txt'
SIDECAR_EXT = '.json'

_TOKEN_RE = re.compile(r'^s(\d{2,3})e(\d{2,4})$', re.IGNORECASE)
_TEMP_SUFFIXES = {'.part', '.tmp'}
_ISO_FORMAT = '%Y-%m-%dT%H:%M:%SZ'

FFMPEG_HINT = ("not a supported audio format (only .mp3 is imported); "
               "convert first, e.g. "
               "ffmpeg -i in.m4a -codec:a libmp3lame -q:a 2 out.mp3")


def parse_basename(stem: str) -> tuple[str, int, int, str | None] | None:
    """'S01E05 - The Beginning' -> ('s01e05', 1, 5, 'The Beginning');
    None when the stem does not match the scheme."""
    match = FILENAME_RE.match(stem)
    if not match:
        return None
    token, title = match.group(1), match.group(2)
    token = token.lower()
    token_match = _TOKEN_RE.match(token)
    if not token_match:
        return None
    season = int(token_match.group(1))
    episode = int(token_match.group(2))
    return (token, season, episode, title)


def validate_sidecar(data: object) -> tuple[dict | None, str | None]:
    """Fail-closed: returns (clean, None) or (None, 'error message').
    Unknown keys rejected; title 1-500 chars; published_at must parse as
    ISO 8601 WITH timezone; season int >= 0; episode int >= 1."""
    if not isinstance(data, dict):
        return None, 'sidecar must be a JSON object'

    unknown = set(data) - SIDECAR_KEYS
    if unknown:
        return None, f'unknown sidecar key(s): {", ".join(sorted(unknown))}'

    clean: dict = {}

    if 'title' in data:
        title = data['title']
        if not isinstance(title, str) or not (1 <= len(title) <= 500):
            return None, 'title must be a string of 1-500 characters'
        clean['title'] = title

    if 'description' in data:
        description = data['description']
        if not isinstance(description, str):
            return None, 'description must be a string'
        clean['description'] = description

    if 'published_at' in data:
        raw = data['published_at']
        if not isinstance(raw, str) or not raw.strip():
            return None, 'published_at must be an ISO 8601 datetime string'
        try:
            parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        except ValueError:
            return None, 'published_at must be a valid ISO 8601 datetime'
        if parsed.tzinfo is None:
            return None, 'published_at must include a timezone'
        clean['published_at'] = parsed.astimezone(timezone.utc).strftime(_ISO_FORMAT)

    if 'season' in data:
        season = data['season']
        if isinstance(season, bool) or not isinstance(season, int) or season < 0:
            return None, 'season must be an integer >= 0'
        clean['season'] = season

    if 'episode' in data:
        episode = data['episode']
        if isinstance(episode, bool) or not isinstance(episode, int) or episode < 1:
            return None, 'episode must be an integer >= 1'
        clean['episode'] = episode

    return clean, None


def _parse_aware(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def synthesize_published_at(entries: list[dict], now_iso: str) -> str | None:
    """entries sorted ascending by (season, episode); each has
    'published_at' (explicit ISO str or None). Fills gaps in place.
    Rules: explicit dates must be strictly increasing in entry order, else
    return an error string naming both offending episode_ids (hard error).
    The final entry, if unset, anchors at now_iso. Leading run before the
    first anchor: step back SYNTH_STEP per entry. Between two anchors:
    space evenly across the interval. Returns None on success."""
    if not entries:
        return None

    explicit_idx = [i for i, e in enumerate(entries) if e.get('published_at')]
    parsed = {i: _parse_aware(entries[i]['published_at']) for i in explicit_idx}

    for a, b in zip(explicit_idx, explicit_idx[1:]):
        if parsed[b] <= parsed[a]:
            return (f"publish dates out of order: {entries[a]['episode_id']} "
                     f"must be before {entries[b]['episode_id']}")

    last = len(entries) - 1
    if entries[last].get('published_at') is None:
        now_dt = _parse_aware(now_iso)
        entries[last]['published_at'] = now_iso
        parsed[last] = now_dt

    anchors = sorted(parsed)

    # Leading run before the first anchor: step back SYNTH_STEP per entry.
    first = anchors[0]
    for i in range(first - 1, -1, -1):
        steps_back = first - i
        dt = parsed[first] - SYNTH_STEP * steps_back
        entries[i]['published_at'] = dt.strftime(_ISO_FORMAT)
        parsed[i] = dt

    # Even spacing between consecutive anchors.
    for a, b in zip(anchors, anchors[1:]):
        span = b - a
        if span <= 1:
            continue
        start, end = parsed[a], parsed[b]
        step = (end - start) / span
        for k in range(1, span):
            dt = start + step * k
            entries[a + k]['published_at'] = dt.strftime(_ISO_FORMAT)
            parsed[a + k] = dt

    return None


def plan_hash(sources: list[Path]) -> str:
    """sha256 over sorted (name, size, mtime_ns) tuples; commit refuses a
    stale hash (TOCTOU guard)."""
    tuples = sorted(
        (p.name, p.stat().st_size, p.stat().st_mtime_ns) for p in sources
    )
    payload = json.dumps(tuples, sort_keys=True).encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def build_import_plan(slug: str, sources: list[Path], existing_ids: set[str],
                      *, overwrite: bool, now_iso: str) -> dict:
    """Returns the dry-run plan:
    {'slug', 'overwrite', 'planHash',
     'entries': [{'episodeId','season','episode','title','audioFile',
                  'descriptionFile','artworkFile','sidecarFile',
                  'publishedAt','publishedAtSource': 'explicit'|'synthesized',
                  'bytes', 'warnings': [], 'errors': []}],
     'rejected': [{'file', 'reason'}],
     'totals': {'importable': N, 'rejected': N, 'errors': N, 'bytes': N}}"""
    rejected: list[dict] = []
    groups: dict[str, list[Path]] = {}

    for path in sources:
        name = path.name
        if name.startswith('.'):
            rejected.append({'file': name, 'reason': 'hidden file (dotfile)'})
            continue
        if path.suffix.lower() in _TEMP_SUFFIXES:
            rejected.append({'file': name,
                              'reason': 'incomplete file (.part/.tmp)'})
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size == 0:
            rejected.append({'file': name, 'reason': 'empty file (0 bytes)'})
            continue
        ext = path.suffix.lower()
        if ext in AUDIO_LIKE_EXTS:
            rejected.append({'file': name, 'reason': FFMPEG_HINT})
            continue
        groups.setdefault(path.stem, []).append(path)

    candidates: list[dict] = []

    for stem, files in sorted(groups.items()):
        files = sorted(files, key=lambda f: f.name)
        audio = next((f for f in files if f.suffix.lower() in AUDIO_EXTS), None)
        sidecar = next((f for f in files if f.suffix.lower() == SIDECAR_EXT), None)
        description = next((f for f in files if f.suffix.lower() == DESCRIPTION_EXT), None)
        artwork = next((f for f in files if f.suffix.lower() in ARTWORK_EXTS), None)

        if audio is None:
            if sidecar is not None:
                rejected.append({'file': sidecar.name,
                                  'reason': 'sidecar has no matching audio file'})
            continue

        parsed = parse_basename(stem)
        if parsed is None:
            rejected.append({'file': audio.name,
                              'reason': 'file name does not match the sNNeNN naming scheme'})
            continue

        token, season, episode, fname_title = parsed
        episode_id = token
        errors: list[str] = []
        title = fname_title
        published_at = None
        sidecar_name = sidecar.name if sidecar else None

        if sidecar is not None:
            try:
                raw = json.loads(sidecar.read_text(encoding='utf-8'))
            except (OSError, ValueError) as exc:
                errors.append(f'invalid sidecar JSON: {exc}')
                raw = None
            if raw is not None:
                clean, err = validate_sidecar(raw)
                if err:
                    errors.append(f'invalid sidecar: {err}')
                else:
                    overridden = 'season' in clean or 'episode' in clean
                    if 'season' in clean:
                        season = clean['season']
                    if 'episode' in clean:
                        episode = clean['episode']
                    if overridden:
                        episode_id = f's{season:02d}e{episode:02d}'
                        if not is_valid_episode_id(episode_id):
                            errors.append(
                                f'season/episode override out of range: {episode_id}')
                    else:
                        assert is_valid_episode_id(episode_id)
                    if 'title' in clean:
                        title = clean['title']
                    if 'published_at' in clean:
                        published_at = clean['published_at']

        if not title:
            title = f'Episode {episode}'

        candidates.append({
            'episode_id': episode_id,
            'season': season,
            'episode': episode,
            'title': title,
            'audio_file': audio.name,
            'description_file': description.name if description else None,
            'artwork_file': artwork.name if artwork else None,
            'sidecar_file': sidecar_name,
            'published_at': published_at,
            'bytes': audio.stat().st_size,
            'errors': errors,
            'warnings': [],
        })

    # Duplicate episode ids within the batch: per-file error on both.
    by_id: dict[str, list[int]] = {}
    for i, c in enumerate(candidates):
        by_id.setdefault(c['episode_id'], []).append(i)
    for eid, idxs in by_id.items():
        if len(idxs) > 1:
            for i in idxs:
                others = [candidates[j]['audio_file'] for j in idxs if j != i]
                candidates[i]['errors'].append(
                    f'duplicate episode id {eid} also used by {", ".join(others)}')

    # Collisions against already-imported episodes.
    for c in candidates:
        if c['episode_id'] in existing_ids and not overwrite:
            c['errors'].append(f'episode {c["episode_id"]} already exists')

    candidates.sort(key=lambda c: (c['season'], c['episode']))

    for c in candidates:
        c['_explicit'] = c['published_at'] is not None

    synth_err = synthesize_published_at(candidates, now_iso)
    if synth_err:
        for c in candidates:
            c['errors'].append(synth_err)
            if c['published_at'] is None:
                c['published_at'] = now_iso

    entries = []
    for c in candidates:
        entries.append({
            'episodeId': c['episode_id'],
            'season': c['season'],
            'episode': c['episode'],
            'title': c['title'],
            'audioFile': c['audio_file'],
            'descriptionFile': c['description_file'],
            'artworkFile': c['artwork_file'],
            'sidecarFile': c['sidecar_file'],
            'publishedAt': c['published_at'],
            'publishedAtSource': 'explicit' if c['_explicit'] else 'synthesized',
            'bytes': c['bytes'],
            'warnings': c['warnings'],
            'errors': c['errors'],
        })

    importable = sum(1 for e in entries if not e['errors'])
    errored = sum(1 for e in entries if e['errors'])
    total_bytes = sum(e['bytes'] for e in entries if not e['errors'])

    return {
        'slug': slug,
        'overwrite': overwrite,
        'planHash': plan_hash(sources),
        'entries': entries,
        'rejected': rejected,
        'totals': {
            'importable': importable,
            'rejected': len(rejected),
            'errors': errored,
            'bytes': total_bytes,
        },
    }
