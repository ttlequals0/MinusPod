"""Regenerate patterns/community/index.json from the per-pattern JSON files.

Invoked by the `regenerate-manifest` GitHub Action on every push to `main`
that touches `patterns/community/**.json`, and also runnable by hand for
local testing or recovery from a workflow failure:

    python -m tools.generate_manifest

Pattern files live at `patterns/community/<sponsor>-<uuid>.json`. The
manifest is a single document that embeds every pattern inline so the
client fetches everything in one request. `published_at` is bumped to the
current UTC time only when the rendered manifest actually changes -- a no-op
run reuses the prior timestamp so the regenerate-manifest workflow's
`git diff --quiet` stays quiet and skips a spurious commit. `manifest_version`
and `vocabulary_version` are constants that bump only when the format changes.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

# Defensive sys.path bootstrap so `python path/to/script.py` works as well as
# `python -m src.tools.X` (the workflow-style invocation). When run via -m,
# tools/__init__.py already did this -- the lines below are then a no-op.
_REPO_SRC = Path(__file__).resolve().parents[1]
if str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))

from utils.community_tags import (  # noqa: E402, F401
    BUNDLE_FORMAT,
    MANIFEST_VERSION,
    VOCABULARY_VERSION,
    iter_bundle_patterns,
)


def _community_dir() -> Path:
    return _REPO_SRC.parent / 'patterns' / 'community'


def _flatten_to_patterns(path: Path, data: Any) -> List[Dict[str, Any]]:
    """Return per-pattern dicts from a JSON payload. Drops entries missing
    ``community_id`` with a stderr warning so the manifest stays clean."""
    if not isinstance(data, dict):
        print(f'WARN: skipping {path.name}: not a JSON object', file=sys.stderr)
        return []
    is_bundle = data.get('format') == BUNDLE_FORMAT
    out: List[Dict[str, Any]] = []
    for i, p in enumerate(iter_bundle_patterns(data)):
        if not p.get('community_id'):
            label = f'{path.name}#patterns[{i}]' if is_bundle else path.name
            print(f'WARN: {label}: missing community_id', file=sys.stderr)
            continue
        out.append(p)
    return out


def _load_pattern_files(directory: Path) -> List[Dict[str, Any]]:
    """Read every <sponsor>-<uuid>.json in `directory`, excluding index.json.

    Returns the parsed pattern dicts sorted by `submitted_at` so the
    manifest order is deterministic across regenerations. Bundle files
    are flattened so each contained pattern becomes its own manifest entry.
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
        patterns.extend(_flatten_to_patterns(path, data))
    # Coerce to str so a stray non-string submitted_at that slipped past
    # validation can't crash the sort with a TypeError (tools-cli-2).
    patterns.sort(key=lambda d: (str(d.get('submitted_at') or ''), str(d.get('community_id') or '')))
    return patterns


def build_manifest(patterns: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build the manifest document from the loaded pattern dicts."""
    entries = [
        {
            'community_id': p['community_id'],
            'version': int(p.get('version') or 1),
            'data': p,
        }
        for p in patterns
    ]
    return {
        'manifest_version': MANIFEST_VERSION,
        'published_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'vocabulary_version': VOCABULARY_VERSION,
        'patterns': entries,
    }


def _render(manifest: Dict[str, Any]) -> str:
    """Serialize the manifest exactly as it is written to disk."""
    return json.dumps(manifest, indent=2) + '\n'


def reuse_published_at(manifest: Dict[str, Any], existing_text: str | None) -> Dict[str, Any]:
    """Reuse the prior `published_at` when nothing but the timestamp changed.

    `existing_text` is the on-disk index.json verbatim. If re-rendering the new
    manifest with the prior timestamp reproduces that text byte-for-byte, only
    the timestamp would have changed, so we keep the old value. The comparison
    is on rendered bytes -- the same thing the regenerate-manifest workflow's
    `git diff --quiet` checks -- so a true no-op stays quiet and skips the
    spurious timestamp-only commit. Any real content change re-renders
    differently and the fresh timestamp stands.
    """
    if not existing_text:
        return manifest
    try:
        previous = json.loads(existing_text)
    except json.JSONDecodeError:
        return manifest
    prev_published = previous.get('published_at')
    if not isinstance(prev_published, str):
        return manifest
    candidate = {**manifest, 'published_at': prev_published}
    return candidate if _render(candidate) == existing_text else manifest


def write_manifest(manifest: Dict[str, Any], path: Path) -> None:
    """Write the manifest atomically to `path`, preserving a trailing newline."""
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(_render(manifest), encoding='utf-8')
    tmp.replace(path)


def main() -> int:
    directory = _community_dir()
    if not directory.exists():
        print(f'ERROR: {directory} does not exist', file=sys.stderr)
        return 1
    patterns = _load_pattern_files(directory)
    target = directory / 'index.json'
    existing_text: str | None = None
    if target.exists():
        try:
            existing_text = target.read_text(encoding='utf-8')
        except OSError as e:
            print(f'WARN: ignoring unreadable {target.name}: {e}', file=sys.stderr)
    manifest = reuse_published_at(build_manifest(patterns), existing_text)
    write_manifest(manifest, target)
    print(
        f'Wrote {target.relative_to(_REPO_SRC.parent)} '
        f'with {len(patterns)} pattern(s) at {manifest["published_at"]}'
    )
    return 0


if __name__ == '__main__':
    sys.exit(main())
