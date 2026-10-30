"""Tolerant readers for JSON text stored in DB columns."""
import json


def load_json_list(raw: str | None) -> list:
    """Parse a JSON array column; empty, corrupt or non-list values give []."""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []
