"""Re-export MinusPod's lifted JSON parsers so the benchmark uses the
exact same response-parsing path production uses.

These imports run after the sys.path setup in ``benchmark/__init__.py``
puts ``src/`` (the parent MinusPod tree) on the path.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from ad_detector import (  # type: ignore[import-not-found]
    extract_json_ads_array,
    parse_ads_from_response,
    get_static_system_prompt,
    format_window_prompt,
    deduplicate_window_ads,
)

__all__ = [
    "extract_json_ads_array",
    "parse_ads_from_response",
    "get_static_system_prompt",
    "format_window_prompt",
    "deduplicate_window_ads",
    "resolve_system_prompt",
]


def resolve_system_prompt(snapshot: Path | None) -> tuple[str, str]:
    """Resolve the system prompt for a run and a label for the report.

    ``None`` returns the live prompt from ``get_static_system_prompt()``; a path
    returns the file's verbatim text (a frozen prompt that live SEED_SPONSORS
    edits no longer touch). The label carries the first 8 hex of the prompt's
    sha256 for traceability, and uses the file name only -- never the path -- so
    a committed report leaks nothing local.
    """
    if snapshot is None:
        text = get_static_system_prompt()
        source = "live"
    else:
        if not snapshot.is_file():
            raise FileNotFoundError(f"snapshot prompt not found: {snapshot}")
        text = snapshot.read_text()
        if not text.strip():
            raise ValueError(f"snapshot prompt is empty: {snapshot}")
        source = f"snapshot:{snapshot.name}"
    sha8 = hashlib.sha256(text.encode()).hexdigest()[:8]
    return text, f"{source} (sha256:{sha8})"
