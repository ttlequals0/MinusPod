"""Re-export MinusPod's lifted JSON parsers so the benchmark uses the
exact same response-parsing path production uses.

These imports run after the sys.path setup in ``benchmark/__init__.py``
puts ``src/`` (the parent MinusPod tree) on the path.
"""
from __future__ import annotations

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
]
