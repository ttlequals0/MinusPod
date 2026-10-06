"""mark_action_from_ad_chapters_v1: fold the retired global/per-feed ad
chapter enable and category toggles into the 'mark' segment action."""
import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from config import SEGMENT_CATEGORIES, DEFAULT_SEGMENT_ACTION  # noqa: E402

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


def _full_actions(**overrides):
    """A full SEGMENT_CATEGORIES map: DEFAULT_SEGMENT_ACTION everywhere,
    overridden per kwarg. The written global setting is always this shape."""
    full = {cat: DEFAULT_SEGMENT_ACTION for cat in SEGMENT_CATEGORIES}
    full.update(overrides)
    return full


def _feed_raw(temp_db, slug):
    return temp_db.get_podcast_by_slug(slug)['segment_category_actions']


def _feed_actions(temp_db, slug):
    raw = _feed_raw(temp_db, slug)
    return json.loads(raw) if raw else {}


def _set_legacy_override(temp_db, slug, ad_chapters_enabled_override=None,
                         ad_chapter_categories_override=None):
    """Seed the retired ad_chapters_enabled_override/ad_chapter_categories_override
    columns directly: update_podcast's allowlist no longer carries them."""
    conn = temp_db.get_connection()
    conn.execute(
        "UPDATE podcasts SET "
        "ad_chapters_enabled_override = coalesce(?, ad_chapters_enabled_override), "
        "ad_chapter_categories_override = coalesce(?, ad_chapter_categories_override) "
        "WHERE slug = ?",
        (ad_chapters_enabled_override, ad_chapter_categories_override, slug))
    conn.commit()


def test_global_on_with_sponsor_keep_and_ticked_becomes_mark(temp_db):
    temp_db.set_setting('ad_chapters_enabled', 'true', is_default=False)
    temp_db.set_setting('ad_chapter_categories', json.dumps({'sponsor': True}), is_default=False)
    temp_db.set_setting('segment_category_actions',
                        json.dumps({'sponsor': 'keep', 'cross_promo': 'beep'}), is_default=False)

    _run(temp_db)

    assert _global_actions(temp_db) == _full_actions(sponsor='mark', cross_promo='beep')


def test_global_off_leaves_keep(temp_db):
    temp_db.set_setting('ad_chapters_enabled', 'false', is_default=False)
    temp_db.set_setting('ad_chapter_categories', json.dumps({'sponsor': True}), is_default=False)
    temp_db.set_setting('segment_category_actions',
                        json.dumps({'sponsor': 'keep', 'cross_promo': 'beep'}), is_default=False)

    _run(temp_db)

    # No promotion means no rewrite: the setting stays exactly as stored.
    assert _global_actions(temp_db) == {'sponsor': 'keep', 'cross_promo': 'beep'}


def test_feed_override_on_with_global_off_marks_only_that_feed(temp_db):
    """Ad chapters globally off, one feed override on."""
    temp_db.set_setting('ad_chapters_enabled', 'false', is_default=False)
    temp_db.set_setting('segment_category_actions',
                        json.dumps({'sponsor': 'keep', 'cross_promo': 'beep'}), is_default=False)
    temp_db.create_podcast('feed-override-on', 'https://example.com/a.xml', 'Feed Override On')
    _set_legacy_override(temp_db, 'feed-override-on', ad_chapters_enabled_override='on')
    temp_db.create_podcast('feed-plain', 'https://example.com/b.xml', 'Feed Plain')

    _run(temp_db)

    assert _global_actions(temp_db) == {'sponsor': 'keep', 'cross_promo': 'beep'}
    assert _feed_actions(temp_db, 'feed-override-on') == {'sponsor': 'mark'}
    assert _feed_actions(temp_db, 'feed-plain') == {}


def test_feed_with_chapters_off_while_global_on_gets_explicit_keep_override(temp_db):
    temp_db.set_setting('ad_chapters_enabled', 'true', is_default=False)
    temp_db.set_setting('segment_category_actions',
                        json.dumps({'sponsor': 'keep', 'cross_promo': 'beep'}), is_default=False)
    temp_db.create_podcast('feed-chapters-off', 'https://example.com/c.xml', 'Feed Chapters Off')
    _set_legacy_override(temp_db, 'feed-chapters-off', ad_chapters_enabled_override='off')

    _run(temp_db)

    assert _global_actions(temp_db) == _full_actions(sponsor='mark', cross_promo='beep')
    assert _feed_actions(temp_db, 'feed-chapters-off') == {'sponsor': 'keep'}


def test_category_already_remove_is_untouched(temp_db):
    temp_db.set_setting('ad_chapters_enabled', 'true', is_default=False)
    temp_db.set_setting('segment_category_actions',
                        json.dumps({'sponsor': 'keep', 'cross_promo': 'beep'}), is_default=False)
    temp_db.create_podcast('feed-already-remove', 'https://example.com/d.xml', 'Feed Already Remove')
    temp_db.update_podcast('feed-already-remove',
                           segment_category_actions=json.dumps({'sponsor': 'remove', 'intro': 'beep'}))

    _run(temp_db)

    assert _global_actions(temp_db) == _full_actions(sponsor='mark', cross_promo='beep')
    # Own override already pins both categories; left exactly as stored.
    assert _feed_actions(temp_db, 'feed-already-remove') == {'sponsor': 'remove', 'intro': 'beep'}


def test_feed_with_own_keep_override_gets_explicit_mark_when_chaptered(temp_db):
    """has_own_action branch: a feed's own 'keep' pin would otherwise block
    inheriting the promoted global action, so it is rewritten to 'mark'."""
    temp_db.set_setting('ad_chapters_enabled', 'true', is_default=False)
    temp_db.set_setting('segment_category_actions', json.dumps({'sponsor': 'keep'}), is_default=False)
    temp_db.create_podcast('feed-own-keep', 'https://example.com/f.xml', 'Feed Own Keep')
    temp_db.update_podcast('feed-own-keep',
                           segment_category_actions=json.dumps({'sponsor': 'keep'}))

    _run(temp_db)

    assert _feed_actions(temp_db, 'feed-own-keep') == {'sponsor': 'mark'}


def test_feed_category_override_off_while_global_promotes_gets_explicit_keep(temp_db):
    temp_db.set_setting('ad_chapters_enabled', 'true', is_default=False)
    temp_db.set_setting('segment_category_actions', json.dumps({'sponsor': 'keep'}), is_default=False)
    temp_db.create_podcast('feed-cat-off', 'https://example.com/g.xml', 'Feed Cat Off')
    _set_legacy_override(temp_db, 'feed-cat-off',
                         ad_chapter_categories_override=json.dumps({'sponsor': False}))

    _run(temp_db)

    assert _feed_actions(temp_db, 'feed-cat-off') == {'sponsor': 'keep'}


def test_malformed_global_segment_category_actions_does_not_block_other_feeds(temp_db):
    temp_db.set_setting('ad_chapters_enabled', 'true', is_default=False)
    temp_db.set_setting('segment_category_actions', 'not valid json {{{', is_default=False)
    temp_db.create_podcast('feed-survives-a', 'https://example.com/h.xml', 'Feed Survives A')
    temp_db.update_podcast('feed-survives-a',
                           segment_category_actions=json.dumps({'sponsor': 'keep'}))
    _set_legacy_override(temp_db, 'feed-survives-a', ad_chapters_enabled_override='on')

    _run(temp_db)

    # Malformed global JSON falls back to defaults (no category is 'keep'),
    # so nothing is promoted and the stored string is left exactly as-is.
    assert temp_db.get_setting('segment_category_actions') == 'not valid json {{{'
    assert _feed_actions(temp_db, 'feed-survives-a') == {'sponsor': 'mark'}


def test_malformed_global_ad_chapter_categories_falls_back_to_default(temp_db):
    temp_db.set_setting('ad_chapters_enabled', 'true', is_default=False)
    temp_db.set_setting('segment_category_actions', json.dumps({'sponsor': 'keep'}), is_default=False)
    temp_db.set_setting('ad_chapter_categories', '{broken json', is_default=False)
    temp_db.create_podcast('feed-plain', 'https://example.com/i.xml', 'Feed Plain')

    _run(temp_db)

    # Falls back to the legacy default map (sponsor/cross_promo True), same
    # as if ad_chapter_categories had never been set.
    assert _global_actions(temp_db) == _full_actions(sponsor='mark')
    assert _feed_actions(temp_db, 'feed-plain') == {}


def test_malformed_feed_segment_category_actions_replaced_only_when_an_override_is_needed(
        temp_db, caplog):
    temp_db.set_setting('ad_chapters_enabled', 'true', is_default=False)
    temp_db.set_setting('segment_category_actions', json.dumps({'sponsor': 'keep'}), is_default=False)
    temp_db.create_podcast('feed-needs-override', 'https://example.com/j.xml', 'Feed Needs Override')
    temp_db.update_podcast('feed-needs-override',
                           segment_category_actions='{garbage not json')
    _set_legacy_override(temp_db, 'feed-needs-override', ad_chapters_enabled_override='off')
    temp_db.create_podcast('feed-no-override-needed', 'https://example.com/k.xml', 'Feed No Override')
    temp_db.update_podcast('feed-no-override-needed', segment_category_actions='also garbage')

    with caplog.at_level(logging.WARNING):
        _run(temp_db)

    # Chapters effectively off while the category is promoted globally:
    # the malformed override is replaced with an explicit keep.
    assert _feed_actions(temp_db, 'feed-needs-override') == {'sponsor': 'keep'}
    warnings = [r.getMessage() for r in caplog.records if r.levelname == 'WARNING']
    assert any('feed-needs-override' in m for m in warnings)
    assert not any('feed-no-override-needed' in m for m in warnings)

    # No override needed here (pure inheritance already yields mark), so the
    # malformed string is left exactly as stored.
    assert _feed_raw(temp_db, 'feed-no-override-needed') == 'also garbage'


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
    _set_legacy_override(temp_db, 'feed-logged', ad_chapters_enabled_override='off')

    with caplog.at_level(logging.INFO):
        _run(temp_db)

    messages = [r.getMessage() for r in caplog.records]
    assert any('Migration: promoted 1' in m and 'keep override on 1 feed' in m
               for m in messages)
