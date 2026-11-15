"""Structural validator and index builder for `patterns/show-segments/` entries, which cannot be reviewed by reading.
Stdlib only: the manifest workflow imports it without the app's dependencies."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any

KIND = 'show_segment'
FORMAT_VERSION = 1
SHOW_BOUND_CATEGORIES = ('cross_promo', 'interaction', 'self_promo', 'intro', 'outro', 'recap')
# recap is import/validate only in v1: receivers may bind recap entries, the library does not accept them yet.
LIBRARY_CATEGORIES = tuple(c for c in SHOW_BOUND_CATEGORIES if c != 'recap')
SHORT_ASK_ENTRY_CATEGORIES = ('interaction', 'self_promo')
KEY_KINDS = ('podcast_guid', 'feed_url_guid')
ANCHORS = ('start', 'end', 'break', 'any')
KDF = {'alg': 'scrypt', 'n': 2 ** 15, 'r': 8, 'p': 1}
SHINGLE_K = 3
HASH_BITS = 24
BUCKETS = 1024
# (core min, core max, head/tail size) per shape.
LONG_SIZES = (12, 400, 6)
SHORT_SIZES = (4, 40, 3)
EVIDENCE_CAP = 10
MAX_DURATION_S = 600
# Entries per (bucket, category) cluster of overlapping cores.
CLUSTER_CAP = 6
CLUSTER_OVERLAP = 0.5
_HASH = re.compile(r'^[0-9a-f]{6}$')
_KEYS = {'kind', 'format_version', 'community_id', 'version', 'submitted_at', 'submitted_app_version',
         'category', 'short_ask', 'source_language', 'bucket', 'key_kind', 'kdf', 'shingle_k', 'hash_bits',
         'core', 'head', 'tail', 'duration', 'position', 'evidence'}
_EVIDENCE_KEYS = {'confirms', 'episodes', 'span_weeks'}


def sizes_for(short_ask: bool) -> tuple[int, int, int]:
    return SHORT_SIZES if short_ask else LONG_SIZES


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _hashes_problem(name: str, value) -> str | None:
    if not isinstance(value, list) or not all(isinstance(h, str) and _HASH.match(h) for h in value):
        return f'{name} must be a list of 6-digit lowercase hex hashes'
    if len(set(value)) != len(value):
        return f'{name} has duplicate hashes'
    return None


def entry_problems(entry: Any, *, library: bool = True) -> list[str]:
    """Why an entry is malformed, empty when it is valid. `library=False` is the receiver's check
    (recap entries allowed); the library (CI) also refuses recap."""
    if not isinstance(entry, dict):
        return ['entry is not a JSON object']
    problems = []
    extra = set(entry) - _KEYS
    if extra:
        problems.append(f'unexpected fields (plaintext is never published): {sorted(extra)}')
    if '@' in json.dumps(entry):
        problems.append("an '@' appears in the entry (no handle or email may be published)")
    if entry.get('kind') != KIND or entry.get('format_version') != FORMAT_VERSION:
        problems.append(f"kind must be '{KIND}' with format_version {FORMAT_VERSION}")
    try:
        uuid.UUID(str(entry.get('community_id')))
    except ValueError:
        problems.append('community_id must be a UUID')
    if not _is_int(entry.get('version')) or entry['version'] < 1:
        problems.append('version must be a positive integer')
    category = entry.get('category')
    allowed = LIBRARY_CATEGORIES if library else SHOW_BOUND_CATEGORIES
    if category == 'sponsor':
        problems.append('category sponsor is not a show segment (sponsor stays sponsor)')
    elif category not in allowed:
        problems.append(f'category must be one of {list(allowed)}')
    short_ask = entry.get('short_ask')
    if not isinstance(short_ask, bool):
        problems.append('short_ask must be true or false')
    elif short_ask and category not in SHORT_ASK_ENTRY_CATEGORIES:
        problems.append('short_ask is only valid for interaction and self_promo')
    if not _is_int(entry.get('bucket')) or not 0 <= entry['bucket'] < BUCKETS:
        problems.append(f'bucket must be an integer in [0, {BUCKETS})')
    if entry.get('key_kind') not in KEY_KINDS:
        problems.append(f'key_kind must be one of {list(KEY_KINDS)}')
    if entry.get('kdf') != KDF or entry.get('shingle_k') != SHINGLE_K or entry.get('hash_bits') != HASH_BITS:
        problems.append('kdf, shingle_k and hash_bits must be the v1 constants')
    problems += _core_problems(entry, bool(short_ask))
    problems += _prior_problems(entry)
    return problems


def _core_problems(entry: dict, short_ask: bool) -> list[str]:
    core_min, core_max, anchors = sizes_for(short_ask)
    problems = [p for p in (_hashes_problem(n, entry.get(n)) for n in ('core', 'head', 'tail')) if p]
    if problems:
        return problems
    core = entry['core']
    if not core_min <= len(core) <= core_max:
        problems.append(f'core has {len(core)} hashes; this shape needs {core_min}-{core_max}')
    if core != sorted(core):
        problems.append('core must be sorted')
    for name in ('head', 'tail'):
        if not 1 <= len(entry[name]) <= anchors or not set(entry[name]) <= set(core):
            problems.append(f'{name} must be 1-{anchors} hashes taken from the core')
    return problems


def _prior_problems(entry: dict) -> list[str]:
    problems = []
    d = entry.get('duration')
    values = [d.get(k) for k in ('p10', 'p50', 'p90')] if isinstance(d, dict) else []
    if (len(values) != 3 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values)
            or not 0 < values[0] <= values[1] <= values[2] <= MAX_DURATION_S or set(d) != {'p10', 'p50', 'p90'}):
        problems.append(f'duration must be 0 < p10 <= p50 <= p90 <= {MAX_DURATION_S}')
    position = entry.get('position')
    if (not isinstance(position, dict) or position.get('anchor') not in ANCHORS
            or not _is_int(position.get('window_s')) or set(position) != {'anchor', 'window_s'}):
        problems.append(f'position must be {{anchor: {"|".join(ANCHORS)}, window_s: int}}')
    evidence = entry.get('evidence')
    if (not isinstance(evidence, dict) or set(evidence) != _EVIDENCE_KEYS
            or not all(_is_int(evidence[k]) and 0 <= evidence[k] for k in _EVIDENCE_KEYS)
            or evidence['confirms'] > EVIDENCE_CAP or evidence['episodes'] > EVIDENCE_CAP):
        problems.append(f'evidence must hold confirms/episodes (capped at {EVIDENCE_CAP}) and span_weeks')
    for key in ('submitted_at', 'submitted_app_version', 'source_language'):
        if entry.get(key) is not None and not isinstance(entry.get(key), str):
            problems.append(f'{key} must be a string')
    return problems


def cluster_problems(entries: list[dict]) -> list[str]:
    """More than CLUSTER_CAP entries of one bucket and category whose cores overlap >= 50 % is a flood."""
    problems = []
    groups: dict[tuple, list[dict]] = {}
    for e in entries:
        if isinstance(e, dict) and isinstance(e.get('core'), list):
            groups.setdefault((e.get('bucket'), e.get('category')), []).append(e)
    for (bucket, category), group in groups.items():
        for e in group:
            core = set(e['core'])
            near = [o for o in group
                    if core and len(core & set(o['core'])) / min(len(core), len(o['core']) or 1) >= CLUSTER_OVERLAP]
            if len(near) > CLUSTER_CAP:
                problems.append(f'bucket {bucket} {category}: {len(near)} overlapping entries exceed the cap '
                                f'of {CLUSTER_CAP}')
                break
    return problems


def segment_dir(repo_root: Path) -> Path:
    return repo_root / 'patterns' / 'show-segments'


def load_entries(directory: Path) -> list[tuple[Path, bytes, Any]]:
    """(path, raw bytes, parsed JSON or None) for every *.json directly in `directory`."""
    out = []
    for path in sorted(directory.glob('*.json')) if directory.is_dir() else []:
        raw = path.read_bytes()
        try:
            out.append((path, raw, json.loads(raw.decode('utf-8'))))
        except (json.JSONDecodeError, UnicodeDecodeError):
            out.append((path, raw, None))
    return out


def index_entries(directory: Path) -> list[dict]:
    """Thin manifest entries for the valid files of `directory`, hashed like the sponsor index."""
    entries = []
    for path, raw, data in load_entries(directory):
        if data is None or entry_problems(data):
            print(f'WARN: skipping {path.name}: not a valid show segment', file=sys.stderr)
            continue
        entries.append({'community_id': data['community_id'], 'version': data['version'],
                        'content_hash': hashlib.sha256(raw).hexdigest(), 'path': path.name})
    return sorted(entries, key=lambda e: e['community_id'])


def validate_files(paths: list[Path], existing: list[dict]) -> dict[str, list[str]]:
    """Per-file problems for a PR's changed files, including the cluster cap against the existing library."""
    report: dict[str, list[str]] = {}
    parsed = []
    for path in paths:
        if not re.fullmatch(r'seg-[0-9a-f]{8}\.json', path.name):
            report[str(path)] = ['file name must be seg-<first 8 hex of community_id>.json']
            continue
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
            report[str(path)] = [f'unreadable JSON: {e}']
            continue
        problems = entry_problems(data)
        if not problems and not path.name.startswith('seg-' + str(data['community_id']).replace('-', '')[:8]):
            problems.append('file name does not match community_id')
        report[str(path)] = problems
        if not problems:
            parsed.append(data)
    names = {str(p) for p in paths}
    others = [e for e in existing if e.get('community_id') not in {d['community_id'] for d in parsed}]
    for problem in cluster_problems(others + parsed):
        for name in names:
            report.setdefault(name, []).append(problem)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--pr-files', nargs='*', default=[])
    parser.add_argument('--comment-output')
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[2]
    existing = [d for _p, _r, d in load_entries(segment_dir(root)) if isinstance(d, dict)]
    report = validate_files([Path(p) for p in args.pr_files], existing)
    failed = {k: v for k, v in report.items() if v}
    lines = ['## Show-segment validation', '']
    for name, problems in sorted(report.items()):
        lines.append(f'- `{name}`: ' + ('ok' if not problems else '; '.join(problems)))
    text = '\n'.join(lines) + '\n'
    if args.comment_output:
        Path(args.comment_output).write_text(text, encoding='utf-8')
    print(text)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
