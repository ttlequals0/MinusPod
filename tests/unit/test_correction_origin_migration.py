"""pattern_corrections.origin: backfill of pass-2 auto-filed confirms, the
readers that key on it, and survival through the sponsor FK table rebuild."""
from tests.unit.test_migration_sponsor_fk import _rebuild_pre_migration_shape

GATE = 'backfill_correction_origin_once'
AUTO_SNIPPET = 'auto-approved: pass-2 corroborated differential_uncorroborated hold'


def _seed(temp_db, slug='origin-test', episode_id='a1b2c3d4e5f6', transcript=None):
    temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Origin Test')
    temp_db.upsert_episode(slug=slug, episode_id=episode_id,
                           original_url='https://example.com/ep.mp3',
                           title='Test Episode', original_duration=3600.0)
    if transcript:
        temp_db.save_episode_details(slug, episode_id, transcript_text=transcript)
    return temp_db.get_podcast_by_slug(slug)['id'], episode_id


def _confirm(temp_db, podcast_id, episode_id, start, end, **kwargs):
    return temp_db.create_pattern_correction(
        correction_type='confirm', episode_id=episode_id, podcast_id=podcast_id,
        original_bounds={'start': start, 'end': end}, **kwargs)


def _row(temp_db, correction_id):
    return dict(temp_db.get_connection().execute(
        "SELECT origin, source_hold_reason, text_snippet FROM pattern_corrections "
        "WHERE id = ?", (correction_id,)).fetchone())


def _run(temp_db):
    conn = temp_db.get_connection()
    conn.execute("DELETE FROM schema_migrations WHERE name = ?", (GATE,))
    conn.commit()
    temp_db._backfill_correction_origin(conn)


def test_fresh_db_defaults_origin_to_user(temp_db):
    podcast_id, eid = _seed(temp_db)
    cid = _confirm(temp_db, podcast_id, eid, 100.0, 200.0)
    assert _row(temp_db, cid)['origin'] == 'user'


def test_backfill_marks_prefixed_confirms_auto_pass2(temp_db):
    podcast_id, eid = _seed(temp_db)
    auto = _confirm(temp_db, podcast_id, eid, 100.0, 200.0, text_snippet=AUTO_SNIPPET)
    user = _confirm(temp_db, podcast_id, eid, 300.0, 400.0, text_snippet='user text')

    _run(temp_db)

    assert _row(temp_db, auto) == {
        'origin': 'auto_pass2',
        'source_hold_reason': 'differential_uncorroborated',
        'text_snippet': AUTO_SNIPPET,
    }
    assert _row(temp_db, user)['origin'] == 'user'
    assert temp_db.get_connection().execute(
        "SELECT 1 FROM schema_migrations WHERE name = ?", (GATE,)).fetchone()


def test_backfill_keeps_an_existing_source_hold_reason(temp_db):
    podcast_id, eid = _seed(temp_db)
    cid = _confirm(temp_db, podcast_id, eid, 100.0, 200.0, text_snippet=AUTO_SNIPPET,
                   source_hold_reason='estimated_pattern_bounds')

    _run(temp_db)

    assert _row(temp_db, cid)['source_hold_reason'] == 'estimated_pattern_bounds'


def test_backfill_is_idempotent(temp_db):
    podcast_id, eid = _seed(temp_db)
    cid = _confirm(temp_db, podcast_id, eid, 100.0, 200.0, text_snippet=AUTO_SNIPPET)

    _run(temp_db)
    first = _row(temp_db, cid)
    _run(temp_db)
    temp_db._backfill_correction_origin(temp_db.get_connection())

    assert _row(temp_db, cid) == first


def test_sponsor_fk_rebuild_keeps_origin(temp_db):
    conn = temp_db.get_connection()
    _rebuild_pre_migration_shape(conn)
    conn.execute("INSERT INTO ad_patterns (scope, text_template, sponsor) "
                 "VALUES ('global', 'ad text', 'Acme')")
    conn.execute("ALTER TABLE pattern_corrections ADD COLUMN origin "
                 "TEXT NOT NULL DEFAULT 'user'")
    conn.execute("INSERT INTO pattern_corrections (pattern_id, correction_type, origin) "
                 "VALUES (1, 'confirm', 'auto_pass2')")
    conn.commit()

    temp_db._migrate_sponsor_fk(conn)

    assert conn.execute(
        "SELECT origin FROM pattern_corrections").fetchone()['origin'] == 'auto_pass2'


def test_reader_uses_origin_column_not_snippet(temp_db):
    podcast_id, eid = _seed(temp_db)
    _confirm(temp_db, podcast_id, eid, 100.0, 200.0, text_snippet='no prefix',
             origin='auto_pass2', source_hold_reason='differential_uncorroborated')
    _confirm(temp_db, podcast_id, eid, 300.0, 400.0, text_snippet=AUTO_SNIPPET)

    by_start = {c['start']: c for c in temp_db.get_confirmed_corrections(podcast_id, eid)}

    assert by_start[100.0]['auto_filed'] is True
    assert by_start[100.0]['hold_reason'] == 'differential_uncorroborated'
    assert not by_start[300.0].get('auto_filed')
    assert 'hold_reason' not in by_start[300.0]


def test_episode_corrections_expose_origin(temp_db):
    podcast_id, eid = _seed(temp_db)
    _confirm(temp_db, podcast_id, eid, 100.0, 200.0, origin='auto_pass2')
    _confirm(temp_db, podcast_id, eid, 300.0, 400.0)

    origins = {c['original_bounds']['start']: c['origin']
               for c in temp_db.get_episode_corrections(podcast_id, eid)}

    assert origins == {100.0: 'auto_pass2', 300.0: 'user'}


def test_prior_skips_auto_filed_confirms(temp_db):
    podcast_id, eid = _seed(temp_db, slug='prior-test')
    _confirm(temp_db, podcast_id, eid, 100.0, 200.0, origin='auto_pass2')
    _confirm(temp_db, podcast_id, eid, 300.0, 400.0)
    temp_db.create_pattern_correction(
        correction_type='false_positive', episode_id=eid, podcast_id=podcast_id,
        original_bounds={'start': 500.0, 'end': 600.0})

    rows = temp_db.get_podcast_corrections_for_prior('prior-test', [eid])

    assert sorted((r['correction_type'], r['start']) for r in rows) == [
        ('confirm', 300.0), ('false_positive', 500.0)]


def test_pattern_backfill_skips_auto_filed_confirms(temp_db):
    line = 'this episode is brought to you by acme widgets, visit example.com today'
    transcript = (f"[00:01:40.000 --> 00:03:20.000] {line}\n"
                  f"[00:05:00.000 --> 00:06:40.000] {line} for a second offer")
    podcast_id, eid = _seed(temp_db, transcript=transcript)
    auto = _confirm(temp_db, podcast_id, eid, 100.0, 200.0, origin='auto_pass2')
    user = _confirm(temp_db, podcast_id, eid, 300.0, 400.0)

    assert temp_db.backfill_patterns_from_corrections() == 1

    conn = temp_db.get_connection()
    pattern_ids = {r['id']: r['pattern_id'] for r in conn.execute(
        "SELECT id, pattern_id FROM pattern_corrections").fetchall()}
    assert pattern_ids[auto] is None
    assert pattern_ids[user] is not None
