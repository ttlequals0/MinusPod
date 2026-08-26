"""Integration tests for GET /stats/addressing (random addressing mode A/B
tracking; mirrors /stats/reviewer's app_client pattern)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')


def test_addressing_stats_returns_both_modes_zeroed_by_default(app_client):
    _authed(app_client)
    resp = app_client.get('/api/v1/stats/addressing')
    assert resp.status_code == 200
    data = resp.get_json()
    assert set(data['modes'].keys()) == {'timestamps', 'segment_ids'}
    for mode in ('timestamps', 'segment_ids'):
        assert data['modes'][mode]['runs'] == 0
        assert data['modes'][mode]['compliancePct'] == 0.0


def test_addressing_stats_reflects_recorded_rows(app_client):
    _authed(app_client)
    from api import get_database

    db = get_database()
    db.record_addressing_log(
        'a-show', 'ep1', 'detection', 'random', 'timestamps', 8, 6)

    resp = app_client.get('/api/v1/stats/addressing')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['modes']['timestamps']['runs'] == 1
    assert data['modes']['timestamps']['windowsJudged'] == 8
    assert data['modes']['timestamps']['windowsCompliant'] == 6
    assert data['modes']['timestamps']['compliancePct'] == 75.0


def test_addressing_stats_podcast_slug_filter(app_client):
    _authed(app_client)
    from api import get_database

    db = get_database()
    db.record_addressing_log(
        'filter-show', 'ep1', 'detection', 'segment_ids', 'segment_ids', 3, 3)

    resp = app_client.get('/api/v1/stats/addressing?podcast_slug=filter-show')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['modes']['segment_ids']['runs'] == 1

    resp_other = app_client.get('/api/v1/stats/addressing?podcast_slug=nonexistent-show')
    assert resp_other.status_code == 200
    data_other = resp_other.get_json()
    assert data_other['modes']['segment_ids']['runs'] == 0
