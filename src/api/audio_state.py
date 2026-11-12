"""Pattern audio state for the marker and detection rows an API response returns."""


def add_pattern_audio_state(db, rows, id_key):
    """Set patternAudioState (active, off or none) on every row whose id_key names an existing pattern; one query."""
    pattern_ids = {r[id_key] for r in rows
                   if isinstance(r.get(id_key), int) and not isinstance(r.get(id_key), bool)}
    if not pattern_ids:
        return
    summaries = db.get_audio_summaries(sorted(pattern_ids))
    for row in rows:
        summary = summaries.get(row.get(id_key)) if row.get(id_key) in pattern_ids else None
        if summary is not None:
            row['patternAudioState'] = summary['state']
