"""Language helpers for pattern matching (#252)."""



def get_pattern_language(
    db,
    slug: str | None = None,
    podcast_id: int | None = None,
) -> str | None:
    """ISO 639-1 tag to stamp on newly-learned patterns.

    Resolution order:
      1. If a podcast can be identified (by `slug` or `podcast_id`) and it
         has a non-empty `language_override`, use that. Stamps patterns with
         the per-feed language so multi-lingual setups don't cross-contaminate
         the pattern DB.
      2. Otherwise, the global `whisper_language` setting.
    'auto' (at either level) returns None.
    """
    if not db:
        return None
    try:
        override: str | None = None
        if slug:
            podcast = db.get_podcast_by_slug(slug)
            if podcast:
                override = (podcast.get('language_override') or '').strip().lower() or None
        elif podcast_id is not None:
            conn = db.get_connection()
            row = conn.execute(
                'SELECT language_override FROM podcasts WHERE id = ?',
                (podcast_id,),
            ).fetchone()
            if row:
                override = (row['language_override'] or '').strip().lower() or None
        if override:
            return None if override == 'auto' else override
        lang = (db.get_setting('whisper_language') or 'en').strip().lower()
    except Exception:
        return None
    return None if lang == 'auto' else lang
