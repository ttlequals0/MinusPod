"""YouTube feed and SponsorBlock state queries."""
SB_COLUMNS = frozenset({
    'youtube_video_id', 'sb_state', 'sb_next_check_at', 'sb_attempts',
    'sb_segment_uuids', 'sb_last_error', 'sb_note', 'sb_submissions',
})


def _check_sb_columns(fields) -> None:
    unknown = set(fields) - SB_COLUMNS
    if unknown:
        raise ValueError(f"not a SponsorBlock column: {', '.join(sorted(unknown))}")


class YouTubeMixin:
    def update_episode_sponsorblock(self, slug: str, episode_id: str, **fields) -> bool:
        _check_sb_columns(fields)
        if not fields:
            return False
        db_id = self._get_episode_db_id(slug, episode_id)
        if not db_id:
            return False
        return self.update_episode_sponsorblock_by_pk(db_id, **fields)

    def update_episode_sponsorblock_by_pk(self, db_id: int, **fields) -> bool:
        _check_sb_columns(fields)
        if not fields:
            return False
        assignments = ', '.join(f"{col} = ?" for col in fields)
        conn = self.get_connection()
        conn.execute(
            f"UPDATE episodes SET {assignments}, "  # noqa: S608
            "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ?",
            (*fields.values(), db_id))
        conn.commit()
        return True

    def schedule_sponsorblock_wait(self, slug: str, episode_id: str, due_iso: str) -> bool:
        """Arm the first SponsorBlock check only for a row with no state yet."""
        db_id = self._get_episode_db_id(slug, episode_id)
        if not db_id:
            return False
        conn = self.get_connection()
        cur = conn.execute(
            "UPDATE episodes SET sb_state = 'waiting', sb_attempts = 0, sb_next_check_at = ?, "
            "updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = ? AND sb_state IS NULL",
            (due_iso, db_id))
        conn.commit()
        return cur.rowcount > 0

    def disarm_sponsorblock_watch(self, slug: str, episode_id: str) -> bool:
        """Clear the next check so the watcher does not race a manual run; the run re-arms it."""
        db_id = self._get_episode_db_id(slug, episode_id)
        if not db_id:
            return False
        conn = self.get_connection()
        cur = conn.execute(
            "UPDATE episodes SET sb_next_check_at = NULL WHERE id = ? AND sb_next_check_at IS NOT NULL", (db_id,))
        conn.commit()
        return cur.rowcount > 0

    def count_sponsorblock_waiting(self) -> int:
        row = self.get_connection().execute(
            "SELECT COUNT(*) FROM episodes WHERE sb_state IN ('waiting', 'probing')").fetchone()
        return row[0]

    def get_due_sponsorblock_episodes(self, now_iso: str, limit: int = 50) -> list[dict]:
        cursor = self.get_connection().execute(
            """SELECT e.id, e.episode_id, e.title, e.original_url, e.published_at, e.description,
                      e.status, e.youtube_video_id, e.sb_state, e.sb_attempts,
                      e.sb_next_check_at, e.sb_segment_uuids, e.podcast_id,
                      e.original_duration, e.created_at, p.slug
               FROM episodes e JOIN podcasts p ON p.id = e.podcast_id
               WHERE p.feed_type = 'youtube' AND e.sb_next_check_at IS NOT NULL
                     AND e.sb_next_check_at <= ?
               ORDER BY e.sb_next_check_at ASC LIMIT ?""",
            (now_iso, limit))
        return [dict(row) for row in cursor.fetchall()]

    def get_youtube_episodes_missing_metadata(self, limit: int) -> list[dict]:
        cursor = self.get_connection().execute(
            """SELECT e.id, e.episode_id, e.youtube_video_id, e.original_url, e.published_at,
                      e.description, p.slug
               FROM episodes e JOIN podcasts p ON p.id = e.podcast_id
               WHERE p.feed_type = 'youtube' AND e.yt_metadata_at IS NULL
               ORDER BY e.published_at DESC, e.id DESC LIMIT ?""",
            (limit,))
        return [dict(row) for row in cursor.fetchall()]

    def count_youtube_feeds(self) -> int:
        row = self.get_connection().execute(
            "SELECT COUNT(*) AS n FROM podcasts WHERE feed_type = 'youtube'").fetchone()
        return int(row['n']) if row else 0
