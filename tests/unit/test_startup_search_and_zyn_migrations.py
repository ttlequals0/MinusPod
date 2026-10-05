import json

from database import schema


ZYN_GATE = 'cleanup_zyn_ad_markers_once'


def _seed_episode(db, slug, episode_id, marker):
    db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    db.upsert_episode(
        slug=slug,
        episode_id=episode_id,
        original_url=f'https://example.com/{episode_id}.mp3',
        title='Test episode',
        original_duration=60,
    )
    db.save_episode_details(slug, episode_id, ad_markers=[marker])
    conn = db.get_connection()
    conn.execute(
        "UPDATE episode_details SET original_transcript_text = ? WHERE episode_id = "
        "(SELECT id FROM episodes WHERE episode_id = ?)",
        ('[00:00:00.000 --> 00:01:00.000] unrelated transcript', episode_id),
    )
    conn.commit()


def _zyn_marker():
    return {'start': 1.0, 'end': 10.0, 'sponsor': 'Zyn', 'reason': 'Zyn promotion'}


def _run_zyn_cleanup(db):
    db._cleanup_zyn_ad_markers(db.get_connection())


def test_zyn_cleanup_sets_gate_without_changes_and_runs_once(temp_db, monkeypatch):
    _seed_episode(temp_db, 'zyn-once', 'episode-once', _zyn_marker())
    conn = temp_db.get_connection()
    conn.execute("DELETE FROM schema_migrations WHERE name = ?", (ZYN_GATE,))
    conn.commit()
    monkeypatch.setattr(schema, 'extract_text_in_range', lambda *_: 'Zyn mentioned')

    _run_zyn_cleanup(temp_db)

    assert conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name = ?", (ZYN_GATE,)
    ).fetchone() is not None
    temp_db.save_episode_details('zyn-once', 'episode-once', ad_markers=[_zyn_marker()])
    monkeypatch.setattr(schema, 'extract_text_in_range', lambda *_: 'unrelated')
    _run_zyn_cleanup(temp_db)

    row = conn.execute(
        "SELECT ad_markers_json FROM episode_details WHERE episode_id = "
        "(SELECT id FROM episodes WHERE episode_id = ?)", ('episode-once',)
    ).fetchone()
    assert json.loads(row['ad_markers_json'])[0]['sponsor'] == 'Zyn'


def test_zyn_cleanup_rolls_back_and_retries_after_failure(temp_db, monkeypatch):
    _seed_episode(temp_db, 'zyn-retry-a', 'episode-a', _zyn_marker())
    _seed_episode(temp_db, 'zyn-retry-b', 'episode-b', _zyn_marker())
    conn = temp_db.get_connection()
    conn.execute("DELETE FROM schema_migrations WHERE name = ?", (ZYN_GATE,))
    conn.commit()
    calls = 0

    def fail_after_first_update(*_):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('transcript read failed')
        return 'unrelated'

    monkeypatch.setattr(schema, 'extract_text_in_range', fail_after_first_update)
    _run_zyn_cleanup(temp_db)

    assert conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name = ?", (ZYN_GATE,)
    ).fetchone() is None
    rows = conn.execute(
        "SELECT ad_markers_json FROM episode_details ORDER BY episode_id"
    ).fetchall()
    assert all(json.loads(row['ad_markers_json'])[0]['sponsor'] == 'Zyn' for row in rows)

    monkeypatch.setattr(schema, 'extract_text_in_range', lambda *_: 'unrelated')
    _run_zyn_cleanup(temp_db)

    assert conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name = ?", (ZYN_GATE,)
    ).fetchone() is not None
    rows = conn.execute(
        "SELECT ad_markers_json FROM episode_details ORDER BY episode_id"
    ).fetchall()
    assert all(json.loads(row['ad_markers_json'])[0]['sponsor'] is None for row in rows)


def test_zyn_cleanup_does_not_rollback_caller_transaction(temp_db, monkeypatch):
    conn = temp_db.get_connection()
    conn.execute("DELETE FROM schema_migrations WHERE name = ?", (ZYN_GATE,))
    conn.commit()
    conn.execute(
        "INSERT INTO settings (key, value, is_default) VALUES (?, ?, 0)",
        ('pending-caller-write', 'preserved'),
    )
    monkeypatch.setattr(schema, 'extract_text_in_range', lambda *_: 'unrelated')

    _run_zyn_cleanup(temp_db)

    assert conn.in_transaction
    assert conn.execute(
        "SELECT value FROM settings WHERE key = ?", ('pending-caller-write',)
    ).fetchone()['value'] == 'preserved'
    conn.rollback()


def test_empty_search_index_is_populated_at_startup(temp_db, monkeypatch):
    temp_db.create_podcast('search-empty', 'https://example.com/feed.xml', 'Search')
    temp_db.upsert_episode(
        slug='search-empty',
        episode_id='search-episode',
        original_url='https://example.com/episode.mp3',
        title='Searchable startup episode',
        original_duration=60,
    )
    conn = temp_db.get_connection()
    conn.execute("DELETE FROM search_index")
    conn.commit()
    rebuild = temp_db.rebuild_search_index
    calls = 0

    def count_rebuilds():
        nonlocal calls
        calls += 1
        return rebuild()

    monkeypatch.setattr(temp_db, 'rebuild_search_index', count_rebuilds)
    temp_db._run_schema_migrations()

    assert calls == 1
    assert conn.execute("SELECT 1 FROM search_index LIMIT 1").fetchone() is not None


def test_nonempty_search_index_is_not_rebuilt_at_startup(temp_db, monkeypatch):
    temp_db.create_podcast('search-present', 'https://example.com/feed.xml', 'Search')
    temp_db.upsert_episode(
        slug='search-present',
        episode_id='search-episode',
        original_url='https://example.com/episode.mp3',
        title='Searchable startup episode',
        original_duration=60,
    )
    temp_db.rebuild_search_index()
    conn = temp_db.get_connection()
    assert conn.execute("SELECT 1 FROM search_index LIMIT 1").fetchone() is not None

    calls = 0

    def unexpected_rebuild():
        nonlocal calls
        calls += 1
        return 0

    monkeypatch.setattr(temp_db, 'rebuild_search_index', unexpected_rebuild)
    temp_db._run_schema_migrations()

    assert calls == 0
    assert conn.execute("SELECT 1 FROM search_index LIMIT 1").fetchone() is not None
