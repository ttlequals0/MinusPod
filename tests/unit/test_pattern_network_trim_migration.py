"""Padded pattern network ids are trimmed and blank ones cleared, once."""
from database import Database

GATE = 'trim_pattern_network_ids'


def _pattern(db, network):
    pid = db.create_ad_pattern(scope='network', text_template=f'text {network!r}')
    db.get_connection().execute("UPDATE ad_patterns SET network_id = ? WHERE id = ?", (network, pid))
    db.get_connection().commit()
    return pid


def _network(db, pid):
    return db.get_connection().execute(
        "SELECT network_id FROM ad_patterns WHERE id = ?", (pid,)).fetchone()['network_id']


def _reset_gate(db):
    db.get_connection().execute("DELETE FROM schema_migrations WHERE name = ?", (GATE,))
    db.get_connection().commit()


def test_padded_ids_are_trimmed_and_blank_ones_cleared(temp_db):
    padded, blank, tabs, clean, none = (
        _pattern(temp_db, ' net1 '), _pattern(temp_db, '   '), _pattern(temp_db, '\tnet2\n'),
        _pattern(temp_db, 'net3'), _pattern(temp_db, None))
    _reset_gate(temp_db)
    temp_db._run_trim_pattern_network_ids(temp_db.get_connection())
    assert [_network(temp_db, p) for p in (padded, blank, tabs, clean, none)] == [
        'net1', None, 'net2', 'net3', None]


def test_unicode_whitespace_is_stripped_like_python(temp_db):
    nbsp, wide, plain = (
        _pattern(temp_db, '\u00a0net1\u00a0'), _pattern(temp_db, '\u3000\u2003'), _pattern(temp_db, 'a\u00a0b'))
    _reset_gate(temp_db)
    temp_db._run_trim_pattern_network_ids(temp_db.get_connection())
    assert [_network(temp_db, p) for p in (nbsp, wide, plain)] == ['net1', None, 'a\u00a0b']


def test_other_columns_are_untouched(temp_db):
    pid = _pattern(temp_db, ' net1 ')
    before = dict(temp_db.get_ad_pattern_by_id(pid))
    _reset_gate(temp_db)
    temp_db._run_trim_pattern_network_ids(temp_db.get_connection())
    after = dict(temp_db.get_ad_pattern_by_id(pid))
    assert {k: v for k, v in after.items() if k != 'network_id'} == {
        k: v for k, v in before.items() if k != 'network_id'}


def test_the_step_runs_once(temp_db):
    _reset_gate(temp_db)
    temp_db._run_trim_pattern_network_ids(temp_db.get_connection())
    pid = _pattern(temp_db, ' late ')
    temp_db._run_trim_pattern_network_ids(temp_db.get_connection())
    assert _network(temp_db, pid) == ' late '


def test_a_failure_never_raises_and_retries_next_start(temp_db):
    conn = temp_db.get_connection()
    _reset_gate(temp_db)
    conn.execute("DROP TABLE ad_patterns")
    conn.commit()
    temp_db._run_trim_pattern_network_ids(conn)
    assert conn.execute("SELECT 1 FROM schema_migrations WHERE name = ?", (GATE,)).fetchone() is None


def test_second_start_changes_nothing(tmp_path):
    path = str(tmp_path / 'p.db')
    Database._instance = None
    db = Database(path)
    padded, blank = _pattern(db, ' net1 '), _pattern(db, '  ')
    _reset_gate(db)
    Database._instance = None
    first = Database(path)
    assert (_network(first, padded), _network(first, blank)) == ('net1', None)
    first.get_connection().execute("UPDATE ad_patterns SET network_id = ' kept ' WHERE id = ?", (padded,))
    first.get_connection().commit()
    Database._instance = None
    second = Database(path)
    assert _network(second, padded) == ' kept '
    Database._instance = None
