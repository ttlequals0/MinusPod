"""Scaffold a community pattern JSON file from the CLI.

Intended for contributors who hand-craft patterns (the bundle exporter
in the app handles automated submissions). Produces a file at the
canonical `<slug>-<short_uuid>.json` path so the PR validator's
filename check passes by construction.

Usage:

    python -m src.tools.scaffold_community_pattern \\
        --sponsor "Shopify" \\
        --text-template "Shopify is the commerce platform ..." \\
        --tags universal business \\
        --aliases "Shop"

The community_id is generated unless `--community-id <uuid>` is passed.
The script refuses to overwrite an existing file unless `--force` is set.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

_REPO_SRC = Path(__file__).resolve().parents[1]
_REPO_ROOT = _REPO_SRC.parent
# Add both `src/` (for utils.community_tags) and the repo root (for
# top-level `version.py`) so the CLI works under `python -m tools.X`.
for _p in (_REPO_SRC, _REPO_ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from utils.community_tags import app_version, expected_filename, valid_tags  # noqa: E402


def _default_out_dir() -> Path:
    return _REPO_SRC.parent / 'patterns' / 'community'


def scaffold(
    *,
    sponsor: str,
    text_template: str,
    tags: List[str],
    aliases: Optional[List[str]] = None,
    out_dir: Optional[Path] = None,
    community_id: Optional[str] = None,
    intro_variants: Optional[List[str]] = None,
    outro_variants: Optional[List[str]] = None,
    force: bool = False,
) -> Path:
    """Write a scaffolded pattern JSON to `<out_dir>/<slug>-<short>.json`.

    Raises FileExistsError if the target exists and `force` is False.
    Returns the written path.
    """
    out_dir = out_dir or _default_out_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    community_id = community_id or str(uuid.uuid4())

    payload = {
        'scope': 'global',
        'text_template': text_template,
        'intro_variants': intro_variants or [],
        'outro_variants': outro_variants or [],
        'avg_duration': None,
        'sponsor': sponsor,
        'sponsor_aliases': aliases or [],
        'sponsor_tags': tags,
        'source_language': None,
        'community_id': community_id,
        'version': 1,
        'submitted_at': datetime.now(timezone.utc).isoformat(),
        'submitted_app_version': app_version(),
        'sponsor_match': 'unknown',
    }

    filename = expected_filename(sponsor, community_id)
    if filename is None:
        raise ValueError('community_id is required')
    target = out_dir / filename
    if target.exists() and not force:
        raise FileExistsError(
            f'{target.name} already exists (use --force to overwrite)'
        )
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + '\n',
                      encoding='utf-8')
    return target


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description='Scaffold a community pattern JSON file.'
    )
    parser.add_argument('--sponsor', required=True)
    parser.add_argument('--text-template', required=True,
                        help='Full ad text. Must be 50-3500 chars to pass validation.')
    parser.add_argument('--tags', nargs='*', default=['universal'])
    parser.add_argument('--aliases', nargs='*', default=[])
    parser.add_argument('--intro', dest='intro_variants', nargs='*', default=[])
    parser.add_argument('--outro', dest='outro_variants', nargs='*', default=[])
    parser.add_argument('--community-id', default=None,
                        help='Override the generated UUID (for tests/recovery).')
    parser.add_argument('--out-dir', type=Path, default=None,
                        help='Target directory (default: patterns/community/).')
    parser.add_argument('--force', action='store_true',
                        help='Overwrite an existing file.')
    args = parser.parse_args(argv)

    vt = valid_tags()
    unknown = [t for t in args.tags if t not in vt]
    if unknown:
        print(f'WARN: unknown tag(s) {unknown} -- the validator will reject. '
              f'See patterns/vocabulary.json.', file=sys.stderr)

    try:
        path = scaffold(
            sponsor=args.sponsor,
            text_template=args.text_template,
            tags=args.tags,
            aliases=args.aliases,
            intro_variants=args.intro_variants,
            outro_variants=args.outro_variants,
            community_id=args.community_id,
            out_dir=args.out_dir,
            force=args.force,
        )
    except FileExistsError as e:
        print(f'ERROR: {e}', file=sys.stderr)
        return 2

    print(f'Wrote {path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
