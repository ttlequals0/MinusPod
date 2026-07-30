"""Integration tests for the split correction type (issue #563, option 1)."""
import json

import pytest

from api import get_database


def _ts(v):
    return f"{int(v // 3600):02d}:{int(v % 3600 // 60):02d}:{v % 60:06.3f}"


def _vtt(*rows):
    return '\n'.join(f"[{_ts(a)} --> {_ts(b)}] {t}" for a, b, t in rows)


THREE_ADS = _vtt(
    (100.0, 130.0, 'Today you can save at Acme dot com with code SAVE.'),
    (130.0, 160.0, 'This episode is brought to you by Beta Corp, the easy way to file.'),
    (160.0, 190.0, 'And our thanks to Gamma Industries for supporting the show.'),
)

MERGED_MARKER = {
    'start': 100.0, 'end': 190.0, 'confidence': 0.9,
    'sponsor': 'Acme', 'reason': 'stacked sponsor block',
    'category': 'sponsor', 'action_applied': 'remove',
    'detection_stage': 'dai_differential', 'was_cut': True,
}


def _csrf(app_client):
    with app_client.session_transaction() as sess:
        sess['authenticated'] = True
    app_client.get('/api/v1/auth/status')
    cookie = app_client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}


@pytest.fixture
def merged(app_client):
    db = get_database()
    slug = 'split-correction-feed'
    episode_id = 'a1b2c3d4e5f6'
    db.create_podcast(slug, 'https://example.com/feed.xml', 'The Daily Tech Show')
    db.upsert_episode(slug=slug, episode_id=episode_id,
                      original_url='https://example.com/ep.mp3',
                      title='Episode One', original_duration=3600.0,
                      status='processed')
    db.save_episode_details(slug, episode_id, transcript_text=THREE_ADS,
                            ad_markers=[dict(MERGED_MARKER)])
    # pattern_corrections keys on episode_id as a string, so delete_podcast
    # leaves its rows behind and they accumulate across tests in this module.
    db.get_connection().execute(
        'DELETE FROM pattern_corrections WHERE episode_id = ?', (episode_id,))
    db.get_connection().commit()
    yield {'slug': slug, 'episodeId': episode_id, 'db': db}
    db.delete_podcast(slug)


def _post(app_client, ref, body):
    return app_client.post(
        f"/api/v1/episodes/{ref['slug']}/{ref['episodeId']}/corrections",
        json=body, headers=_csrf(app_client))


def _split(app_client, ref, points, pieces=None):
    body = {'type': 'split',
            'original_ad': {'start': 100.0, 'end': 190.0},
            'split_points': points}
    if pieces is not None:
        body['pieces'] = pieces
    return _post(app_client, ref, body)


def _markers(ref):
    episode = ref['db'].get_episode(ref['slug'], ref['episodeId'])
    return json.loads(episode['ad_markers_json'])


class TestValidation:
    def test_point_outside_the_span_is_rejected(self, app_client, merged):
        assert _split(app_client, merged, [500.0]).status_code == 400

    def test_point_at_the_boundary_is_rejected(self, app_client, merged):
        assert _split(app_client, merged, [100.0]).status_code == 400

    def test_empty_split_points_is_rejected(self, app_client, merged):
        assert _split(app_client, merged, []).status_code == 400

    def test_non_numeric_point_is_rejected(self, app_client, merged):
        assert _split(app_client, merged, ['banana']).status_code == 400

    def test_piece_under_the_minimum_duration_is_rejected(self, app_client, merged):
        resp = _split(app_client, merged, [103.0])
        assert resp.status_code == 400
        assert 'minimum ad duration' in resp.get_json()['error']

    def test_two_points_too_close_together_are_rejected(self, app_client, merged):
        assert _split(app_client, merged, [130.0, 132.0]).status_code == 400

    def test_bounds_matching_no_marker_are_rejected(self, app_client, merged):
        resp = _post(app_client, merged, {
            'type': 'split',
            'original_ad': {'start': 900.0, 'end': 1000.0},
            'split_points': [950.0],
        })
        assert resp.status_code == 404

    def test_a_rejected_split_leaves_the_marker_untouched(self, app_client, merged):
        _split(app_client, merged, [103.0])
        markers = _markers(merged)
        assert len(markers) == 1
        assert (markers[0]['start'], markers[0]['end']) == (100.0, 190.0)


class TestSplitting:
    def test_one_divider_replaces_the_marker_with_two(self, app_client, merged):
        resp = _split(app_client, merged, [130.0])
        assert resp.status_code == 200
        assert resp.get_json()['markerCount'] == 2
        bounds = [(m['start'], m['end']) for m in _markers(merged)]
        assert bounds == [(100.0, 130.0), (130.0, 190.0)]

    def test_two_dividers_give_three_contiguous_markers(self, app_client, merged):
        assert _split(app_client, merged, [130.0, 160.0]).status_code == 200
        bounds = [(m['start'], m['end']) for m in _markers(merged)]
        assert bounds == [(100.0, 130.0), (130.0, 160.0), (160.0, 190.0)]

    def test_pieces_inherit_category_stage_and_cut_state(self, app_client, merged):
        """Splitting must not silently change whether the audio was removed."""
        _split(app_client, merged, [130.0])
        for m in _markers(merged):
            assert m['category'] == 'sponsor'
            assert m['action_applied'] == 'remove'
            assert m['detection_stage'] == 'dai_differential'
            assert m['was_cut'] is True

    def test_a_pattern_is_minted_per_piece(self, app_client, merged):
        body = _split(app_client, merged, [130.0]).get_json()
        assert len(body['patternIds']) >= 1
        assert len(set(body['patternIds'])) == len(body['patternIds'])

    def test_supplied_sponsor_overrides_the_guess(self, app_client, merged):
        _split(app_client, merged, [130.0],
               pieces=[{'sponsor': 'Acme'}, {'sponsor': 'Beta Corp'}])
        sponsors = [m['sponsor'] for m in _markers(merged)]
        assert sponsors == ['Acme', 'Beta Corp']

    def test_response_message_names_the_piece_count(self, app_client, merged):
        body = _split(app_client, merged, [130.0, 160.0]).get_json()
        assert body['message'] == 'Split into 3 ads'

    def test_correction_rows_use_existing_types(self, app_client, merged):
        """No 'split' value: correction_type has a CHECK constraint SQLite cannot
        alter in place. Reads the table directly, since get_review_corrections
        filters out 'create' and could never observe it."""
        _split(app_client, merged, [130.0, 160.0])
        conn = merged['db'].get_connection()
        types = [r[0] for r in conn.execute(
            'SELECT correction_type FROM pattern_corrections WHERE episode_id = ?'
            ' ORDER BY id', (merged['episodeId'],)).fetchall()]
        assert types == ['boundary_adjustment', 'create', 'create']

    def test_other_markers_on_the_episode_survive(self, app_client, merged):
        other = {'start': 900.0, 'end': 930.0, 'confidence': 0.8,
                 'sponsor': 'Delta', 'was_cut': True}
        merged['db'].save_episode_details(
            merged['slug'], merged['episodeId'],
            ad_markers=[dict(MERGED_MARKER), other])
        _split(app_client, merged, [130.0])
        bounds = [(m['start'], m['end']) for m in _markers(merged)]
        assert (900.0, 930.0) in bounds
        assert len(bounds) == 3
