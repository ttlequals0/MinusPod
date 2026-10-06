"""mark_action_from_ad_chapters_v1: fold the retired global/per-feed ad
chapter enable and category toggles into the 'mark' segment action."""
import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

GATE = 'mark_action_from_ad_chapters_v1'


def _run(temp_db):
    """Clear the gate the boot-time run set, then run the migration."""
    conn = temp_db.get_connection()
    conn.execute("DELETE FROM schema_migrations WHERE name = ?", (GATE,))
    conn.commit()
    temp_db._run_mark_action_from_ad_chapters_migration(conn)
    return conn


def _global_actions(temp_db):
    return json.loads(temp_db.get_setting('segment_category_actions') or '{}')


def _feed_actions(temp_db, slug):
    row = temp_db.get_podcast_by_slug(slug)
    raw = row['segment_category_actions']
    return json.loads(raw) if raw else {}


def test_global_on_with_sponsor_keep_and_ticked_becomes_mark(temp_db):
    temp_db.set_setting('ad_chapters_enabled', 'true', is_default=False)
    temp_db.set_setting('ad_chapter_categories', json.dumps({'sponsor': True}), is_default=False)
    temp_db.set_setting('segment_category_actions', json.dumps({'sponsor': 'keep'}), is_default=False)

    _run(temp_db)

    assert _global_actions(temp_db)['sponsor'] == 'mark'


def test_global_off_leaves_keep(temp_db):
    temp_db.set_setting('ad_chapters_enabled', 'false', is_default=False)
    temp_db.set_setting('ad_chapter_categories', json.dumps({'sponsor': True}), is_default=False)
    temp_db.set_setting('segment_category_actions', json.dumps({'sponsor': 'keep'}), is_default=False)

    _run(temp_db)

    assert _global_actions(temp_db)['sponsor'] == 'keep'


def test_feed_override_on_with_global_off_marks_only_that_feed(temp_db):
    """Review focus 2: ad chapters globally off, one feed override on."""
    temp_db.set_setting('ad_chapters_enabled', 'false', is_default=False)
    temp_db.set_setting('segment_category_actions', json.dumps({'sponsor': 'keep'}), is_default=False)
    temp_db.create_podcast('feed-override-on', 'https://example.com/a.xml', 'Feed Override On')
    temp_db.update_podcast('feed-override-on', ad_chapters_enabled_override='on')
    temp_db.create_podcast('feed-plain', 'https://example.com/b.xml', 'Feed Plain')

    _run(temp_db)

    assert _global_actions(temp_db)['sponsor'] == 'keep'
    assert _feed_actions(temp_db, 'feed-override-on')['sponsor'] == 'mark'
    assert _feed_actions(temp_db, 'feed-plain') == {}


def test_feed_with_chapters_off_while_global_on_gets_explicit_keep_override(temp_db):
    temp_db.set_setting('ad_chapters_enabled', 'true', is_default=False)
    temp_db.set_setting('segment_category_actions', json.dumps({'sponsor': 'keep'}), is_default=False)
    temp_db.create_podcast('feed-chapters-off', 'https://example.com/c.xml', 'Feed Chapters Off')
    temp_db.update_podcast('feed-chapters-off', ad_chapters_enabled_override='off')

    _run(temp_db)

    assert _global_actions(temp_db)['sponsor'] == 'mark'
    assert _feed_actions(temp_db, 'feed-chapters-off')['sponsor'] == 'keep'


def test_category_already_remove_is_untouched(temp_db):
    temp_db.set_setting('ad_chapters_enabled', 'true', is_default=False)
    temp_db.set_setting('segment_category_actions', json.dumps({'sponsor': 'keep'}), is_default=False)
    temp_db.create_podcast('feed-already-remove', 'https://example.com/d.xml', 'Feed Already Remove')
    temp_db.update_podcast('feed-already-remove',
                           segment_category_actions=json.dumps({'sponsor': 'remove'}))

    _run(temp_db)

    assert _global_actions(temp_db)['sponsor'] == 'mark'
    assert _feed_actions(temp_db, 'feed-already-remove')['sponsor'] == 'remove'


def test_fresh_db_is_a_noop(temp_db):
    """The gate is already set from boot-time init, and the retired settings
    were never seeded (their SettingSpecs are gone), so nothing to promote."""
    conn = temp_db.get_connection()
    gate = conn.execute(
        "SELECT 1 FROM schema_migrations WHERE name = ?", (GATE,)).fetchone()
    assert gate is not None
    assert temp_db.get_setting('ad_chapters_enabled') is None
    assert temp_db.get_setting('ad_chapter_categories') is None
    assert _global_actions(temp_db) == {}


def test_second_run_is_a_noop(temp_db):
    temp_db.set_setting('ad_chapters_enabled', 'true', is_default=False)
    temp_db.set_setting('segment_category_actions', json.dumps({'sponsor': 'keep'}), is_default=False)

    conn = _run(temp_db)
    gate = conn.execute("SELECT 1 FROM schema_migrations WHERE name = ?", (GATE,)).fetchone()
    assert gate is not None
    assert _global_actions(temp_db)['sponsor'] == 'mark'

    # A second call (gate still set) must not pick up a fresh promotion candidate.
    temp_db.set_setting('ad_chapter_categories', json.dumps({'cross_promo': True}), is_default=False)
    conn.execute(
        "UPDATE settings SET value = ? WHERE key = 'segment_category_actions'",
        (json.dumps({'sponsor': 'mark', 'cross_promo': 'keep'}),))
    conn.commit()
    temp_db._run_mark_action_from_ad_chapters_migration(conn)

    assert _global_actions(temp_db)['cross_promo'] == 'keep'


def test_counts_are_logged(temp_db, caplog):
    temp_db.set_setting('ad_chapters_enabled', 'true', is_default=False)
    temp_db.set_setting('segment_category_actions', json.dumps({'sponsor': 'keep'}), is_default=False)
    temp_db.create_podcast('feed-logged', 'https://example.com/e.xml', 'Feed Logged')
    temp_db.update_podcast('feed-logged', ad_chapters_enabled_override='off')

    with caplog.at_level(logging.INFO):
        _run(temp_db)

    messages = [r.getMessage() for r in caplog.records]
    assert any('Migration: promoted 1' in m and 'keep override on 1 feed' in m
               for m in messages)
