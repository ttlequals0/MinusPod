"""Language helpers for pattern matching (#252)."""
import re

# Accepted shape for a Whisper transcription language: a bare 2-3 letter code
# (e.g. 'en', 'de', 'pt', 'yue'). faster-whisper rejects region/script subtags
# like 'pt-br' with a fatal ValueError, so they are not allowed here. Shared by
# the global whisperLanguage setting and the per-feed languageOverride so the
# two validators cannot drift.
LANGUAGE_CODE_RE = re.compile(r'^[a-z]{2,3}$')


def get_feed_language_override(db, slug: str | None) -> str | None:
    """Return a feed's raw `language_override` (or None), never raising.

    Single resolution point for the per-feed override lookup, shared by the
    transcription and pattern-stamping paths so their null-handling can't drift.
    """
    if not db or not slug:
        return None
    try:
        podcast = db.get_podcast_by_slug(slug)
    except Exception:
        return None
    if not podcast:
        return None
    return podcast.get('language_override')


def get_pattern_language(db, slug: str | None = None) -> str | None:
    """ISO 639-1 tag to stamp on newly-learned patterns.

    Resolution order:
      1. If `slug` identifies a podcast with a non-empty `language_override`,
         use that. Stamps patterns with the per-feed language so multi-lingual
         setups don't cross-contaminate the pattern DB.
      2. Otherwise, the global `whisper_language` setting.
    'auto' (at either level) returns None.
    """
    if not db:
        return None
    try:
        override = (get_feed_language_override(db, slug) or '').strip().lower() or None
        if override:
            return None if override == 'auto' else override
        lang = (db.get_setting('whisper_language') or 'en').strip().lower()
    except Exception:
        return None
    return None if lang == 'auto' else lang
