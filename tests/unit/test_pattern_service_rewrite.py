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


TRANSCRIPT = (
    'Welcome to the show. '
    'This episode is brought to you by Squarespace. Visit Squarespace dot com '
    'slash show for a free trial. Use code SHOW for ten percent off. '
    'Squarespace gives you the tools to launch any idea. '
    'Now back to our regular programming.'
)


def _seed_pattern(db, source='local'):
    # The schema migration already seeded Squarespace from sponsors_final.csv.
    sponsor = db.get_known_sponsor_by_name('Squarespace')
    assert sponsor is not None, 'Squarespace should be in the migrated seed list'
    sid = sponsor['id']
    pid = db.create_ad_pattern(
        scope='global',
        text_template='old text template that is long enough to satisfy any length checks for tests today',
        intro_variants=['old intro'],
        outro_variants=['old outro'],
        sponsor_id=sid,
        source=source,
        community_id='abc-123' if source == 'community' else None,
    )
    return pid, sid


def test_rewrite_pattern_from_bounds_updates_local(db, monkeypatch):
    pid, _ = _seed_pattern(db, source='local')
    svc = PatternService(db)
    # Mock the transcript-segment helper used by rewrite to return a fixed window.
    import api
    monkeypatch.setattr(
        api,
        'extract_transcript_segment',
        lambda t, s, e: 'This episode is brought to you by Squarespace. Visit Squarespace dot com slash show for a free trial. Use code SHOW for ten percent off.',
    )
    changed = svc.rewrite_pattern_from_bounds(pid, TRANSCRIPT, 20.0, 60.0)
    assert changed is True
    p = db.get_ad_pattern_by_id(pid)
    assert 'Squarespace' in p['text_template']
    assert 'old text template' not in p['text_template']


def test_rewrite_pattern_skips_community(db, monkeypatch):
    pid, _ = _seed_pattern(db, source='community')
    svc = PatternService(db)
    import api
    monkeypatch.setattr(api, 'extract_transcript_segment', lambda *a, **k: 'new text long enough to be valid')
    changed = svc.rewrite_pattern_from_bounds(pid, TRANSCRIPT, 0.0, 30.0)
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
