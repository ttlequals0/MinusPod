"""Maintenance of episode_marker_refs, the lookup table of pattern ids and sponsors episodes' markers name."""
from utils.markers import extract_marker_refs


def replace_marker_refs(conn, episode_pk: int, markers) -> None:
    """Make the episode's refs match its parsed markers, writing only the difference; the caller owns the transaction."""
    new = extract_marker_refs(markers)
    old = {(r[0], r[1]) for r in conn.execute(
        "SELECT ref_type, ref_value FROM episode_marker_refs WHERE episode_pk = ?", (episode_pk,))}
    conn.executemany("DELETE FROM episode_marker_refs WHERE episode_pk = ? AND ref_type = ? AND ref_value = ?",
                     [(episode_pk, t, v) for t, v in old - new])
    conn.executemany("INSERT INTO episode_marker_refs (episode_pk, ref_type, ref_value) VALUES (?, ?, ?)",
                     [(episode_pk, t, v) for t, v in new - old])
