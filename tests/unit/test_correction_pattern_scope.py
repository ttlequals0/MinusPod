"""Pattern auto-creation from corrections must store the slug, not the
numeric podcast id (PR #196).

The detection-side query reads `ad_patterns.podcast_id` against
`podcasts.slug` (`src/database/patterns.py:17`). All other creation paths
already pass the slug; this test pins the two correction paths
(`type=confirm` and `type=adjust` with no existing pattern_id) so they
can't regress to writing the numeric id.
"""
import json
import os
import sys
import tempfile
from unittest.mock import patch, MagicMock

import pytest

_test_data_dir = tempfile.mkdtemp(prefix='corr_test_')
os.environ['SECRET_KEY'] = 'test-secret'
os.environ['DATA_DIR'] = _test_data_dir

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import database
import storage as storage_mod
database.Database._instance = None
database.Database.__init__.__defaults__ = (_test_data_dir,)
database.Database.__new__.__defaults__ = (_test_data_dir,)
storage_mod.Storage.__init__.__defaults__ = (_test_data_dir,)

from main_app import app


SLUG = 'my-test-podcast'
EPISODE_ID = 'ep-001'
NUMERIC_PODCAST_ID = 42
TRANSCRIPT_TEXT = (
    "[00:00:00.000 --> 00:01:00.000] This episode is brought to you by "
    "ExampleSponsor. Visit examplesponsor.com slash podcast for fifty percent "
    "off your first month. ExampleSponsor makes everything better and faster "
    "than the competition."
)


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _mock_db():
    """A db mock that produces the conditions for the no-pattern_id branch
    of submit_correction, and records the create_ad_pattern call."""
    db = MagicMock()
    db.get_transcript_for_timestamps.return_value = TRANSCRIPT_TEXT
    db.get_podcast_by_slug.return_value = {'id': NUMERIC_PODCAST_ID, 'slug': SLUG}
    db.find_pattern_by_text.return_value = None
    db.create_ad_pattern.return_value = 1234
    return db


def _correction_payload(correction_type, *, with_adjusted=False):
    payload = {
        'type': correction_type,
        'original_ad': {
            'start': 0.0,
            'end': 60.0,
            'sponsor': 'ExampleSponsor',
            'reason': 'host-read sponsor',
        },
    }
    if with_adjusted:
        payload['adjusted_start'] = 5.0
        payload['adjusted_end'] = 55.0
    return payload


def _slug_passed_to_create_ad_pattern(db_mock):
    db_mock.create_ad_pattern.assert_called_once()
    return db_mock.create_ad_pattern.call_args.kwargs['podcast_id']


def test_confirm_correction_stores_slug_not_numeric_id(client):
    db = _mock_db()
    with patch('api.patterns.get_database', return_value=db):
        resp = client.post(
            f'/api/v1/episodes/{SLUG}/{EPISODE_ID}/corrections',
            data=json.dumps(_correction_payload('confirm')),
            content_type='application/json',
        )
    assert resp.status_code == 200, resp.data
    assert _slug_passed_to_create_ad_pattern(db) == SLUG


def test_adjust_correction_stores_slug_not_numeric_id(client):
    db = _mock_db()
    with patch('api.patterns.get_database', return_value=db):
        resp = client.post(
            f'/api/v1/episodes/{SLUG}/{EPISODE_ID}/corrections',
            data=json.dumps(_correction_payload('adjust', with_adjusted=True)),
            content_type='application/json',
        )
    assert resp.status_code == 200, resp.data
    assert _slug_passed_to_create_ad_pattern(db) == SLUG


def test_confirm_correction_dedup_lookup_uses_slug(client):
    """The deduplication query (find_pattern_by_text) is also a podcast_id
    consumer; it has to use the slug too or it'll fail to find existing
    patterns and create duplicates."""
    db = _mock_db()
    with patch('api.patterns.get_database', return_value=db):
        client.post(
            f'/api/v1/episodes/{SLUG}/{EPISODE_ID}/corrections',
            data=json.dumps(_correction_payload('confirm')),
            content_type='application/json',
        )
    db.find_pattern_by_text.assert_called_once()
    # Second positional arg of find_pattern_by_text is podcast_id
    args, _ = db.find_pattern_by_text.call_args
    assert args[1] == SLUG
