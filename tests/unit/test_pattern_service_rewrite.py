"""Tests for PatternService.rewrite_pattern_from_bounds + import_community_pattern."""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from database import Database  # noqa: E402
from pattern_service import PatternService  # noqa: E402


@pytest.fixture
def db(tmp_path):
    # Singleton Database; clear it so each test gets a fresh sqlite file.
    Database._instance = None  # type: ignore[attr-defined]
    if hasattr(Database, '_initialized'):
        Database._initialized = False  # type: ignore[attr-defined]
    instance = Database(data_dir=str(tmp_path))
    yield instance
    Database._instance = None  # type: ignore[attr-defined]


# VTT-style transcript. Sentence-per-segment with gaps between, so the trim
# windows (head 0..20, tail 90..120) line up with the head / tail segments
# without partially overlapping the middle ad copy. include_partial=True is
# the default in extract_text_in_range, so any overlap pulls the segment in.
TRANSCRIPT = (
    '[00:00:00.000 --> 00:00:05.000] Welcome to the show.\n'
    '[00:00:30.000 --> 00:00:35.000] This episode is brought to you by Squarespace.\n'
    '[00:00:40.000 --> 00:00:50.000] Visit Squarespace dot com slash show for a free trial.\n'
    '[00:00:55.000 --> 00:01:05.000] Use code SHOW for ten percent off your first website.\n'
    '[00:01:55.000 --> 00:02:00.000] Now back to our regular programming.\n'
)


def _seed_pattern(
    db,
    source='local',
    text_template=None,
    intro_variants=None,
    outro_variants=None,
):
    # The schema migration already seeded Squarespace from sponsors_final.csv.
    sponsor = db.get_known_sponsor_by_name('Squarespace')
    assert sponsor is not None, 'Squarespace should be in the migrated seed list'
    sid = sponsor['id']
    pid = db.create_ad_pattern(
        scope='global',
        text_template=text_template or 'old text template that is long enough to satisfy any length checks for tests today',
        intro_variants=intro_variants if intro_variants is not None else ['old intro'],
        outro_variants=outro_variants if outro_variants is not None else ['old outro'],
        sponsor_id=sid,
        source=source,
        community_id='abc-123' if source == 'community' else None,
    )
    return pid, sid


def test_rewrite_trims_head_and_tail_from_existing_template(db):
    """The trim splices the head/tail transcript slice out of the existing
    template — it does NOT re-extract a new template from the new bounds."""
    template = (
        'Welcome to the show. This episode is brought to you by Squarespace. '
        'Visit Squarespace dot com slash show for a free trial. Use code SHOW '
        'for ten percent off your first website. Now back to our regular programming.'
    )
    intro = ['Welcome to the show.']
    outro = ['Now back to our regular programming.']
    pid, _ = _seed_pattern(
        db, text_template=template, intro_variants=intro, outro_variants=outro,
    )
    svc = PatternService(db)

    # Reviewer narrowed [0, 120] -> [25, 90]. Head trim (0..25) picks up the
    # "Welcome to the show." segment; tail trim (90..120) picks up "Now back
    # to our regular programming." Middle ad copy stays intact.
    changed = svc.rewrite_pattern_from_bounds(
        pid, TRANSCRIPT,
        original_start=0.0, original_end=120.0,
        new_start=25.0, new_end=90.0,
    )
    assert changed is True
    p = db.get_ad_pattern_by_id(pid)
    assert 'Welcome to the show.' not in p['text_template']
    assert 'Now back to our regular programming.' not in p['text_template']
    # Middle survives, unchanged from the original template content.
    assert 'Squarespace' in p['text_template']
    assert 'Use code SHOW' in p['text_template']


def test_rewrite_returns_false_when_trim_doesnt_match_template(db):
    """If the head/tail slice from the transcript isn't actually at the
    start/end of the existing template, the rewrite is a no-op."""
    pid, _ = _seed_pattern(
        db,
        text_template='completely unrelated template that does not begin with welcome or end with regular programming',
    )
    svc = PatternService(db)
    changed = svc.rewrite_pattern_from_bounds(
        pid, TRANSCRIPT,
        original_start=0.0, original_end=120.0,
        new_start=25.0, new_end=90.0,
    )
    assert changed is False
    p = db.get_ad_pattern_by_id(pid)
    assert p['text_template'].startswith('completely unrelated')


def test_rewrite_pattern_skips_community(db):
    pid, _ = _seed_pattern(db, source='community')
    svc = PatternService(db)
    changed = svc.rewrite_pattern_from_bounds(
        pid, TRANSCRIPT,
        original_start=0.0, original_end=120.0,
        new_start=25.0, new_end=90.0,
    )
    assert changed is False
    p = db.get_ad_pattern_by_id(pid)
    assert p['text_template'].startswith('old text template')


def test_import_community_pattern_insert_then_update(db):
    svc = PatternService(db)
    cid = 'c1-22-33-44-55'
    pid1 = svc.import_community_pattern({
        'community_id': cid,
        'version': 1,
        'scope': 'global',
        'sponsor': 'Squarespace',
        'text_template': 'community pattern text template version one of squarespace dot com slash show',
        'intro_variants': ['Visit Squarespace'],
    })
    row = db.get_ad_pattern_by_id(pid1)
    assert row['source'] == 'community'
    assert row['community_id'] == cid
    assert row['version'] == 1

    # Same community_id, higher version -> update.
    pid2 = svc.import_community_pattern({
        'community_id': cid,
        'version': 2,
        'scope': 'global',
        'sponsor': 'Squarespace',
        'text_template': 'updated community text template version two for squarespace promo SHOW',
    })
    assert pid2 == pid1
    row = db.get_ad_pattern_by_id(pid1)
    assert row['version'] == 2
    assert 'updated community text' in row['text_template']


def test_import_community_pattern_respects_protected(db):
    svc = PatternService(db)
    cid = 'protected-c-001'
    pid = svc.import_community_pattern({
        'community_id': cid,
        'version': 1,
        'scope': 'global',
        'sponsor': 'Squarespace',
        'text_template': 'community version one for squarespace dot com slash show promo SHOW save ten',
    })
    db.set_pattern_protected(pid, True)
    # Higher version, but protected -> no update.
    pid2 = svc.import_community_pattern({
        'community_id': cid,
        'version': 5,
        'scope': 'global',
        'sponsor': 'Squarespace',
        'text_template': 'updated version five text body for squarespace promo SHOW save fifty percent today',
    })
    assert pid2 == pid
    row = db.get_ad_pattern_by_id(pid)
    assert row['version'] == 1
    assert 'version one' in row['text_template']
