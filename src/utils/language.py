"""Language helpers for pattern matching (#252)."""
import os
import re

# Whisper language shape: a bare 2-3 letter code ('en', 'yue'). faster-whisper raises on
# subtags like 'pt-br'. Shared by the whisperLanguage setting and per-feed languageOverride.
LANGUAGE_CODE_RE = re.compile(r'^[a-z]{2,3}$')


def get_feed_language_override(db, slug: str | None) -> str | None:
    """A feed's raw `language_override` (or None), never raising; shared by transcription and pattern stamping."""
    if not db or not slug:
        return None
    try:
        podcast = db.get_podcast_row(slug)
    except Exception:
        return None
    if not podcast:
        return None
    return podcast.get('language_override')


def get_pattern_language(db, slug: str | None = None) -> str | None:
    """ISO 639-1 tag to stamp on newly-learned patterns.

    The feed's non-empty `language_override` wins (so multi-lingual setups do not
    cross-contaminate patterns), else the global `whisper_language` setting, else the `WHISPER_LANGUAGE`
    environment variable (as the transcriber resolves it), else 'en'. 'auto' returns None.
    """
    if not db:
        return None
    try:
        override = (get_feed_language_override(db, slug) or '').strip().lower() or None
        if override:
            return None if override == 'auto' else override
        # Same order as the transcriber: saved setting, then WHISPER_LANGUAGE, then 'en'.
        lang = ((db.get_setting('whisper_language') or '').strip().lower()
                or (os.environ.get('WHISPER_LANGUAGE') or '').strip().lower()
                or 'en')
    except Exception:
        return None
    return None if lang == 'auto' else lang
