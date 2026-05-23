"""Tests for the 2.5.7 multi-sponsor guard on PatternService.merge_similar_patterns.

If the combined template would name >=1 sponsor outside the patterns'
consolidated sponsor row, the merge is aborted to prevent kitchen-sink
templates from over-matching downstream.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from database import Database
from pattern_service import PatternService


@pytest.fixture
def db(tmp_path):
    Database._instance = None
    if hasattr(Database, '_initialized'):
        Database._initialized = False
    instance = Database(data_dir=str(tmp_path))
    yield instance
    Database._instance = None


def _seed_sponsor(db, name):
    existing = db.get_known_sponsor_by_name(name)
    if existing:
        return existing['id']
    return db.create_known_sponsor(name=name, aliases=[], category=None)


def _seed_pattern(db, sponsor_id, text_template, scope='podcast'):
    return db.create_ad_pattern(
        scope=scope,
        text_template=text_template,
        sponsor_id=sponsor_id,
        intro_variants=[],
        outro_variants=[],
        source_language=None,
    )


def test_merge_rejected_when_combined_template_names_two_foreign_brands(db):
    # AG1 row + a second pattern whose template drags in BetterHelp and
    # Squarespace; merging would produce a multi-sponsor template.
    ag1_id = _seed_sponsor(db, 'AG1')
    _seed_sponsor(db, 'BetterHelp')
    _seed_sponsor(db, 'Squarespace')

    p1 = _seed_pattern(
        db, ag1_id,
        text_template='AG1 has a special offer for listeners. Visit drinkag1.com.',
    )
    p2 = _seed_pattern(
        db, ag1_id,
        text_template=(
            'promo code, discount code, sponsored by, AG1, BetterHelp, '
            'Squarespace, ZipRecruiter for listeners'
        ),
    )

    svc = PatternService(db=db)
    result = svc.merge_similar_patterns([p1, p2], target_scope='network')

    assert result is None, "Merge must be rejected when 2+ foreign brands appear"
    # Source patterns stay active because the merge aborted before disabling them.
    p1_after = db.get_ad_pattern_by_id(p1)
    p2_after = db.get_ad_pattern_by_id(p2)
    assert p1_after['is_active'] in (1, True)
    assert p2_after['is_active'] in (1, True)


def test_merge_allowed_when_all_brands_are_declared_sponsor(db):
    # Two AG1-only patterns -> merge proceeds normally.
    ag1_id = _seed_sponsor(db, 'AG1')

    p1 = _seed_pattern(
        db, ag1_id,
        text_template='AG1 has a special offer for listeners. Visit drinkag1.com.',
    )
    p2 = _seed_pattern(
        db, ag1_id,
        text_template='Drink AG1 every morning. Get a free welcome kit at drinkag1.com.',
    )

    svc = PatternService(db=db)
    merged_id = svc.merge_similar_patterns([p1, p2], target_scope='network')

    assert merged_id is not None
    merged = db.get_ad_pattern_by_id(merged_id)
    assert merged is not None
    assert merged['scope'] == 'network'
