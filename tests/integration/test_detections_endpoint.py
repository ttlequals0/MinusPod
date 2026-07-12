"""Integration tests for GET /api/v1/detections."""
import pytest


def _csrf(app_client):
    with app_client.session_transaction() as sess:
        sess['authenticated'] = True
    app_client.get('/api/v1/auth/status')
    cookie = app_client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}


@pytest.fixture
def seeded_detections(app_client):
    from api import get_database
    db = get_database()
    slug = 'detections-feed'
    db.create_podcast(slug, 'https://example.com/feed.xml',
                      title='Detections Test Feed')
    markers = [
        {'start': 10.0, 'end': 40.0, 'confidence': 0.9, 'sponsor': 'Acme',
         'reason': 'sponsor read'},
        {'start': 100.0, 'end': 130.0, 'confidence': 0.4, 'was_cut': False,
         'validation': {'decision': 'REJECT'}},
        {'start': 200.0, 'end': 230.0, 'confidence': 0.6,
         'held_for_review': True, 'was_cut': False},
    ]
    db.upsert_episode(slug, 'det-ep-1',
                      original_url='https://example.com/e1.mp3',
                      title='Episode One', status='processed')
    db.save_episode_details(slug, 'det-ep-1', ad_markers=markers)
    yield {'slug': slug, 'db': db}
    db.delete_podcast(slug)


def test_default_returns_needs_review_only(app_client, seeded_detections):
    _csrf(app_client)
    body = app_client.get('/api/v1/detections').get_json()
    starts = sorted(d['start'] for d in body['detections'])
    assert starts == [100.0, 200.0]
    assert body['total'] == 2
    assert body['page'] == 1
    assert body['totalPages'] == 1
    for d in body['detections']:
        assert d['processedUrl'].startswith('/episodes/')
        assert 'processedVersion' not in d


def test_status_all_includes_accepted(app_client, seeded_detections):
    _csrf(app_client)
    body = app_client.get('/api/v1/detections?status=all').get_json()
    assert body['total'] == 3


def test_feed_filter_and_search(app_client, seeded_detections):
    _csrf(app_client)
    slug = seeded_detections['slug']
    body = app_client.get(
        f'/api/v1/detections?status=all&feed={slug}&q=acme').get_json()
    assert body['total'] == 1
    assert body['detections'][0]['sponsor'] == 'Acme'


def test_sort_confidence_asc(app_client, seeded_detections):
    _csrf(app_client)
    body = app_client.get(
        '/api/v1/detections?status=all&sort=confidence&order=asc').get_json()
    confidences = [d['confidence'] for d in body['detections']]
    assert confidences == sorted(confidences)


def test_pagination_limits(app_client, seeded_detections):
    _csrf(app_client)
    body = app_client.get('/api/v1/detections?status=all&limit=2').get_json()
    assert len(body['detections']) == 2
    assert body['totalPages'] == 2
    page2 = app_client.get(
        '/api/v1/detections?status=all&limit=2&page=2').get_json()
    assert len(page2['detections']) == 1


@pytest.mark.parametrize('query', [
    'status=bogus', 'sort=bogus', 'order=sideways',
])
def test_invalid_params_return_400(app_client, seeded_detections, query):
    _csrf(app_client)
    r = app_client.get(f'/api/v1/detections?{query}')
    assert r.status_code == 400


def test_resolved_detection_leaves_needs_review(app_client, seeded_detections):
    _csrf(app_client)
    db = seeded_detections['db']
    db.create_pattern_correction(
        correction_type='false_positive', pattern_id=None,
        episode_id='det-ep-1', original_bounds={'start': 100.0, 'end': 130.0})
    body = app_client.get('/api/v1/detections').get_json()
    starts = [d['start'] for d in body['detections']]
    assert starts == [200.0]
    resolved = app_client.get('/api/v1/detections?status=rejected').get_json()
    assert resolved['detections'][0]['resolution'] == 'dismissed'
