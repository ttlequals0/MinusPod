"""Extract one version's section from CHANGELOG.md.

Used by the release tooling: the section body becomes the GitHub
pre-release notes for that version. With --rollup-since, every section
newer than the given version is included, so a release whose PR shipped
multiple version bumps carries all of them as a rollup.
"""
import argparse
import re
import sys
from pathlib import Path


def _version_key(version: str) -> tuple:
    return tuple(int(part) for part in version.split("."))


def unwrap_lines(text: str) -> str:
    """Join hard-wrapped continuation lines for GitHub release bodies.

    Release bodies render every single newline as a hard line break, so
    the changelog's 72-column wrapping displays as ragged short lines.
    Two-space continuation lines are folded into the line above; headers,
    blank lines, list items, and fenced code blocks are left alone.
    """
    out: list[str] = []
    fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
            out.append(line)
            continue
        is_cont = (
            not fence
            and re.match(r"^  \S", line)
            and not re.match(r"^\s*[-*] ", line)
            and out and out[-1].strip()
            and not out[-1].lstrip().startswith("#")
            and not out[-1].lstrip().startswith("```")
        )
        if is_cont:
            out[-1] += " " + line.strip()
        else:
            out.append(line)
    return "\n".join(out) + "\n"


def extract_section(text: str, version: str) -> str:
    pattern = re.compile(
        r"^## \[" + re.escape(version) + r"\][^\n]*\n(.*?)(?=^## \[|\Z)",
        re.M | re.S,
    )
    match = pattern.search(text)
    if not match:
        raise KeyError(version)
    return match.group(1).strip() + "\n"


def extract_rollup(text: str, version: str, since_exclusive: str) -> str:
    """Sections for every version v with since_exclusive < v <= version.

    Returns the single-section body unchanged when only one version is in
    range; otherwise each section is prefixed with a `## <version>` header,
    newest first (changelog order).
    """
    versions = [
        v for v in re.findall(r"^## \[([0-9.]+)\]", text, re.M)
        if _version_key(since_exclusive) < _version_key(v) <= _version_key(version)
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
