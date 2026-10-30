"""The youtube_drop_private_placeholders step removes only bare-id YouTube placeholders."""


def _add(conn, pid, eid, status, title, vid, desc=None):
    conn.execute(
        "INSERT INTO episodes (podcast_id, episode_id, original_url, status, title, "
        "youtube_video_id, description) VALUES (?, ?, 'https://example.com/x', ?, ?, ?, ?)",
        (pid, eid, status, title, vid, desc))


def _ids(conn):
    return {r[0] for r in conn.execute("SELECT episode_id FROM episodes")}


def test_drops_only_placeholder_rows_and_runs_once(temp_db):
    temp_db.create_podcast('yt-feed', 'https://example.com/yt', 'YT')
    temp_db.create_podcast('rss-feed', 'https://example.com/rss', 'RSS')
    conn = temp_db.get_connection()
    yt = temp_db.get_podcast_by_slug('yt-feed')['id']
    rss = temp_db.get_podcast_by_slug('rss-feed')['id']
    conn.execute("UPDATE podcasts SET feed_type = 'youtube' WHERE id = ?", (yt,))
    _add(conn, yt, 'placeholder01', 'discovered', 'abcdefghijk', 'abcdefghijk')
    _add(conn, yt, 'emptydesc001', 'discovered', 'bcdefghijkl', 'bcdefghijkl', '')
    _add(conn, yt, 'realtitle001', 'discovered', 'A real title', 'cdefghijklm')
    _add(conn, yt, 'withdesc0001', 'discovered', 'defghijklmn', 'defghijklmn', 'text')
    _add(conn, yt, 'processed001', 'processed', 'efghijklmno', 'efghijklmno')
    _add(conn, rss, 'rssepisode01', 'discovered', 'fghijklmnop', 'fghijklmnop')
    temp_db.index_episodes([('placeholder01', 'yt-feed'), ('realtitle001', 'yt-feed')])
    conn.execute("DELETE FROM schema_migrations WHERE name = 'youtube_drop_private_placeholders'")
    conn.commit()

    temp_db._run_schema_migrations()

    assert _ids(conn) == {'realtitle001', 'withdesc0001', 'processed001', 'rssepisode01'}
    indexed = {r[0] for r in conn.execute("SELECT content_id FROM search_index WHERE content_type = 'episode'")}
    assert 'placeholder01' not in indexed and 'realtitle001' in indexed
    assert conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name = 'youtube_drop_private_placeholders'"
    ).fetchone()

    _add(conn, yt, 'placeholder02', 'discovered', 'ghijklmnopq', 'ghijklmnopq')
    conn.commit()
    temp_db._run_schema_migrations()
    assert 'placeholder02' in _ids(conn)


def test_drops_failed_placeholders_only(temp_db):
    temp_db.create_podcast('yt-feed', 'https://example.com/yt', 'YT')
    conn = temp_db.get_connection()
    yt = temp_db.get_podcast_by_slug('yt-feed')['id']
    conn.execute("UPDATE podcasts SET feed_type = 'youtube' WHERE id = ?", (yt,))
    _add(conn, yt, 'failedholder1', 'permanently_failed', 'abcdefghijk', 'abcdefghijk')
    _add(conn, yt, 'failedholder2', 'failed', 'bcdefghijkl', 'bcdefghijkl')
    _add(conn, yt, 'failedreal001', 'permanently_failed', 'A real title', 'cdefghijklm')
    conn.execute("DELETE FROM schema_migrations WHERE name = 'youtube_drop_private_placeholders_failed'")
    conn.commit()

    temp_db._run_schema_migrations()

    assert _ids(conn) == {'failedreal001'}
