"""Integration tests for GET /api/v1/detections."""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='detections-api-test-'))

from api import get_database


def _csrf(app_client):
    with app_client.session_transaction() as sess:
        sess['authenticated'] = True
    app_client.get('/api/v1/auth/status')
    cookie = app_client.get_cookie('minuspod_csrf')
    return {'X-CSRF-Token': cookie.value} if cookie else {}


@pytest.fixture
def seeded_detections(app_client):
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
                      title='Episode One', status='processed',
                      original_duration=3600.0)
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
    assert body['counts'] == {
        'total': 3, 'needsReview': 2, 'pending': 1, 'rejected': 1,
        'accepted': 1, 'confirmed': 0, 'dismissed': 0,
    }


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
    'status=bogus', 'sort=bogus', 'order=sideways', 'reviewer=bogus',
])
def test_invalid_params_return_400(app_client, seeded_detections, query):
    _csrf(app_client)
    r = app_client.get(f'/api/v1/detections?{query}')
    assert r.status_code == 400


def test_reviewer_filter_narrows_rows_and_cut_summary(app_client, seeded_detections):
    _csrf(app_client)
    db = seeded_detections['db']
    slug = seeded_detections['slug']
    db.save_episode_details(slug, 'det-ep-1', ad_markers=[
        {'start': 10.0, 'end': 40.0, 'confidence': 0.9, 'sponsor': 'Acme',
         'reviewer_verdict': 'adjust', 'reviewer_original_start': 8.0,
         'reviewer_original_end': 41.0},
        {'start': 100.0, 'end': 130.0, 'confidence': 0.8, 'sponsor': 'Bolt',
         'reviewer_verdict': 'confirmed'},
    ])
    body = app_client.get(
        '/api/v1/detections?status=all&reviewer=adjusted').get_json()
    assert body['total'] == 1
    assert body['cutSummary']['count'] == 1
    d = body['detections'][0]
    assert d['reviewerVerdict'] == 'adjust'
    assert d['reviewerOriginalStart'] == 8.0
    assert d['reviewerOriginalEnd'] == 41.0
    body = app_client.get(
        '/api/v1/detections?status=all&reviewer=unadjusted').get_json()
    assert [d['start'] for d in body['detections']] == [100.0]
    assert body['detections'][0]['reviewerOriginalStart'] is None


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


@pytest.fixture
def seeded_categories(app_client):
    """Two cut detections with categories plus one uncategorised, so the
    category filter and the cut summary have something to separate."""
    db = get_database()
    slug = 'category-feed'
    db.create_podcast(slug, 'https://example.com/cat.xml', title='Category Feed')
    markers = [
        {'start': 10.0, 'end': 40.0, 'confidence': 0.9, 'sponsor': 'Acme',
         'category': 'sponsor', 'action_applied': 'remove'},
        {'start': 60.0, 'end': 100.0, 'confidence': 0.8, 'sponsor': 'Acme',
         'category': 'cross_promo', 'action_applied': 'remove'},
        {'start': 150.0, 'end': 170.0, 'confidence': 0.7},
        {'start': 300.0, 'end': 330.0, 'confidence': 0.4, 'was_cut': False,
         'category': 'sponsor', 'validation': {'decision': 'REJECT'}},
    ]
    db.upsert_episode(slug, 'cat-ep-1',
                      original_url='https://example.com/c1.mp3',
                      title='Episode One', status='processed')
    db.save_episode_details(slug, 'cat-ep-1', ad_markers=markers)
    yield {'slug': slug, 'db': db}
    db.delete_podcast(slug)


def test_rows_carry_category_and_action(app_client, seeded_categories):
    _csrf(app_client)
    body = app_client.get('/api/v1/detections?status=all').get_json()
    by_start = {d['start']: d for d in body['detections']}
    assert by_start[10.0]['category'] == 'sponsor'
    assert by_start[10.0]['actionApplied'] == 'remove'
    assert by_start[150.0]['category'] is None


def test_category_filter_narrows_rows(app_client, seeded_categories):
    _csrf(app_client)
    body = app_client.get(
        '/api/v1/detections?status=all&category=cross_promo').get_json()
    assert [d['start'] for d in body['detections']] == [60.0]


def test_category_none_matches_uncategorised(app_client, seeded_categories):
    _csrf(app_client)
    body = app_client.get('/api/v1/detections?status=all&category=none').get_json()
    assert [d['start'] for d in body['detections']] == [150.0]


def test_invalid_category_is_rejected(app_client, seeded_categories):
    _csrf(app_client)
    resp = app_client.get('/api/v1/detections?category=banana')
    assert resp.status_code == 400


def test_cut_summary_counts_only_cut_detections(app_client, seeded_categories):
    _csrf(app_client)
    body = app_client.get('/api/v1/detections?status=all').get_json()
    cut = body['cutSummary']
    # The REJECT marker was not cut and must not appear in any total.
    assert cut['count'] == 3
    assert cut['durationSeconds'] == 90.0
    assert cut['byCategory']['sponsor'] == 1
    assert cut['byCategory']['cross_promo'] == 1
    assert cut['byCategory']['none'] == 1
    assert cut['distinctSponsors'] == 1
    assert cut['distinctPodcasts'] == 1


def test_cut_summary_survives_the_category_filter(app_client, seeded_categories):
    """Filtering to one category must not collapse the breakdown, or the header
    could never show the mix it exists to show."""
    _csrf(app_client)
    body = app_client.get(
        '/api/v1/detections?status=all&category=sponsor').get_json()
    # Both sponsor markers, cut and rejected: category is orthogonal to status.
    assert sorted(d['start'] for d in body['detections']) == [10.0, 300.0]
    assert body['cutSummary']['byCategory']['cross_promo'] == 1
    assert body['cutSummary']['count'] == 3


def test_detections_carry_the_episode_duration(app_client, seeded_detections):
    """The waveform editor slices its window against this; without it the
    editor assumes a short default and opens on the wrong part of the
    episode at max zoom."""
    _csrf(app_client)
    body = app_client.get('/api/v1/detections?status=all').get_json()
    assert body['detections']
    assert all(d['episodeDuration'] == 3600.0 for d in body['detections'])
