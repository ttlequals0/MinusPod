"""Integration tests for POST /patterns/merge keep-in-place folding (#399).

Covers variant union, the kept row keeping its own template, cross-sponsor
rejection, the sub-75% advisory warning, and folded-fingerprint deletion.
Tests reference patterns by the ids they create, so the shared singleton DB
used across integration tests does not interfere.
"""
import os
import sys
import tempfile


sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='merge-api-test-'))

_READ_A = ('Acme makes great widgets for busy people everywhere. '
           'Visit acme dot com slash deal for twenty percent off your first order.')
_READ_A2 = ('Acme makes great widgets for busy people everywhere. '
            'Visit acme dot com slash deal for twenty percent off your first order today.')
_READ_FAR = ('A completely unrelated read about premium cookware and kitchen knives '
             'with nothing in common with the other sponsor copy at all here.')


def _csrf(app_client):
    with app_client.session_transaction() as sess:
        sess['authenticated'] = True
    app_client.get('/api/v1/auth/status')
    cookie = app_client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}


def _sponsor(db, name):
    existing = db.get_known_sponsor_by_name(name)
    return existing['id'] if existing else db.create_known_sponsor(name=name, aliases=[], category=None)


def _pattern(db, sponsor_id, text, conf=0):
    pid = db.create_ad_pattern(
        scope='podcast', text_template=text, sponsor_id=sponsor_id,
        intro_variants=[], outro_variants=[],
    )
    if conf:
        db.update_ad_pattern(pid, confirmation_count=conf)
    return pid


def test_merge_folds_variants_keeps_template_and_deletes_fingerprint(app_client):
    from api import get_database
    db = get_database()
    hdr = _csrf(app_client)
    s = _sponsor(db, 'AcmeMergeOne')
    keep = _pattern(db, s, _READ_A)
    folded = _pattern(db, s, _READ_A2)
    db.create_audio_fingerprint(keep, b'keep-fp', 1.0)
    db.create_audio_fingerprint(folded, b'folded-fp', 1.0)

    r = app_client.post('/api/v1/patterns/merge',
                        json={'keep_id': keep, 'merge_ids': [folded]}, headers=hdr)
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert body['merged_count'] == 1
    # Manual variant-less rows -> the fold derives + unions intro/outro.
    assert body['intro_variant_count'] >= 1
    assert body['outro_variant_count'] >= 1

    kept = db.get_ad_pattern_by_id(keep)
    assert kept is not None and kept['text_template'] == _READ_A  # own template canonical
    assert db.get_ad_pattern_by_id(folded) is None                # folded row gone
    assert db.get_audio_fingerprint(keep) is not None             # kept fp preserved
    assert db.get_audio_fingerprint(folded) is None               # folded fp deleted


def test_merge_rejects_cross_sponsor(app_client):
    from api import get_database
    db = get_database()
    hdr = _csrf(app_client)
    keep = _pattern(db, _sponsor(db, 'AcmeMergeTwo'), _READ_A)
    other = _pattern(db, _sponsor(db, 'OtherSponsorTwo'), _READ_A2)

    r = app_client.post('/api/v1/patterns/merge',
                        json={'keep_id': keep, 'merge_ids': [other]}, headers=hdr)
    assert r.status_code == 400
    assert 'sponsor' in r.get_json()['error'].lower()
    # Nothing deleted on rejection.
    assert db.get_ad_pattern_by_id(other) is not None


def test_merge_warns_on_sub_75_percent_pair_but_proceeds(app_client):
    from api import get_database
    db = get_database()
    hdr = _csrf(app_client)
    s = _sponsor(db, 'AcmeMergeThree')
    keep = _pattern(db, s, _READ_A)
    far = _pattern(db, s, _READ_FAR)

    r = app_client.post('/api/v1/patterns/merge',
                        json={'keep_id': keep, 'merge_ids': [far]}, headers=hdr)
    assert r.status_code == 200, r.get_data(as_text=True)
    body = r.get_json()
    assert 'warning' in body                      # advisory present
    assert db.get_ad_pattern_by_id(far) is None   # merge still proceeded


def test_merge_suggestions_endpoint_returns_list(app_client):
    r = app_client.get('/api/v1/patterns/merge-suggestions')
    assert r.status_code == 200
    assert isinstance(r.get_json()['suggestions'], list)
