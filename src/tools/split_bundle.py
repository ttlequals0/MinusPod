"""Split a community-submission bundle into per-pattern files.

The in-app exporter packages multiple patterns into one bundle file
(`minuspod-submission-<id>.json`). The maintainer prefers per-pattern
files in `patterns/community/` (one file = one ad). This tool reads a
bundle and writes each contained pattern to its canonical
`<slug>-<short_uuid>.json` filename using the same slugify logic the
validator enforces.

Usage:

    python -m src.tools.split_bundle patterns/community/minuspod-submission-abc.json
    python -m src.tools.split_bundle path/to/bundle.json --keep-original
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

_REPO_SRC = Path(__file__).resolve().parents[1]
if str(_REPO_SRC) not in sys.path:
    sys.path.insert(0, str(_REPO_SRC))

from utils.community_tags import BUNDLE_FORMAT, expected_filename, iter_bundle_patterns  # noqa: E402


def split(bundle_path: Path, *, keep_original: bool = False) -> List[Path]:
    """Write each pattern from `bundle_path` as a sibling per-pattern file.

    Returns the list of written paths. Raises ValueError if the file is not a
    bundle, and FileExistsError if any target per-pattern filename already
    exists in the same directory (no overwrite).
    """
    raw = json.loads(bundle_path.read_text(encoding='utf-8'))
    if not isinstance(raw, dict) or raw.get('format') != BUNDLE_FORMAT:
        raise ValueError(f'{bundle_path.name} is not a bundle '
                         f'(format != {BUNDLE_FORMAT})')

    out_dir = bundle_path.parent
    written: List[Path] = []
    # First pass: compute names + check for collisions before writing anything.
    targets = []
    for i, p in enumerate(iter_bundle_patterns(raw)):
        sponsor = p.get('sponsor') or ''
        cid = p.get('community_id') or ''
        filename = expected_filename(sponsor, cid)
        if filename is None:
            raise ValueError(
                f'patterns[{i}]: missing community_id; cannot derive filename'
            )
        target = out_dir / filename
        if target.exists():
            raise FileExistsError(
                f'refusing to overwrite existing {filename}; '
                f'resolve manually before re-running'
            )
        targets.append((target, p))

    for target, p in targets:
        target.write_text(json.dumps(p, indent=2, ensure_ascii=False) + '\n',
                          encoding='utf-8')
        written.append(target)

    if not keep_original:
        bundle_path.unlink()

    return written


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description='Split a community-submission bundle into per-pattern files.'
    )
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--keep-original', action='store_true',
                        help='Leave the bundle file in place after splitting.')
    args = parser.parse_args(argv)

    try:
        written = split(args.bundle, keep_original=args.keep_original)
    except (ValueError, FileExistsError) as e:
        print(f'ERROR: {e}', file=sys.stderr)
        return 2

    for p in written:
        print(f'Wrote {p}')
    if not args.keep_original:
        print(f'Removed {args.bundle}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
