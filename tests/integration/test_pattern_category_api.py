"""Integration tests for PUT /patterns/<id> category editing (DTNS 5337).

A pattern's category decides its segment action at detection time (e.g.
cross_promo resolving to keep), but the update endpoint had no way to fix a
miscategorized pattern; the only levers were disable or delete.
"""
import os
import sys
import tempfile


sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='pattern-cat-api-test-'))

_READ = ('Morning Brew Daily covers the biggest stories in business and tech '
         'every weekday. Subscribe wherever you get your podcasts today.')


def _csrf(app_client):
    with app_client.session_transaction() as sess:
        sess['authenticated'] = True
    app_client.get('/api/v1/auth/status')
    cookie = app_client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}


def _pattern(db, category=None):
    pid = db.create_ad_pattern(
        scope='podcast', text_template=_READ,
        intro_variants=[], outro_variants=[],
    )
    if category:
        db.update_ad_pattern(pid, category=category)
    return pid


def test_put_updates_category(app_client):
    from api import get_database
    db = get_database()
    hdr = _csrf(app_client)
    pid = _pattern(db, category='cross_promo')

    r = app_client.put(f'/api/v1/patterns/{pid}',
                       json={'category': 'sponsor'}, headers=hdr)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert db.get_ad_pattern_by_id(pid)['category'] == 'sponsor'


def test_put_rejects_unknown_category(app_client):
    from api import get_database
    db = get_database()
    hdr = _csrf(app_client)
    pid = _pattern(db, category='cross_promo')

    r = app_client.put(f'/api/v1/patterns/{pid}',
                       json={'category': 'advertisement'}, headers=hdr)
    assert r.status_code == 400
    assert db.get_ad_pattern_by_id(pid)['category'] == 'cross_promo'


def test_put_null_clears_category(app_client):
    from api import get_database
    db = get_database()
    hdr = _csrf(app_client)
    pid = _pattern(db, category='cross_promo')

    r = app_client.put(f'/api/v1/patterns/{pid}',
                       json={'category': None}, headers=hdr)
    assert r.status_code == 200, r.get_data(as_text=True)
    assert db.get_ad_pattern_by_id(pid).get('category') is None


def test_import_persists_valid_category(app_client):
    from api import get_database
    db = get_database()
    hdr = _csrf(app_client)

    r = app_client.post('/api/v1/patterns/import', headers=hdr, json={
        'mode': 'supplement',
        'patterns': [{'scope': 'global', 'text_template': _READ,
                      'sponsor': 'Morning Brew Cat Test',
                      'category': 'cross_promo'}],
    })
    assert r.status_code == 200, r.get_data(as_text=True)
    created = [p for p in db.get_ad_patterns()
               if p.get('sponsor') == 'Morning Brew Cat Test']
    assert created and created[0]['category'] == 'cross_promo'


def test_import_rejects_unknown_category(app_client):
    hdr = _csrf(app_client)

    r = app_client.post('/api/v1/patterns/import', headers=hdr, json={
        'mode': 'supplement',
        'patterns': [{'scope': 'global', 'text_template': _READ,
                      'sponsor': 'Bad Category Co',
                      'category': 'advertisement'}],
    })
    assert r.status_code == 400
