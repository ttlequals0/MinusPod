"""Integration tests for disabled_at stamping on PUT /patterns/<id>."""
import os
import sys
import tempfile


sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='pattern-disable-api-test-'))

from tests.app_bootstrap import authenticate_test_client  # noqa: E402

_READ = ('Acme makes great widgets for busy people everywhere. '
         'Visit acme dot com slash deal for twenty percent off your first order.')
_OLD_STAMP = '2020-01-02T03:04:05Z'


def _pattern(db):
    return db.create_ad_pattern(
        scope='podcast', text_template=_READ,
        intro_variants=[], outro_variants=[],
    )


def _put(app_client, pid, body):
    r = app_client.put(f'/api/v1/patterns/{pid}', json=body,
                       headers=authenticate_test_client(app_client))
    assert r.status_code == 200, r.get_data(as_text=True)


def test_put_deactivate_stamps_disabled_at(app_client):
    from api import get_database
    db = get_database()
    pid = _pattern(db)

    _put(app_client, pid, {'is_active': False})

    row = db.get_ad_pattern_by_id(pid)
    assert not row['is_active']
    assert row['disabled_at']


def test_put_redeactivate_keeps_original_disabled_at(app_client):
    from api import get_database
    db = get_database()
    pid = _pattern(db)
    db.update_ad_pattern(pid, is_active=False, disabled_at=_OLD_STAMP)

    _put(app_client, pid, {'is_active': False})

    assert db.get_ad_pattern_by_id(pid)['disabled_at'] == _OLD_STAMP


def test_put_reactivate_clears_disabled_at(app_client):
    from api import get_database
    db = get_database()
    pid = _pattern(db)
    db.update_ad_pattern(pid, is_active=False, disabled_at=_OLD_STAMP,
                         disabled_reason='Too many false positives')

    _put(app_client, pid, {'is_active': True})

    row = db.get_ad_pattern_by_id(pid)
    assert row['is_active']
    assert row['disabled_at'] is None
    assert row['disabled_reason'] is None
