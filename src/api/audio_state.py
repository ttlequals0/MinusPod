"""Pattern audio state for the marker and detection rows an API response returns."""


def add_pattern_audio_state(db, rows, id_key):
    """Set patternAudioState (active, off or none) on every row whose id_key names an existing pattern; one query."""
    targets = [(row, row[id_key]) for row in rows
               if isinstance(row.get(id_key), int) and not isinstance(row.get(id_key), bool)]
    if not targets:
        return
    states = db.get_audio_states(sorted({pattern_id for _, pattern_id in targets}))
    for row, pattern_id in targets:
        if pattern_id in states:
            row['patternAudioState'] = states[pattern_id]
