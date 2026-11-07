"""One-shot repair of split first-piece rows that recorded the whole pre-split span (#967)."""
import json

from tests.unit.db_test_utils import _seed

GATE = 'split_piece_original_bounds_967'


def _insert(temp_db, kind, podcast_id, eid, orig, new=None):
    def d(b):
        return {'start': b[0], 'end': b[1]} if b else None
    return temp_db.create_pattern_correction(
        correction_type=kind, episode_id=eid, podcast_id=podcast_id,
        original_bounds=d(orig), corrected_bounds=d(new))


def _original(conn, cid):
    return json.loads(conn.execute(
        "SELECT original_bounds FROM pattern_corrections WHERE id = ?", (cid,)).fetchone()[0])


def _pending(conn, eid):
    return conn.execute(
        "SELECT pending_recut_at FROM episodes WHERE episode_id = ?", (eid,)).fetchone()[0]


def _run(temp_db):
    conn = temp_db.get_connection()
    conn.execute("DELETE FROM schema_migrations WHERE name = ?", (GATE,))
    conn.commit()
    temp_db._run_split_piece_bounds_repair(conn)
    return conn


def test_two_piece_split_row_is_rewritten_and_episode_marked(temp_db):
    pid, eid = _seed(temp_db)
    conn = temp_db.get_connection()
    first = _insert(temp_db, 'boundary_adjustment', pid, eid, (1000, 1400), (1000, 1150))
    _insert(temp_db, 'create', pid, eid, None, (1150, 1400))
    assert _pending(conn, eid) is None

    _run(temp_db)

    assert _original(conn, first) == {'start': 1000, 'end': 1150}
    assert _pending(conn, eid) is not None


def test_three_piece_split_row_is_rewritten(temp_db):
    pid, eid = _seed(temp_db)
    conn = temp_db.get_connection()
    first = _insert(temp_db, 'boundary_adjustment', pid, eid, (1000, 1400), (1000, 1100))
    _insert(temp_db, 'create', pid, eid, None, (1100, 1250))
    _insert(temp_db, 'create', pid, eid, None, (1250, 1400))

    _run(temp_db)

    assert _original(conn, first) == {'start': 1000, 'end': 1100}
    assert _pending(conn, eid) is not None


def test_unrelated_later_create_at_original_end_is_still_rewritten(temp_db):
    pid, eid = _seed(temp_db)
    conn = temp_db.get_connection()
    first = _insert(temp_db, 'boundary_adjustment', pid, eid, (1000, 1400), (1000, 1150))
    _insert(temp_db, 'create', pid, eid, None, (1150, 1400))
    _insert(temp_db, 'create', pid, eid, None, (1400, 1500))

    _run(temp_db)

    assert _original(conn, first) == {'start': 1000, 'end': 1150}


def test_chain_stops_at_first_non_create_row(temp_db):
    pid, eid = _seed(temp_db)
    conn = temp_db.get_connection()
    first = _insert(temp_db, 'boundary_adjustment', pid, eid, (1000, 1400), (1000, 1150))
    _insert(temp_db, 'confirm', pid, eid, (1150, 1300))
    _insert(temp_db, 'create', pid, eid, None, (1150, 1400))

    _run(temp_db)

    assert _original(conn, first) == {'start': 1000, 'end': 1400}


def test_genuine_trim_is_untouched(temp_db):
    pid, eid = _seed(temp_db)
    conn = temp_db.get_connection()
    trim = _insert(temp_db, 'boundary_adjustment', pid, eid, (1000, 1400), (1000, 1150))

    _run(temp_db)

    assert _original(conn, trim) == {'start': 1000, 'end': 1400}
    assert _pending(conn, eid) is None


def test_partial_chain_is_untouched(temp_db):
    pid, eid = _seed(temp_db)
    conn = temp_db.get_connection()
    row = _insert(temp_db, 'boundary_adjustment', pid, eid, (1000, 1400), (1000, 1150))
    _insert(temp_db, 'create', pid, eid, None, (1150, 1300))

    _run(temp_db)

    assert _original(conn, row) == {'start': 1000, 'end': 1400}
    assert _pending(conn, eid) is None


def test_second_run_is_a_no_op(temp_db):
    pid, eid = _seed(temp_db)
    conn = temp_db.get_connection()
    first = _insert(temp_db, 'boundary_adjustment', pid, eid, (1000, 1400), (1000, 1150))
    _insert(temp_db, 'create', pid, eid, None, (1150, 1400))
    _run(temp_db)
    conn.execute("UPDATE pattern_corrections SET original_bounds = ? WHERE id = ?",
                 (json.dumps({'start': 1000, 'end': 1400}), first))
    conn.commit()

    temp_db._run_split_piece_bounds_repair(conn)

    assert _original(conn, first) == {'start': 1000, 'end': 1400}


def test_create_filed_much_later_is_not_a_split_piece(temp_db):
    pid, eid = _seed(temp_db)
    conn = temp_db.get_connection()
    trim = _insert(temp_db, 'boundary_adjustment', pid, eid, (1000, 1400), (1000, 1150))
    late = _insert(temp_db, 'create', pid, eid, None, (1150, 1400))
    conn.execute("UPDATE pattern_corrections SET created_at = ? WHERE id = ?",
                 ('2099-01-01T01:00:00Z', late))
    conn.commit()

    _run(temp_db)

    assert _original(conn, trim) == {'start': 1000, 'end': 1400}
    assert _pending(conn, eid) is None


def test_orphaned_split_row_is_untouched(temp_db):
    pid, eid = _seed(temp_db)
    conn = temp_db.get_connection()
    first = _insert(temp_db, 'boundary_adjustment', pid, eid, (1000, 1400), (1000, 1150))
    _insert(temp_db, 'create', pid, eid, None, (1150, 1400))
    conn.execute("UPDATE pattern_corrections SET podcast_id = NULL")
    conn.commit()

    _run(temp_db)

    assert _original(conn, first) == {'start': 1000, 'end': 1400}
    assert _pending(conn, eid) is None
