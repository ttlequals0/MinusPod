"""API-level tests for the sponsors management endpoints (issue #304):
stats fields in GET responses and hard-delete-with-unlink on DELETE.

No app_password is set, so the session is unauthenticated and CSRF is
bypassed for mutations (same setup as test_stage_tunables_api).
"""
import json
import os
import sys
import tempfile

import pytest

_test_data_dir = tempfile.mkdtemp(prefix='sponsors_api_test_')
os.environ['SECRET_KEY'] = 'test-secret'
os.environ['DATA_DIR'] = _test_data_dir
os.environ['MINUSPOD_MASTER_PASSPHRASE'] = 'sponsors-api-test-passphrase'

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import database
import storage as storage_mod

database.Database._instance = None
database.Database.__init__.__defaults__ = (_test_data_dir,)
database.Database.__new__.__defaults__ = (_test_data_dir,)
storage_mod.Storage.__init__.__defaults__ = (_test_data_dir,)

from main_app import app


@pytest.fixture
def client():
    db = database.Database()
    db.set_setting('app_password', '')
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _seed_sponsor_with_pattern(name, last_matched_at='2026-02-01T00:00:00Z'):
    db = database.Database()
    sid = db.create_known_sponsor(name)
    pid = db.create_ad_pattern(scope='podcast', text_template='buy now', sponsor_id=sid)
    conn = db.get_connection()
    conn.execute(
        "UPDATE ad_patterns SET last_matched_at = ? WHERE id = ?",
        (last_matched_at, pid),
    )
    conn.commit()
    return sid, pid


def _get_sponsor(client, sponsor_id):
    resp = client.get('/api/v1/sponsors?include_inactive=true')
    assert resp.status_code == 200
    sponsors = json.loads(resp.data)['sponsors']
    return next((s for s in sponsors if s['id'] == sponsor_id), None)


def test_list_includes_pattern_stats(client):
    sid, _ = _seed_sponsor_with_pattern('Stats Sponsor')
    s = _get_sponsor(client, sid)
    assert s is not None
    assert s['pattern_count'] == 1
    assert s['last_matched_at'] == '2026-02-01T00:00:00Z'


def test_sponsor_without_patterns_defaults(client):
    db = database.Database()
    sid = db.create_known_sponsor('No Patterns Co')
    s = _get_sponsor(client, sid)
    assert s['pattern_count'] == 0
    assert s['last_matched_at'] is None


def test_delete_hard_removes_and_unlinks(client):
    sid, pid = _seed_sponsor_with_pattern('Delete Sponsor')

    resp = client.delete(f'/api/v1/sponsors/{sid}')
    assert resp.status_code == 200
    assert json.loads(resp.data)['unlinkedPatterns'] == 1

    # Sponsor row is gone, even with include_inactive.
    assert _get_sponsor(client, sid) is None

    # Pattern survives, unlinked.
    conn = database.Database().get_connection()
    row = conn.execute("SELECT sponsor_id FROM ad_patterns WHERE id = ?", (pid,)).fetchone()
    assert row is not None
    assert row['sponsor_id'] is None


def test_delete_unknown_returns_404(client):
    resp = client.delete('/api/v1/sponsors/999999')
    assert resp.status_code == 404
