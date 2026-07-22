"""Extract one version's section from CHANGELOG.md.

Used by the release tooling: the section body becomes the GitHub
pre-release notes for that version.
"""
import argparse
import re
import sys
from pathlib import Path


def extract_section(text: str, version: str) -> str:
    pattern = re.compile(
        r"^## \[" + re.escape(version) + r"\][^\n]*\n(.*?)(?=^## \[|\Z)",
        re.M | re.S,
    )
    match = pattern.search(text)
    if not match:
        raise KeyError(version)
    return match.group(1).strip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    args = parser.parse_args()
    try:
        section = extract_section(args.changelog.read_text(), args.version)
    except KeyError:
        print(f"version {args.version} not found in {args.changelog}",
              file=sys.stderr)
        return 1
    sys.stdout.write(section)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
