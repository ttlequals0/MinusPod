"""Connection-level deletes and moves of fingerprint rows and match history, never committing."""


def delete_pattern_dependents_in(conn, id_clause: str, params=()) -> None:
    """Delete fingerprints and match history of the patterns id_clause selects, on conn, without committing."""
    # Explicit as well as the FK cascade: enforcement is per connection.
    for table in ('audio_fingerprints', 'audio_fingerprint_matches'):
        conn.execute(f"DELETE FROM {table} WHERE pattern_id IN {id_clause}", params)  # noqa: S608


def delete_pattern_dependents(conn, pattern_ids: list[int]) -> None:
    """Delete fingerprints and match history of the given patterns on conn, without committing."""
    delete_pattern_dependents_in(conn, f"({','.join('?' * len(pattern_ids))})", pattern_ids)


def delete_matches_for_patterns(conn, pattern_ids: list[int]) -> int:
    """Delete every match row of the given patterns on conn, without committing."""
    return conn.execute(
        f"DELETE FROM audio_fingerprint_matches WHERE pattern_id IN ({','.join('?' * len(pattern_ids))})",  # noqa: S608
        pattern_ids).rowcount


def delete_matches_for_episode(conn, podcast_slug: str, episode_id: str, keep_rejected: bool = False) -> int:
    """Delete an episode's match rows on conn, optionally sparing rejected ones."""
    sql = "DELETE FROM audio_fingerprint_matches WHERE podcast_slug = ? AND episode_id = ?"
    if keep_rejected:
        sql += " AND rejected = 0"
    return conn.execute(sql, (podcast_slug, episode_id)).rowcount


def delete_matches_for_episodes(conn, podcast_slug: str, episode_ids: list[str]) -> int:
    """Delete the match rows of the given episodes of one feed on conn, without committing."""
    return sum(delete_matches_for_episode(conn, podcast_slug, episode_id) for episode_id in episode_ids)


def delete_matches_for_missing_episodes(conn) -> int:
    """Delete match rows whose feed or episode row no longer exists, on conn, without committing."""
    return conn.execute(
        "DELETE FROM audio_fingerprint_matches WHERE NOT EXISTS ("
        "SELECT 1 FROM episodes e JOIN podcasts p ON p.id = e.podcast_id "
        "WHERE p.slug = audio_fingerprint_matches.podcast_slug "
        "AND e.episode_id = audio_fingerprint_matches.episode_id)").rowcount


def delete_matches_for_podcast(conn, podcast_slug: str) -> int:
    """Delete every match row of a feed on conn, without committing."""
    return conn.execute(
        "DELETE FROM audio_fingerprint_matches WHERE podcast_slug = ?", (podcast_slug,)).rowcount


def move_matches(conn, from_pattern_id: int, to_pattern_id: int) -> int:
    """Re-point match rows from one pattern to another on conn, without committing."""
    return conn.execute(
        "UPDATE audio_fingerprint_matches SET pattern_id = ? WHERE pattern_id = ?",
        (to_pattern_id, from_pattern_id)).rowcount
