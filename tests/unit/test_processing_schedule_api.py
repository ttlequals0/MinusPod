"""REST contract for the processing schedule and its status/queue surfaces (#992)."""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='schedule-api-test-'))

URL = '/api/v1/settings/processing-schedule'
DEFAULTS = {'enabled': False, 'start': '00:00', 'end': '06:00',
            'days': ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun']}


@pytest.fixture
def client(app_client):
    from api import get_database
    import processing_schedule as ps
    db = get_database()
    with app_client.session_transaction() as sess:
        sess['authenticated'] = True
    app_client.get('/api/v1/auth/status')
    ps.save_processing_schedule(db, **DEFAULTS)
    yield app_client
    ps.save_processing_schedule(db, enabled=False)


def test_get_returns_the_defaults(client):
    body = client.get(URL).get_json()
    assert {k: body[k] for k in DEFAULTS} == DEFAULTS
    assert body['open'] is True
    assert body['nextChangeAt'] is None
    assert isinstance(body['timezone'], str)


def test_put_updates_every_field(client):
    response = client.put(URL, json={
        'enabled': True, 'start': '22:00', 'end': '06:00', 'days': ['sat', 'fri']})
    assert response.status_code == 200
    body = response.get_json()
    assert (body['enabled'], body['start'], body['end'], body['days']) == (
        True, '22:00', '06:00', ['fri', 'sat'])
    assert client.get(URL).get_json() == body


def test_put_partial_update_keeps_other_fields(client):
    client.put(URL, json={'start': '08:30'})
    body = client.get(URL).get_json()
    assert body['start'] == '08:30'
    assert body['end'] == '06:00'
    assert body['enabled'] is False


def test_put_ignores_read_only_fields(client):
    before = client.get(URL).get_json()
    response = client.put(URL, json={'timezone': 'Asia/Tokyo', 'open': False,
                                     'nextChangeAt': '2030-01-01T00:00:00Z', 'start': '01:00'})
    assert response.status_code == 200
    body = response.get_json()
    assert body['timezone'] == before['timezone']
    assert body['open'] is True
    assert body['nextChangeAt'] is None
    assert body['start'] == '01:00'


def test_put_accepts_the_get_body_back(client):
    assert client.put(URL, json=client.get(URL).get_json()).status_code == 200


@pytest.mark.parametrize('body,message', [
    ({'enabled': 'yes'}, 'enabled must be a boolean'),
    ({'start': '9:00'}, 'start must be a time in HH:MM 24-hour format'),
    ({'start': 900}, 'start must be a time in HH:MM 24-hour format'),
    ({'end': '24:00'}, 'end must be a time in HH:MM 24-hour format'),
    ({'days': []}, 'days must be a non-empty list of mon, tue, wed, thu, fri, sat, sun'),
    ({'days': ['mon', 'mon']}, 'days must be a non-empty list of mon, tue, wed, thu, fri, sat, sun'),
    ({'days': ['monday']}, 'days must be a non-empty list of mon, tue, wed, thu, fri, sat, sun'),
    ({'days': 'mon'}, 'days must be a non-empty list of mon, tue, wed, thu, fri, sat, sun'),
    ({}, 'No data provided'),
    ([], 'No data provided'),
])
def test_put_rejects_bad_bodies(client, body, message):
    response = client.put(URL, json=body)
    assert response.status_code == 400
    assert response.get_json()['error'] == message


def test_a_bad_field_leaves_nothing_half_applied(client):
    before = client.get(URL).get_json()
    response = client.put(URL, json={'enabled': True, 'start': '10:00', 'end': 'nope'})
    assert response.status_code == 400
    assert client.get(URL).get_json() == before


def test_status_carries_the_schedule_state(client):
    assert client.get('/api/v1/status').get_json()['processingSchedule'] == {
        'enabled': False, 'open': True, 'nextChangeAt': None}
    client.put(URL, json={'enabled': True, 'start': '00:00', 'end': '00:00'})
    assert client.get('/api/v1/status').get_json()['processingSchedule'] == {
        'enabled': True, 'open': True, 'nextChangeAt': None}


def test_queue_rows_carry_the_outside_schedule_reason(client, monkeypatch):
    import processing_schedule as ps
    from datetime import datetime, timezone
    from api import get_database
    db = get_database()
    db.create_podcast('schedule-feed', 'https://example.com/feed.xml', 'Schedule Pod')
    try:
        podcast_id = db.get_podcast_by_slug('schedule-feed')['id']
        conn = db.get_connection()
        conn.execute(
            """INSERT INTO auto_process_queue
               (podcast_id, episode_id, original_url, title, status, priority)
               VALUES (?, 'a1b2c3d4e5f6', 'https://example.com/e.mp3', 'One episode', 'pending', 0)""",
            (podcast_id,))
        conn.commit()
        monkeypatch.setattr(ps, 'utc_now', lambda: datetime(2026, 1, 14, 20, 0, tzinfo=timezone.utc))
        client.put(URL, json={'enabled': True, 'start': '09:00', 'end': '17:00',
                              'days': ['mon', 'tue', 'wed', 'thu', 'fri']})
        rows = [e for e in client.get('/api/v1/episodes/processing').get_json()
                if e['slug'] == 'schedule-feed']
        assert rows[0]['admission'] == {
            'blocked': True, 'phase': None, 'slot': None,
            'reason': 'outside_schedule', 'resumesAt': '2026-01-15T09:00:00Z'}
    finally:
        db.get_connection().execute("DELETE FROM auto_process_queue")
        db.get_connection().commit()
        db.delete_podcast('schedule-feed')
