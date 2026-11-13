"""Extract one version's section from CHANGELOG.md.

Used by the release tooling: the section body becomes the GitHub
pre-release notes for that version. With --rollup-since, every section
newer than the given version is included, so a release whose PR shipped
multiple version bumps carries all of them as a rollup.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from changelog import parse_changelog, unwrap_lines, version_key  # noqa: E402


def extract_section(text: str, version: str) -> str:
    for section in parse_changelog(text):
        if section["version"] == version:
            return section["raw"] + "\n"
    raise KeyError(version)


def extract_rollup(text: str, version: str, since_exclusive: str) -> str:
    """Sections for every version v with since_exclusive < v <= version.

    One version in range returns its body unchanged; otherwise each section gets a
    `## <version>` header, newest first.
    """
    low, high = version_key(since_exclusive), version_key(version)
    if low is None or high is None:
        raise KeyError(version)
    versions = [
        s["version"] for s in parse_changelog(text)
        if low < version_key(s["version"]) <= high
    ]
    if version not in versions:
        raise KeyError(version)
    if len(versions) == 1:
        return extract_section(text, version)
    parts = [f"## {v}\n\n{extract_section(text, v)}" for v in versions]
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version")
    parser.add_argument("--changelog", type=Path, default=Path("CHANGELOG.md"))
    parser.add_argument(
        "--rollup-since", metavar="VERSION",
        help="include every section newer than this version (exclusive)")
    args = parser.parse_args()
    try:
        text = args.changelog.read_text()
        if args.rollup_since:
            section = extract_rollup(text, args.version, args.rollup_since)
        else:
            section = extract_section(text, args.version)
        section = unwrap_lines(section)
    except KeyError:
        print(f"version {args.version} not found in {args.changelog}",
              file=sys.stderr)
        return 1
    sys.stdout.write(section)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
