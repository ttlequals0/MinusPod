"""Parse CHANGELOG.md into version sections for the What's new dialog and release tooling."""
import re
from pathlib import Path

CHANGELOG_PATH = Path(__file__).resolve().parents[1] / "CHANGELOG.md"

HEADER_RE = re.compile(r"^## \[(\d+\.\d+\.\d+)\](?:\s*-\s*(\S+))?[^\n]*$", re.M)
_SECTION_START_RE = re.compile(r"^## \[", re.M)

_cache: list[dict] | None = None


def version_key(version: str) -> tuple[int, ...] | None:
    """Sort key for an X.Y.Z version string, or None when it is malformed."""
    parts = version.split(".")
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        return None
    return tuple(int(p) for p in parts)


def unwrap_lines(text: str) -> str:
    """Fold two-space continuation lines into the line above; leave headers, blanks, list items and fences alone."""
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


def parse_changelog(text: str) -> list[dict]:
    """Return released sections in file order as {version, date, raw, body}; [Unreleased] is skipped."""
    headers = list(_SECTION_START_RE.finditer(text))
    sections = []
    for i, start in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        chunk = text[start.start():end]
        header, _, rest = chunk.partition("\n")
        match = HEADER_RE.match(header)
        if not match:
            continue
        sections.append({
            "version": match.group(1),
            "date": match.group(2) or "",
            "raw": rest.strip(),
            "body": unwrap_lines(rest.strip()).strip(),
        })
    return sections


def load_sections() -> list[dict]:
    """Sections from the app-root CHANGELOG.md, cached; empty when the file is missing."""
    global _cache
    if _cache is None:
        try:
            _cache = parse_changelog(CHANGELOG_PATH.read_text(encoding="utf-8"))
        except OSError:
            _cache = []
    return _cache
