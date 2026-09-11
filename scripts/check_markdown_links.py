#!/usr/bin/env python3
"""Check local links and GitHub-style heading anchors in maintained docs."""
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCS = [
    ROOT / 'README.md',
    ROOT / 'frontend' / 'README.md',
    *sorted((ROOT / 'docs').glob('*.md')),
]


def heading_anchors(path):
    anchors = set()
    counts = {}
    for heading in re.findall(r'^#{1,6}\s+(.+?)\s*#*$', path.read_text(), re.M):
        slug = re.sub(r'<[^>]+>', '', heading).lower()
        slug = re.sub(r'[^a-z0-9 _-]', '', slug)
        slug = re.sub(r' +', '-', slug)
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        anchors.add(slug if count == 0 else f'{slug}-{count}')
    return anchors


def main():
    errors = []
    anchors = {path: heading_anchors(path) for path in DOCS}
    for path in DOCS:
        for target in re.findall(r'(?<!!)\[[^]]+\]\(([^)]+)\)', path.read_text()):
            target = target.split()[0].strip('<>')
            if target.startswith(('http:', 'https:', 'mailto:')):
                continue
            filename, _, fragment = target.partition('#')
            destination = (path.parent / filename).resolve() if filename else path
            if not destination.exists():
                errors.append(f'{path.relative_to(ROOT)}: missing {target}')
            elif fragment and fragment not in anchors.get(
                    destination, heading_anchors(destination)):
                errors.append(f'{path.relative_to(ROOT)}: missing anchor {target}')
    if errors:
        raise SystemExit('\n'.join(errors))
    print(f'Checked links and anchors in {len(DOCS)} maintained Markdown files')


if __name__ == '__main__':
    main()
