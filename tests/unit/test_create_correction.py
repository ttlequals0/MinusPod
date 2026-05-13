"""Unit tests for the `create` correction type in submit_correction."""
import json
import pytest

# Importable handler so we can call it directly without spinning up Flask.
from api.patterns import _submit_correction_create


def _make_episode(temp_db, slug='create-test', episode_id='abcdef012345',
                  duration=300.0):
    temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Create Test')
    temp_db.upsert_episode(
        slug=slug,
        episode_id=episode_id,
        original_url='https://example.com/ep.mp3',
        title='Test Episode',
        original_duration=duration,
    )
    return slug, episode_id


def _call(temp_db, data, slug='create-test', episode_id='abcdef012345'):
    """Invoke the create handler within a Flask app context so json_response works."""
    from main_app import app
    with app.test_request_context():
        response = _submit_correction_create(temp_db, slug, episode_id, data)
    return response


# --- Validation ----------------------------------------------------------

def test_rejects_missing_start(temp_db):
    _make_episode(temp_db)
    resp = _call(temp_db, {'end': 60, 'sponsor': 'Foo', 'text_template': 'x' * 60})
    assert resp.status_code == 400


def test_rejects_inverted_bounds(temp_db):
    _make_episode(temp_db)
    resp = _call(temp_db, {
        'start': 100, 'end': 50, 'sponsor': 'Foo',
        'text_template': 'x' * 60,
    })
    assert resp.status_code == 400


def test_rejects_negative_start(temp_db):
    _make_episode(temp_db)
    resp = _call(temp_db, {
        'start': -1, 'end': 50, 'sponsor': 'Foo',
        'text_template': 'x' * 60,
    })
    assert resp.status_code == 400


def test_rejects_missing_sponsor(temp_db):
    _make_episode(temp_db)
    resp = _call(temp_db, {
        'start': 10, 'end': 50, 'sponsor': '',
        'text_template': 'x' * 60,
    })
    assert resp.status_code == 400


def test_rejects_short_text_template(temp_db):
    _make_episode(temp_db)
    resp = _call(temp_db, {
        'start': 10, 'end': 50, 'sponsor': 'Foo',
        'text_template': 'too short',
    })
    assert resp.status_code == 400


def test_rejects_unknown_scope(temp_db):
    _make_episode(temp_db)
    resp = _call(temp_db, {
        'start': 10, 'end': 50, 'sponsor': 'Foo',
        'text_template': 'x' * 60, 'scope': 'network',
    })
    assert resp.status_code == 400


def test_rejects_end_past_duration(temp_db):
    _make_episode(temp_db, duration=60.0)
    resp = _call(temp_db, {
        'start': 10, 'end': 500, 'sponsor': 'Foo',
        'text_template': 'x' * 60,
    })
    assert resp.status_code == 400


def test_rejects_invalid_sponsor_after_sanitization(temp_db):
    _make_episode(temp_db)
    resp = _call(temp_db, {
        'start': 10, 'end': 50,
        'sponsor': '\x00\x07',  # control chars, sanitize to None
        'text_template': 'x' * 60,
    })
    assert resp.status_code == 400


def test_rejects_missing_episode(temp_db):
    resp = _call(temp_db, {
        'start': 10, 'end': 50, 'sponsor': 'Foo',
        'text_template': 'x' * 60,
    })
    assert resp.status_code == 404


# --- Happy path ----------------------------------------------------------

def test_happy_path_creates_pattern_and_marker(temp_db):
    slug, episode_id = _make_episode(temp_db)
    text = 'This is a long enough ad read for SpansCo to learn from later.'
    assert len(text) >= 50
    resp = _call(temp_db, {
        'start': 30.0, 'end': 60.5, 'sponsor': 'SpansCo',
        'text_template': text, 'reason': 'sponsor read',
        'scope': 'podcast',
    })
    assert resp.status_code == 200
    body = resp.get_json()
    pattern_id = body['pattern_id']
    assert isinstance(pattern_id, int)
    assert body['sponsor'] == 'SpansCo'

    # ad_patterns row created with created_by='user' and sponsor_id set
    pattern = temp_db.get_ad_pattern_by_id(pattern_id)
    assert pattern['created_by'] == 'user'
    assert pattern['sponsor'] == 'SpansCo'  # via JOIN
    assert pattern['scope'] == 'podcast'

    # Marker written to episode_details.ad_markers_json
    episode = temp_db.get_episode(slug, episode_id)
    markers = json.loads(episode['ad_markers_json'])
    assert len(markers) == 1
    marker = markers[0]
    assert marker['start'] == 30.0
    assert marker['end'] == 60.5
    assert marker['sponsor'] == 'SpansCo'
    assert marker['detection_stage'] == 'manual'
    assert marker['confidence'] == 1.0
    assert marker['pattern_id'] == pattern_id

    # pattern_corrections row written with correction_type='create'
    conn = temp_db.get_connection()
    rows = conn.execute(
        "SELECT correction_type, pattern_id, episode_id, sponsor_id, corrected_bounds, "
        "       original_bounds, text_snippet "
        "FROM pattern_corrections WHERE pattern_id = ?",
        (pattern_id,)
    ).fetchall()
    assert len(rows) == 1
    row = rows[0]
    assert row['correction_type'] == 'create'
    assert row['episode_id'] == episode_id
    assert row['sponsor_id'] == pattern['sponsor_id']
    assert json.loads(row['corrected_bounds']) == {'start': 30.0, 'end': 60.5}
    assert row['original_bounds'] is None


def test_inserted_marker_sorted_among_existing(temp_db):
    slug, episode_id = _make_episode(temp_db)
    # Seed two existing markers spanning [10,20] and [120,140]
    seed = [
        {'start': 10.0, 'end': 20.0, 'sponsor': 'A',
         'reason': '', 'confidence': 0.9,
         'detection_stage': 'claude', 'pattern_id': None},
        {'start': 120.0, 'end': 140.0, 'sponsor': 'C',
         'reason': '', 'confidence': 0.9,
         'detection_stage': 'claude', 'pattern_id': None},
    ]
    temp_db.save_episode_details(slug, episode_id, ad_markers=seed)

    text = 'A net-new manual ad that should slot between the two existing markers.'
    resp = _call(temp_db, {
        'start': 60.0, 'end': 90.0, 'sponsor': 'B',
        'text_template': text * 2, 'scope': 'podcast',
    })
    assert resp.status_code == 200

    episode = temp_db.get_episode(slug, episode_id)
    markers = json.loads(episode['ad_markers_json'])
    starts = [m['start'] for m in markers]
    assert starts == sorted(starts)
    assert 60.0 in starts


def test_overlap_with_existing_marker_not_blocked(temp_db):
    slug, episode_id = _make_episode(temp_db)
    seed = [
        {'start': 30.0, 'end': 60.0, 'sponsor': 'A',
         'reason': '', 'confidence': 0.9,
         'detection_stage': 'claude', 'pattern_id': None},
    ]
    temp_db.save_episode_details(slug, episode_id, ad_markers=seed)

    text = 'Overlapping manual ad that should still go through.' * 2
    resp = _call(temp_db, {
        'start': 40.0, 'end': 70.0, 'sponsor': 'B',
        'text_template': text, 'scope': 'podcast',
    })
    assert resp.status_code == 200


def test_global_scope_creates_global_pattern(temp_db):
    _make_episode(temp_db)
    text = 'A global-scope manual ad to validate the scope branch wires through.'
    resp = _call(temp_db, {
        'start': 0.0, 'end': 30.0, 'sponsor': 'GlobalCo',
        'text_template': text, 'scope': 'global',
    })
    assert resp.status_code == 200
    pattern = temp_db.get_ad_pattern_by_id(resp.get_json()['pattern_id'])
    assert pattern['scope'] == 'global'


def test_case_variant_sponsor_resolves_to_existing(temp_db):
    """Submitting a create with 'squarespace' after 'Squarespace' exists
    must reuse the same sponsor_id (case-insensitive lookup)."""
    _make_episode(temp_db)
    sid = temp_db.create_known_sponsor(name='Squarespace')

    text = 'A second create against a case-variant sponsor name.'
    resp = _call(temp_db, {
        'start': 0.0, 'end': 30.0, 'sponsor': 'squarespace',
        'text_template': text * 2, 'scope': 'podcast',
    })
    assert resp.status_code == 200
    pattern = temp_db.get_ad_pattern_by_id(resp.get_json()['pattern_id'])
    assert pattern['sponsor_id'] == sid
