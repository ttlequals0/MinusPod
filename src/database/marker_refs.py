"""Maintenance of episode_marker_refs, the lookup table of pattern ids and sponsors episodes' markers name."""
from utils.markers import extract_marker_refs


def replace_marker_refs(conn, episode_pk: int, markers) -> int:
    """Make the episode's refs match its parsed markers; the caller owns the transaction. Returns the new row count."""
    refs = extract_marker_refs(markers)
    conn.execute("DELETE FROM episode_marker_refs WHERE episode_pk = ?", (episode_pk,))
    conn.executemany("INSERT INTO episode_marker_refs (episode_pk, ref_type, ref_value) VALUES (?, ?, ?)",
                     [(episode_pk, ref_type, value) for ref_type, value in refs])
    return len(refs)
