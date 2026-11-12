"""Merge, dedupe and delete carry a pattern's audio history, with foreign keys off to prove it is explicit."""
import pytest
from tests.fingerprint_match_helpers import record_fingerprint_match

SLUG = 'example-podcast'


@pytest.fixture
def db(temp_db):
    conn = temp_db.get_connection()
    conn.commit()
    conn.execute('PRAGMA foreign_keys = OFF')
    yield temp_db
    conn.execute('PRAGMA foreign_keys = ON')


def _pattern(db, created_by='auto', confirmations=0, text='buy now today', scope='podcast'):
    conn = db.get_connection()
    pid = conn.execute(
        "INSERT INTO ad_patterns (scope, podcast_id, text_template, created_by, confirmation_count) "
        "VALUES (?, ?, ?, ?, ?)", (scope, SLUG, text, created_by, confirmations)).lastrowid
    conn.commit()
    return pid


def _audio(db, pid, matches=2, source='ep1'):
    db.create_audio_fingerprint(pid, b'AQAA', 6.0, source_podcast_slug=SLUG, source_episode_id=source,
                                source_start=2.0, source_end=8.0)
    for i in range(matches):
        record_fingerprint_match(db, pid, f'ep{i}', SLUG, 10.0, 40.0, 0.9, 0.8, True, False)


def _count(db, pid):
    return db.count_fingerprint_matches(pid)


def test_dedupe_moves_donor_history_and_source_to_keeper(db):
    keep = _pattern(db, 'user', 1)
    donor = _pattern(db, 'auto', 0)
    _audio(db, donor, matches=3)
    db.set_fingerprint_enabled(donor, False)
    db.deduplicate_patterns()
    row = db.get_audio_fingerprint(keep)
    assert row is not None and row['enabled'] == 0 and row['source_episode_id'] == 'ep1'
    assert _count(db, keep) == 3 and _count(db, donor) == 0


def test_dedupe_keeper_with_audio_keeps_its_own_history_and_drops_losers(db):
    keep = _pattern(db, 'user', 1)
    loser = _pattern(db, 'auto', 0)
    _audio(db, keep, matches=1)
    _audio(db, loser, matches=4)
    db.deduplicate_patterns()
    assert _count(db, keep) == 1 and _count(db, loser) == 0


def test_dedupe_does_not_rebuild_audio_the_user_deleted(db):
    keep = _pattern(db, 'user', 1)
    donor = _pattern(db, 'auto', 0)
    db.set_pattern_audio_removed(keep, True)
    _audio(db, donor, matches=2)
    db.deduplicate_patterns()
    assert db.get_audio_fingerprint(keep) is None
    assert _count(db, keep) == 0 and _count(db, donor) == 0


def test_dedupe_disabled_keeper_gets_no_history(db):
    keep = _pattern(db, 'user', 1)
    donor = _pattern(db, 'auto', 0)
    db.get_connection().execute('UPDATE ad_patterns SET is_active = 0 WHERE id = ?', (keep,))
    db.get_connection().commit()
    _audio(db, donor, matches=2)
    db.deduplicate_patterns()
    assert db.get_audio_fingerprint(keep) is None and _count(db, keep) == 0


def test_delete_pattern_removes_history(db):
    pid = _pattern(db)
    _audio(db, pid)
    other = _pattern(db, text='another read entirely')
    _audio(db, other, matches=1)
    assert db.delete_ad_pattern(pid)
    assert _count(db, pid) == 0 and _count(db, other) == 1


def test_bulk_delete_removes_history(db):
    a, b, keep = _pattern(db, text='read a'), _pattern(db, text='read b'), _pattern(db, text='read c')
    for pid in (a, b, keep):
        _audio(db, pid)
    with db.transaction() as conn:
        db._bulk_delete_patterns_conn(conn, [a, b])
    assert (_count(db, a), _count(db, b), _count(db, keep)) == (0, 0, 2)


def test_delete_community_patterns_removes_history(db):
    community = _pattern(db, text='community read')
    local = _pattern(db, text='local read')
    db.get_connection().execute("UPDATE ad_patterns SET source = 'community' WHERE id = ?", (community,))
    db.get_connection().commit()
    _audio(db, community)
    _audio(db, local)
    assert db.delete_all_community_patterns() == 1
    assert _count(db, community) == 0 and _count(db, local) == 2


def test_delete_podcast_removes_history_of_its_patterns_from_other_feeds(db):
    db.create_podcast(SLUG, 'https://example.com/feed.xml', 'Example')
    pid = _pattern(db)
    glob = _pattern(db, text='global read', scope='global')
    _audio(db, pid)
    record_fingerprint_match(db, pid, 'x1', 'other-feed', 1.0, 9.0, 0.9, 0.8, True, False)
    _audio(db, glob, matches=1)
    db.delete_podcast(SLUG)
    assert _count(db, pid) == 0 and _count(db, glob) == 0
