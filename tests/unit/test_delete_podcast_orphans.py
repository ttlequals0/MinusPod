"""Deleting a feed leaves nothing behind that points at its slug.

Most child tables cascade from podcasts(id). ad_patterns, ad_reviewer_log,
and addressing_log hold the slug as plain TEXT with no foreign key, and
audio_fingerprints and pattern_corrections hang off ad_patterns without one
either, so they have to be cleaned up by hand. Slugs get reused when a feed
is re-added, which is what makes a surviving podcast-scoped pattern a
correctness problem and not just clutter: the next feed to take the slug
inherits another show's learning.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from database import Database  # noqa: E402


SLUG = 'orphan-test'


@pytest.fixture
def db(tmp_path):
    # Database is a singleton, so each test needs a fresh one or it inherits
    # the previous test's rows.
    Database._instance = None
    yield Database(str(tmp_path / 'test.db'))
    Database._instance = None


def _add_feed(db, slug=SLUG):
    return db.create_podcast(slug=slug, source_url='https://example.com/rss.xml',
                             title='Orphan Test')


def _add_pattern(db, slug, scope='podcast'):
    """A pattern plus the fingerprint and correction that hang off it."""
    conn = db.get_connection()
    pattern_id = conn.execute(
        "INSERT INTO ad_patterns (scope, podcast_id, text_template) VALUES (?, ?, 'buy now')",
        (scope, slug),
    ).lastrowid
    conn.execute(
        "INSERT INTO audio_fingerprints (pattern_id, fingerprint, duration) "
        "VALUES (?, X'00', 1.0)", (pattern_id,))
    conn.execute(
        "INSERT INTO pattern_corrections (pattern_id, correction_type) "
        "VALUES (?, 'confirm')", (pattern_id,))
    conn.commit()
    return pattern_id


def _add_reviewer_log(db, slug):
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO ad_reviewer_log (episode_id, podcast_id, pass, pool, "
        "original_start, original_end, verdict, model_used, success) "
        "VALUES ('ep1', ?, 1, 'p', 0.0, 1.0, 'keep', 'm', 1)", (slug,))
    conn.commit()


def _add_addressing_log(db, slug):
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO addressing_log (podcast_slug, episode_id, pass_name, "
        "configured_mode, effective_mode, windows_judged, windows_compliant) "
        "VALUES (?, 'ep1', 'detection', 'random', 'timestamps', 3, 2)", (slug,))
    conn.commit()


def _count(db, sql, params=()):
    return db.get_connection().execute(sql, params).fetchone()[0]


def test_podcast_scoped_patterns_do_not_survive_the_feed(db):
    _add_feed(db)
    _add_pattern(db, SLUG)

    db.delete_podcast(SLUG)

    assert _count(db, "SELECT COUNT(*) FROM ad_patterns WHERE podcast_id = ?", (SLUG,)) == 0


def test_rows_hanging_off_a_deleted_pattern_go_with_it(db):
    _add_feed(db)
    pattern_id = _add_pattern(db, SLUG)

    db.delete_podcast(SLUG)

    assert _count(db, "SELECT COUNT(*) FROM audio_fingerprints WHERE pattern_id = ?",
                  (pattern_id,)) == 0
    assert _count(db, "SELECT COUNT(*) FROM pattern_corrections WHERE pattern_id = ?",
                  (pattern_id,)) == 0


def test_reviewer_log_rows_do_not_survive_the_feed(db):
    _add_feed(db)
    _add_reviewer_log(db, SLUG)

    db.delete_podcast(SLUG)

    assert _count(db, "SELECT COUNT(*) FROM ad_reviewer_log WHERE podcast_id = ?",
                  (SLUG,)) == 0


def test_addressing_log_rows_do_not_survive_the_feed(db):
    _add_feed(db)
    _add_addressing_log(db, SLUG)

    db.delete_podcast(SLUG)

    assert _count(db, "SELECT COUNT(*) FROM addressing_log WHERE podcast_slug = ?",
                  (SLUG,)) == 0


def test_a_re_added_feed_does_not_inherit_the_old_one_s_patterns(db):
    _add_feed(db)
    _add_pattern(db, SLUG)
    db.delete_podcast(SLUG)

    _add_feed(db)  # same slug, as re-adding a feed produces

    assert db.get_ad_patterns(podcast_id=SLUG) == []


@pytest.mark.parametrize('scope', ['global', 'network'])
def test_wider_scoped_patterns_keep_their_learning_but_lose_the_dead_slug(db, scope):
    # podcast_id on these only records where the pattern was first seen. The
    # pattern still applies to other feeds, so detach rather than delete.
    _add_feed(db)
    pattern_id = _add_pattern(db, SLUG, scope=scope)

    db.delete_podcast(SLUG)

    row = db.get_connection().execute(
        "SELECT podcast_id FROM ad_patterns WHERE id = ?", (pattern_id,)).fetchone()
    assert row is not None, f'{scope} pattern should survive the feed'
    assert row[0] is None, f'{scope} pattern should not still point at a deleted slug'


def test_another_feeds_patterns_are_untouched(db):
    _add_feed(db)
    _add_feed(db, slug='other-feed')
    _add_pattern(db, SLUG)
    keeper = _add_pattern(db, 'other-feed')
    _add_reviewer_log(db, 'other-feed')
    _add_addressing_log(db, 'other-feed')

    db.delete_podcast(SLUG)

    assert _count(db, "SELECT COUNT(*) FROM ad_patterns WHERE id = ?", (keeper,)) == 1
    assert _count(db, "SELECT COUNT(*) FROM audio_fingerprints WHERE pattern_id = ?",
                  (keeper,)) == 1
    assert _count(db, "SELECT COUNT(*) FROM ad_reviewer_log WHERE podcast_id = ?",
                  ('other-feed',)) == 1
    assert _count(db, "SELECT COUNT(*) FROM addressing_log WHERE podcast_slug = ?",
                  ('other-feed',)) == 1


def test_episodes_still_cascade(db):
    podcast_id = _add_feed(db)
    conn = db.get_connection()
    conn.execute("INSERT INTO episodes (podcast_id, episode_id, title, original_url) "
                 "VALUES (?, 'ep1', 'E1', 'https://e/1.mp3')", (podcast_id,))
    conn.commit()

    db.delete_podcast(SLUG)

    assert _count(db, "SELECT COUNT(*) FROM episodes WHERE podcast_id = ?",
                  (podcast_id,)) == 0


def test_deleting_a_feed_that_does_not_exist_reports_nothing_deleted(db):
    assert db.delete_podcast('never-existed') is False


def test_delete_ad_pattern_takes_its_fingerprint(db):
    _add_feed(db)
    pattern_id = _add_pattern(db, SLUG)

    assert db.delete_ad_pattern(pattern_id) is True

    assert db.get_audio_fingerprint(pattern_id) is None


def test_bulk_delete_patterns_takes_their_fingerprints(db):
    _add_feed(db)
    first = _add_pattern(db, SLUG)
    second = _add_pattern(db, SLUG)

    assert db.bulk_delete_patterns([first, second]) == 2

    assert db.get_audio_fingerprint(first) is None
    assert db.get_audio_fingerprint(second) is None
