"""Regenerate patterns/community/index.json from the per-pattern JSON files.

Invoked by the `regenerate-manifest` GitHub Action on every push to `main`
that touches `patterns/community/**.json`, and also runnable by hand for
local testing or recovery from a workflow failure:

    python -m tools.generate_manifest

Pattern files live at `patterns/community/<sponsor>-<uuid>.json`. The
manifest is a single document that embeds every pattern inline so the
client fetches everything in one request. `published_at` is bumped to
the current UTC time; `manifest_version` and `vocabulary_version` are
constants that bump only when the format changes.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

# Defensive sys.path bootstrap so `python path/to/script.py` works as well as
# `python -m src.tools.X` (the workflow-style invocation). When run via -m,
# tools/__init__.py already did this — the lines below are then a no-op.
_REPO_SRC = Path(__file__).resolve().parents[1]
if str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))

from utils.community_tags import MANIFEST_VERSION, VOCABULARY_VERSION  # noqa: E402, F401


def _community_dir() -> Path:
    return _REPO_SRC.parent / 'patterns' / 'community'


def _load_pattern_files(directory: Path) -> List[Dict[str, Any]]:
    """Read every <sponsor>-<uuid>.json in `directory`, excluding index.json.

    Returns the parsed pattern dicts sorted by `submitted_at` so the
    manifest order is deterministic across regenerations.
    """
    patterns: List[Dict[str, Any]] = []
    for path in sorted(directory.glob('*.json')):
        if path.name == 'index.json':
            continue
        try:
            with path.open('r', encoding='utf-8') as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            print(f'WARN: skipping {path.name}: {e}', file=sys.stderr)
            continue
        if not isinstance(data, dict):
            print(f'WARN: skipping {path.name}: not a JSON object', file=sys.stderr)
            continue
        if not data.get('community_id'):
            print(f'WARN: skipping {path.name}: missing community_id', file=sys.stderr)
            continue
        patterns.append(data)
    patterns.sort(key=lambda d: (d.get('submitted_at') or '', d.get('community_id') or ''))
    return patterns


def build_manifest(patterns: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build the manifest document from the loaded pattern dicts."""
    entries = []
    for p in patterns:
        entries.append({
            'community_id': p['community_id'],
            'version': int(p.get('version') or 1),
            'data': p,
        })
    return {
        'manifest_version': MANIFEST_VERSION,
        'published_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'vocabulary_version': VOCABULARY_VERSION,
        'patterns': entries,
    }


def write_manifest(manifest: Dict[str, Any], path: Path) -> None:
    """Write the manifest atomically to `path`, preserving a trailing newline."""
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('w', encoding='utf-8') as fh:
        json.dump(manifest, fh, indent=2)
        fh.write('\n')
    tmp.replace(path)


def main() -> int:
    directory = _community_dir()
    if not directory.exists():
        print(f'ERROR: {directory} does not exist', file=sys.stderr)
        return 1
    patterns = _load_pattern_files(directory)
    manifest = build_manifest(patterns)
    target = directory / 'index.json'
    write_manifest(manifest, target)
    print(
        f'Wrote {target.relative_to(_REPO_SRC.parent)} '
        f'with {len(patterns)} pattern(s) at {manifest["published_at"]}'
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
