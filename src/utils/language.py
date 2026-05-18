"""Language helpers for pattern matching (#252)."""
from typing import Optional


def get_pattern_language(db) -> Optional[str]:
    """ISO 639-1 tag to stamp on newly-learned patterns.

    Reads `whisper_language` setting; returns None for 'auto'.
    """
    if not db:
        return None
    try:
        lang = (db.get_setting('whisper_language') or 'en').strip().lower()
    except Exception:
        return None
    return None if lang == 'auto' else lang
