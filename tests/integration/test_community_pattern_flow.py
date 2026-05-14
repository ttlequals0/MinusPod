"""End-to-end integration: community sync + protect + reseed + tag eligibility."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from database import Database  # noqa: E402
from community_sync import apply_manifest  # noqa: E402
from text_pattern_matcher import TextPatternMatcher  # noqa: E402


@pytest.fixture
def db(tmp_path):
    Database._instance = None  # type: ignore[attr-defined]
    inst = Database(data_dir=str(tmp_path))
    yield inst
    Database._instance = None  # type: ignore[attr-defined]


SEGMENTS = [
    {'start': 0.0, 'end': 5.0, 'text': 'Welcome to the show'},
    {'start': 5.0, 'end': 15.0, 'text': 'This episode is brought to you by Squarespace'},
    {'start': 15.0, 'end': 25.0, 'text': 'Visit Squarespace dot com slash show for a free trial'},
    {'start': 25.0, 'end': 35.0, 'text': 'Use code SHOW for ten percent off your first website'},
    {'start': 35.0, 'end': 40.0, 'text': 'Back to the episode'},
]


def _manifest(community_id='sq-1', version=1):
    return {
        'manifest_version': 1,
        'patterns': [{
            'community_id': community_id,
            'version': version,
            'data': {
                'community_id': community_id,
                'version': version,
                'scope': 'global',
                'sponsor': 'Squarespace',
                'text_template': (
                    'This episode is brought to you by Squarespace. '
                    'Visit Squarespace dot com slash show for a free trial. '
                    'Use code SHOW for ten percent off your first website.'
                ),
                'intro_variants': ['This episode is brought to you by Squarespace'],
                'outro_variants': ['Use code SHOW'],
            },
        }],
    }


def test_full_sync_to_match_cycle(db):
    # 1. Apply the manifest -> 1 community pattern inserted, source='community'.
    summary = apply_manifest(db, _manifest())
    assert summary['inserted'] == 1

    rows = db.get_patterns_by_source('community', active_only=True)
    assert len(rows) == 1
    cp = rows[0]
    assert cp['source'] == 'community'
    assert cp['version'] == 1
    assert cp['protected_from_sync'] == 0

    # 2. Sponsor Squarespace was seeded by migration with 'universal' tag,
    # so eligibility is unconditional.
    matcher = TextPatternMatcher(db=db)
    matcher._load_patterns()
    out = matcher._filter_patterns_by_scope(podcast_tags={'kids_family'})
    assert any(p.id == cp['id'] for p in out), 'universal sponsor should always be eligible'


def test_resync_skips_protected_pattern(db):
    apply_manifest(db, _manifest(version=1))
    pid = db.get_patterns_by_source('community', active_only=False)[0]['id']

    # User edits / protects the row (the API endpoint sets this; we set it directly).
    db.set_pattern_protected(pid, True)

    # Higher-version manifest -> should NOT update the protected row.
    summary = apply_manifest(db, _manifest(version=2))
    row = db.get_ad_pattern_by_id(pid)
    assert row['version'] == 1
    assert summary['updated'] == 0
    assert summary['skipped'] >= 1


def test_sponsor_reseed_preserves_pattern_fk(db):
    # The seed migration ran during Database init; patterns referencing
    # sponsor_id from the migration should remain valid.
    squarespace = db.get_known_sponsor_by_name('Squarespace')
    assert squarespace is not None
    pid = db.create_ad_pattern(
        scope='podcast',
        podcast_id='test-podcast',
        text_template='Squarespace pattern body long enough for tests to be considered valid for storage today',
        sponsor_id=squarespace['id'],
        source='local',
    )
    # Re-trigger the schema migrations explicitly to ensure idempotence.
    conn = db.get_connection()
    # Force-rerun by clearing the revision stamp
    conn.execute("DELETE FROM settings WHERE key = 'sponsor_seed_revision'")
    conn.commit()
    db._reseed_known_sponsors(conn)

    # The pattern's sponsor_id still resolves to a valid sponsor row.
    pattern = db.get_ad_pattern_by_id(pid)
    sponsor = db.get_known_sponsor_by_id(pattern['sponsor_id'])
    assert sponsor is not None
    assert sponsor['name'] == 'Squarespace'
