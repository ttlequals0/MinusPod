"""pending_holds: derived index of held, uncut markers kept in step with ad_markers_json."""
import json

import pytest

from config import ALL_HOLD_REASONS, is_pending_review
import config

SLUG = 'holds-feed'

HELD = {'start': 10.0, 'end': 40.0, 'held_for_review': True, 'was_cut': False,
        'hold_reason': 'max_duration'}
HELD_CUT = {'start': 50.0, 'end': 80.0, 'held_for_review': True, 'was_cut': True,
            'hold_reason': 'no_cue_evidence'}
PLAIN = {'start': 100.0, 'end': 130.0, 'was_cut': True}


@pytest.fixture
def db(temp_db):
    temp_db.create_podcast(SLUG, 'https://example.com/feed.xml', title='Holds Feed')
    for ep in ('ep-1', 'ep-2'):
        temp_db.upsert_episode(SLUG, ep, original_url=f'https://example.com/{ep}.mp3',
                               title=ep, status='processed')
    return temp_db


def _rows(db, episode_id=None):
    sql = ("SELECT h.marker_start, h.marker_end, h.hold_reason, e.episode_id "
           "FROM pending_holds h JOIN episodes e ON e.id = h.episode_pk")
    params = ()
    if episode_id:
        sql += " WHERE e.episode_id = ?"
        params = (episode_id,)
    return [dict(r) for r in db.get_connection().execute(sql + " ORDER BY h.marker_start", params)]


def test_save_indexes_only_pending_holds(db):
    db.save_episode_details(SLUG, 'ep-1', ad_markers=[HELD, HELD_CUT, PLAIN])
    assert _rows(db) == [{'marker_start': 10.0, 'marker_end': 40.0,
                          'hold_reason': 'max_duration', 'episode_id': 'ep-1'}]


def test_resave_without_hold_clears_rows(db):
    db.save_episode_details(SLUG, 'ep-1', ad_markers=[HELD])
    db.save_episode_details(SLUG, 'ep-1', ad_markers=[{**HELD, 'was_cut': True}])
    assert _rows(db) == []


def test_save_without_markers_leaves_rows(db):
    db.save_episode_details(SLUG, 'ep-1', ad_markers=[HELD])
    db.save_episode_details(SLUG, 'ep-1', transcript_text='hello')
    assert len(_rows(db)) == 1


@pytest.mark.parametrize('clear', [
    lambda db: db.clear_episode_details(SLUG, 'ep-1'),
    lambda db: db.clear_episode_ad_data(SLUG, 'ep-1'),
    lambda db: db.batch_clear_episode_details(SLUG, ['ep-1']),
    lambda db: db.batch_clear_episode_ad_data(SLUG, ['ep-1']),
])
def test_clear_paths_drop_rows(db, clear):
    db.save_episode_details(SLUG, 'ep-1', ad_markers=[HELD])
    db.save_episode_details(SLUG, 'ep-2', ad_markers=[HELD])
    clear(db)
    assert _rows(db, 'ep-1') == []
    assert len(_rows(db, 'ep-2')) == 1


def test_episode_and_podcast_deletion_drop_rows(db):
    class _Storage:
        def cleanup_episode_files(self, *a):
            pass

        def remove_episode_artwork(self, *a):
            pass

    db.save_episode_details(SLUG, 'ep-1', ad_markers=[HELD])
    db.save_episode_details(SLUG, 'ep-2', ad_markers=[HELD])
    db.delete_episode_rows(SLUG, ['ep-1'], _Storage())
    assert [r['episode_id'] for r in _rows(db)] == ['ep-2']
    db.delete_podcast(SLUG)
    assert _rows(db) == []


def test_backfill_rebuilds_from_stored_json(db):
    conn = db.get_connection()
    db.save_episode_details(SLUG, 'ep-1', ad_markers=[HELD, PLAIN])
    db.save_episode_details(SLUG, 'ep-2', ad_markers=[HELD_CUT])
    conn.execute("DELETE FROM pending_holds")
    conn.execute(
        "UPDATE episode_details SET ad_markers_json = 'not json' WHERE episode_id = "
        "(SELECT id FROM episodes WHERE episode_id = 'ep-2')")
    conn.execute("DELETE FROM schema_migrations WHERE name = 'backfill_pending_holds_once'")
    conn.commit()
    db._backfill_pending_holds(conn)
    assert [(r['episode_id'], r['hold_reason']) for r in _rows(db)] == [('ep-1', 'max_duration')]
    assert conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name = 'backfill_pending_holds_once'").fetchone()
    # Gated: a second call does not duplicate rows.
    db._backfill_pending_holds(conn)
    assert len(_rows(db)) == 1


VARIANTS = [
    HELD, HELD_CUT, PLAIN,
    {'start': 1.0, 'end': 2.0, 'held_for_review': True},
    {'start': 3.0, 'end': 4.0, 'held_for_review': False, 'was_cut': False},
    {'start': 5.0, 'end': 6.0, 'held_for_review': True, 'was_cut': False},
    {'start': 7.0, 'end': 8.0, 'held_for_review': True, 'was_cut': False,
     'hold_reason': 'verification_miss'},
    {'start': 9.0, 'end': 9.5, 'held_for_review': 1, 'was_cut': 0,
     'hold_reason': 'reviewer_failed'},
]


def test_membership_matches_is_pending_review(db):
    db.save_episode_details(SLUG, 'ep-1', ad_markers=VARIANTS + ['not a dict'])
    expected = sorted((m['start'], m['end'], m.get('hold_reason'))
                      for m in VARIANTS if is_pending_review(m))
    got = sorted((r['marker_start'], r['marker_end'], r['hold_reason']) for r in _rows(db))
    assert got == expected


def test_count_pending_holds_by_reason(db):
    db.save_episode_details(SLUG, 'ep-1', ad_markers=VARIANTS)
    db.save_episode_details(SLUG, 'ep-2', ad_markers=[HELD])
    assert db.count_pending_holds_by_reason() == {
        'max_duration': 2, 'verification_miss': 1, 'reviewer_failed': 1,
    }


def test_count_pending_holds_by_reason_scoped_to_feed(db):
    db.create_podcast('other-feed', 'https://example.com/other.xml', title='Other Feed')
    db.upsert_episode('other-feed', 'ep-9', original_url='https://example.com/ep-9.mp3',
                      title='ep-9', status='processed')
    db.save_episode_details(SLUG, 'ep-1', ad_markers=[HELD])
    db.save_episode_details('other-feed', 'ep-9', ad_markers=[HELD, VARIANTS[6]])
    assert db.count_pending_holds_by_reason(SLUG) == {'max_duration': 1}
    assert db.count_pending_holds_by_reason('other-feed') == {
        'max_duration': 1, 'verification_miss': 1}
    assert db.count_pending_holds_by_reason() == {
        'max_duration': 2, 'verification_miss': 1}


def test_all_hold_reasons_covers_every_constant():
    constants = {v for k, v in vars(config).items() if k.startswith('HOLD_REASON_')}
    assert ALL_HOLD_REASONS == constants


def test_marker_json_is_untouched(db):
    db.save_episode_details(SLUG, 'ep-1', ad_markers=[HELD, PLAIN])
    stored = db.get_connection().execute(
        "SELECT ad_markers_json FROM episode_details").fetchone()[0]
    assert json.loads(stored) == [HELD, PLAIN]
