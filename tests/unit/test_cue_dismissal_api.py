"""API tests for cue candidate dismiss / list / undo endpoints."""
import json
import os
import sys
import tempfile
from unittest.mock import patch

import pytest

_test_data_dir = tempfile.mkdtemp(prefix='cue_dismiss_api_test_')
os.environ['SECRET_KEY'] = 'test-secret'
os.environ['DATA_DIR'] = _test_data_dir
os.environ['MINUSPOD_MASTER_PASSPHRASE'] = 'cue-dismiss-api-test-passphrase'

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import database
import storage as storage_mod

database.Database._instance = None
database.Database.__init__.__defaults__ = (_test_data_dir,)
database.Database.__new__.__defaults__ = (_test_data_dir,)
storage_mod.Storage.__init__.__defaults__ = (_test_data_dir,)

from main_app import app

from database import Database


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def db():
    return Database()


def _seed_cache(db, pid, episode_id, candidates):
    db.claim_cue_candidate_scan(pid, episode_id, 3600, force=True)
    db.save_cue_candidate_scan_result(pid, episode_id, candidates)


def test_dismiss_happy_path(client, db):
    ep = 'abcdef123456'
    pid = db.create_podcast('dfeed', 'http://x/rss', 'Feed')
    _seed_cache(db, pid, ep, [
        {'start': 10.0, 'end': 12.0, 'kind': 'recurring', 'sv': 5},
        {'start': 50.0, 'end': 52.0, 'kind': 'recurring', 'sv': 5},
    ])
    with patch('api.cue_templates._resolve_original_audio',
               return_value=(f'/audio/{ep}.mp3', None)), \
         patch('api.cue_templates.AudioFingerprinter') as FP:
        FP.return_value.generate_raw_span_fingerprint.return_value = ([1, 2, 3], 2.0)
        r = client.post(f'/api/v1/feeds/dfeed/episodes/{ep}/cue-candidates/dismiss',
                        json={'start_s': 10.0, 'end_s': 12.0, 'label': 'jingle'})
    assert r.status_code == 201
    did = r.get_json()['id']
    rows = db.list_cue_candidate_dismissals(pid)
    assert len(rows) == 1 and rows[0]['fingerprint'] == '[1, 2, 3]'
    cached = json.loads(db.get_cue_candidate_scan(pid, ep)['candidates_json'])
    stamped = [c for c in cached if c.get('dismissed')]
    assert len(stamped) == 1 and stamped[0]['start'] == 10.0
    assert stamped[0]['dismissalId'] == did


def test_dismiss_invalid_span(client, db):
    db.create_podcast('dfeed2', 'http://x/rss', 'Feed')
    r = client.post(
        '/api/v1/feeds/dfeed2/episodes/abcdef123456/cue-candidates/dismiss',
        json={'start_s': 5.0, 'end_s': 5.0})
    assert r.status_code == 400


def test_dismiss_unknown_feed(client):
    r = client.post(
        '/api/v1/feeds/nope/episodes/abcdef123456/cue-candidates/dismiss',
        json={'start_s': 1.0, 'end_s': 2.0})
    assert r.status_code == 404


def test_dismiss_invalid_episode_id(client, db):
    db.create_podcast('dfeed4', 'http://x/rss', 'Feed')
    r = client.post('/api/v1/feeds/dfeed4/episodes/ep1/cue-candidates/dismiss',
                    json={'start_s': 1.0, 'end_s': 2.0})
    assert r.status_code == 400


def test_list_and_undo(client, db):
    pid = db.create_podcast('dfeed3', 'http://x/rss', 'Feed')
    did = db.create_cue_candidate_dismissal(pid, 'ep1', 1.0, 2.0, 'x', '[1]')
    _seed_cache(db, pid, 'ep1', [
        {'start': 1.0, 'end': 2.0, 'kind': 'recurring', 'sv': 5,
         'dismissed': True, 'dismissalId': did},
    ])
    r = client.get('/api/v1/feeds/dfeed3/cue-candidate-dismissals')
    assert r.status_code == 200
    assert r.get_json()['dismissals'][0]['id'] == did
    r = client.delete(f'/api/v1/cue-candidate-dismissals/{did}')
    assert r.status_code == 200 and r.get_json()['deleted'] is True
    assert db.get_cue_candidate_dismissal(did) is None
    cached = json.loads(db.get_cue_candidate_scan(pid, 'ep1')['candidates_json'])
    assert not any(c.get('dismissed') for c in cached)


def test_undo_unknown_id(client):
    r = client.delete('/api/v1/cue-candidate-dismissals/999999')
    assert r.status_code == 404
