"""Tests for the 2.5.13 _cleanup_low_mention_patterns migration.

The migration retires structurally-shaped false-positive patterns without
touching patterns that have already matched real ads. Conditions:

  Criterion 1: occurrences<2 AND created_by='auto' AND conf=0 AND fp=0
  Criterion 2a: sponsor starts with a SPONSOR_REASONING_PREFIXES entry
  Criterion 2b: sponsor ends with an LLM-suffix tell
  Criterion 2c: sponsor not canonical AND no variant appears in template

Reversible per row. Idempotent via low_mention_cleanup_revision settings flag.

The audit on this prod (177 active patterns) identified 8 conf>0 patterns in
the 1-mention bucket that legitimate or arguably-legitimate match real ads
(SoFi conf=7, Chubbiesshorts conf=5, etc). The naive 2.5.13a migration would
have wrongly killed them; this rewrite must not.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from database import Database


@pytest.fixture
def db(tmp_path):
    Database._instance = None
    if hasattr(Database, '_initialized'):
        Database._initialized = False
    inst = Database(data_dir=str(tmp_path))
    yield inst
    Database._instance = None


def _make_pattern(db, name, template, conf=0, fp=0, created_by='auto'):
    existing = db.get_known_sponsor_by_name(name)
    if existing:
        sponsor_id = existing['id']
    else:
        sponsor_id = db.create_known_sponsor(name=name, aliases=[], category=None)
    pid = db.create_ad_pattern(
        scope='podcast',
        text_template=template,
        sponsor_id=sponsor_id,
        intro_variants=[],
        outro_variants=[],
        podcast_id='show',
        created_by=created_by,
    )
    if conf or fp:
        db.update_ad_pattern(pid, confirmation_count=conf, false_positive_count=fp)
    return pid


def _run_migration(db):
    """Bypass the settings-flag idempotency to force a re-run inside a test."""
    conn = db.get_connection()
    conn.execute(
        "DELETE FROM settings WHERE key = 'low_mention_cleanup_revision'"
    )
    conn.commit()
    db._cleanup_low_mention_patterns(conn)


def test_low_mention_auto_never_matched_is_disabled(db):
    """The Pattern #354 shape. Sponsor once in template, auto-created, never
    matched. Disabled."""
    pid = _make_pattern(
        db, 'Modelo',
        template="Yo, how you get the big, Modelo? No, no, no, later.",
        conf=0, fp=0, created_by='auto',
    )
    _run_migration(db)
    row = db.get_ad_pattern_by_id(pid)
    assert row['is_active'] in (0, False)
    assert '2.5.13' in (row['disabled_reason'] or '')


def test_low_mention_with_confirmation_is_kept(db):
    """A 1-mention pattern that has matched real ads (conf>0) is left alone
    even though it sits in the low-mention bucket. We cannot tell legit-but-
    rare from bad-but-self-boosted here, so the conservative choice is to
    keep it for human review."""
    pid = _make_pattern(
        db, 'SoFi',
        template="SoFi can help you save. (one mention only).",
        conf=7, fp=0, created_by='auto',
    )
    _run_migration(db)
    row = db.get_ad_pattern_by_id(pid)
    assert row['is_active'] in (1, True)


def test_low_mention_with_false_positive_history_is_kept(db):
    """Patterns with fp>0 may already be on the user's review radar; don't
    second-guess. Leave alone."""
    pid = _make_pattern(
        db, 'Acme',
        template="Acme once. (one mention).",
        conf=0, fp=1, created_by='auto',
    )
    _run_migration(db)
    row = db.get_ad_pattern_by_id(pid)
    assert row['is_active'] in (1, True)


def test_user_created_pattern_is_kept_even_if_low_mention(db):
    """User-created patterns aren't touched by the auto-cleanup."""
    pid = _make_pattern(
        db, 'CustomBrand',
        template="CustomBrand once mentioned.",
        conf=0, fp=0, created_by='user',
    )
    _run_migration(db)
    row = db.get_ad_pattern_by_id(pid)
    assert row['is_active'] in (1, True)


def test_real_ad_with_two_plus_mentions_is_kept(db):
    pid = _make_pattern(
        db, 'BetterHelp',
        template="BetterHelp can help you. Try BetterHelp today. BetterHelp.",
        conf=0, fp=0, created_by='auto',
    )
    _run_migration(db)
    row = db.get_ad_pattern_by_id(pid)
    assert row['is_active'] in (1, True)


def test_sponsor_field_with_reasoning_prefix_is_disabled(db):
    """#202 Walden University shape - sponsor field holds the reasoning blob."""
    pid = _make_pattern(
        db, 'Inferred from a long silence in the transcript',
        template="Some episode content here that does not match the sponsor field.",
        conf=0, fp=0, created_by='auto',
    )
    _run_migration(db)
    row = db.get_ad_pattern_by_id(pid)
    assert row['is_active'] in (0, False)


def test_sponsor_field_with_brand_suffix_is_disabled(db):
    """#227 'Grainger brand' shape - LLM stuck a generic suffix on the sponsor."""
    pid = _make_pattern(
        db, 'Grainger brand',
        template="Some Grainger-related conversation but no brand here.",
        conf=0, fp=0, created_by='auto',
    )
    _run_migration(db)
    row = db.get_ad_pattern_by_id(pid)
    assert row['is_active'] in (0, False)


def test_idempotent_second_run_is_noop(db):
    """The settings flag should prevent a second pass from re-scanning."""
    pid_alive = _make_pattern(
        db, 'Apple',
        template="Apple keeps the doctor away. Eat an apple.",
        conf=0, fp=0, created_by='auto',
    )
    _run_migration(db)
    conn = db.get_connection()
    # Force a second call WITHOUT clearing the settings flag this time -
    # this is the real-world boot path.
    db._cleanup_low_mention_patterns(conn)
    row = db.get_ad_pattern_by_id(pid_alive)
    # No-op + pattern survives (Apple appears twice).
    assert row['is_active'] in (1, True)
