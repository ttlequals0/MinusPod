"""Tests for the auto-split guard in _resolve_or_create_pattern_from_text
(issue #563). Manual correction paths (confirm/adjust with no pattern_id)
call db.create_ad_pattern directly, bypassing the guards in
create_pattern_from_ad -- this is how oversized multi-sponsor patterns
formed. The fix runs the resolved text through split_template_text and
creates one pattern per sponsor segment instead of one oversized pattern.
"""
import logging

from api.patterns import _resolve_or_create_pattern_from_text
from pattern_service import PatternService
from text_pattern_matcher import MAX_PATTERN_CHARS


SLUG = 'resolve-test'
EPISODE_ID = 'abcdef012345'

TWO_SPONSOR_TEXT = (
    "This episode is brought to you by Acme. Acme provides the best "
    "widgets around, visit acme dot com for twenty percent off today. "
    "This episode is sponsored by Widgetco. Widgetco has amazing "
    "deals this week, check out widgetco dot com right now for savings."
)

THREE_SPONSOR_TEXT = (
    "This episode is brought to you by Acme. Acme provides the best "
    "widgets around, visit acme dot com for twenty percent off today. "
    "This episode is sponsored by Widgetco. Widgetco has amazing "
    "deals this week, check out widgetco dot com right now for savings. "
    "Thanks to Spanso for supporting the show, go check out spanso dot "
    "com slash podcast for a free trial of their new gadget service."
)

SINGLE_SPONSOR_TEXT = (
    "This episode is brought to you by Acme, makers of fine widgets "
    "since 1999, visit acme dot com today for a special discount code."
)


def _seed(temp_db, slug=SLUG, episode_id=EPISODE_ID):
    temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Resolve Test')
    temp_db.upsert_episode(
        slug=slug, episode_id=episode_id,
        original_url='https://example.com/ep.mp3',
        title='Test Episode', original_duration=3600.0,
    )
    return slug, episode_id


def test_single_sponsor_text_creates_one_pattern_unchanged(temp_db):
    slug, episode_id = _seed(temp_db)
    svc = PatternService(temp_db)

    primary_id, all_ids = _resolve_or_create_pattern_from_text(
        temp_db, svc, slug, episode_id, SINGLE_SPONSOR_TEXT,
        {'sponsor': 'Acme'}, label='confirmed',
    )

    assert all_ids == [primary_id]
    pattern = temp_db.get_ad_pattern_by_id(primary_id)
    assert pattern['text_template'] == SINGLE_SPONSOR_TEXT
    assert pattern['sponsor'] == 'Acme'


def test_three_sponsor_text_splits_into_three_patterns_primary_matches_sponsor(temp_db):
    slug, episode_id = _seed(temp_db)
    svc = PatternService(temp_db)

    primary_id, all_ids = _resolve_or_create_pattern_from_text(
        temp_db, svc, slug, episode_id, THREE_SPONSOR_TEXT,
        {'sponsor': 'Widgetco'}, label='confirmed',
    )

    assert len(all_ids) == 3
    primary = temp_db.get_ad_pattern_by_id(primary_id)
    assert primary['sponsor'] == 'Widgetco'

    sponsors = {temp_db.get_ad_pattern_by_id(i)['sponsor'] for i in all_ids}
    assert sponsors == {'Acme', 'Widgetco', 'Spanso'}


def test_adjust_path_also_splits(temp_db):
    slug, episode_id = _seed(temp_db)
    svc = PatternService(temp_db)

    primary_id, all_ids = _resolve_or_create_pattern_from_text(
        temp_db, svc, slug, episode_id, TWO_SPONSOR_TEXT,
        {'sponsor': 'Acme'}, label='adjusted',
    )

    assert len(all_ids) == 2
    primary = temp_db.get_ad_pattern_by_id(primary_id)
    assert primary['sponsor'] == 'Acme'


def test_no_sponsor_matched_segment_falls_back_to_first_created(temp_db):
    slug, episode_id = _seed(temp_db)
    svc = PatternService(temp_db)

    # original_ad has no sponsor and label='adjusted' skips reason lookup,
    # so extract_sponsor_from_text(ad_text) resolves the caller-side sponsor;
    # it may not exactly match either segment's guessed sponsor casing.
    primary_id, all_ids = _resolve_or_create_pattern_from_text(
        temp_db, svc, slug, episode_id, TWO_SPONSOR_TEXT,
        {}, label='adjusted',
    )
    assert primary_id in all_ids
    assert primary_id == all_ids[0]


def test_dedupe_against_existing_pattern_reused_for_segment(temp_db):
    slug, episode_id = _seed(temp_db)
    svc = PatternService(temp_db)

    # Pre-create a pattern matching the Acme segment's exact text so the
    # per-segment dedupe check reuses it instead of creating a duplicate.
    from text_pattern_matcher import split_template_text
    segments = split_template_text(TWO_SPONSOR_TEXT)
    acme_segment = next(s for s in segments if s['sponsor'] == 'Acme')
    existing_id = temp_db.create_ad_pattern(
        scope='podcast', text_template=acme_segment['text'],
        podcast_id=slug,
    )

    primary_id, all_ids = _resolve_or_create_pattern_from_text(
        temp_db, svc, slug, episode_id, TWO_SPONSOR_TEXT,
        {'sponsor': 'Acme'}, label='confirmed',
    )

    assert existing_id in all_ids
    assert len(all_ids) == 2
    # No duplicate row for the Acme segment's exact text.
    dup = temp_db.get_connection().execute(
        "SELECT COUNT(*) as c FROM ad_patterns WHERE text_template = ?",
        (acme_segment['text'],),
    ).fetchone()
    assert dup['c'] == 1


def test_oversized_segment_is_skipped_with_warning(temp_db, caplog):
    slug, episode_id = _seed(temp_db)
    svc = PatternService(temp_db)

    oversized_filler = "blah blah blah filler content here. " * 120
    text = (
        f"This episode is brought to you by Acme. {oversized_filler}"
        f"Thanks to Widgetco for sponsoring too, check widgetco dot com "
        f"out today for their amazing new gadget deals available everywhere."
    )
    assert len(text) > MAX_PATTERN_CHARS

    with caplog.at_level(logging.WARNING):
        primary_id, all_ids = _resolve_or_create_pattern_from_text(
            temp_db, svc, slug, episode_id, text,
            {'sponsor': 'Acme'}, label='confirmed',
        )

    assert len(all_ids) == 1
    only = temp_db.get_ad_pattern_by_id(all_ids[0])
    assert only['sponsor'] == 'Widgetco'
    assert any('MAX_PATTERN_CHARS' in r.message or 'exceeds' in r.message
               for r in caplog.records)
