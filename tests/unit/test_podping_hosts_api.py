"""GET /podping/hosts: the domains the listener has seen sending podpings."""
from datetime import datetime, timedelta, timezone

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('podping_hosts_api_test_')

from main_app import app
from database import Database
from utils.time import ISO_FORMAT


@pytest.fixture
def db():
    d = Database()
    conn = d.get_connection()
    conn.execute("DELETE FROM podping_hosts")
    conn.commit()
    d.set_setting('podping_enabled', 'true', is_default=False)
    return d


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['authenticated'] = True
        yield c


def _stale(db, domain, days):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(ISO_FORMAT)
    conn = db.get_connection()
    conn.execute("UPDATE podping_hosts SET last_seen_at = ? WHERE domain = ?",
                 (cutoff, domain))
    conn.commit()


def test_requires_authentication_when_a_password_is_set(db):
    # The blueprint serves everything unauthenticated while no app password
    # exists, so the gate only means anything once one is set.
    db.set_setting('app_password', 'pbkdf2:sha256:fake', is_default=False)
    app.config['TESTING'] = True
    try:
        with app.test_client() as anon:
            assert anon.get('/api/v1/podping/hosts').status_code == 401
    finally:
        db.set_setting('app_password', '', is_default=False)


def test_path_is_not_auth_exempt():
    from api import AUTH_EXEMPT_PATHS
    assert '/api/v1/podping/hosts' not in AUTH_EXEMPT_PATHS


def test_empty_table(client, db):
    payload = client.get('/api/v1/podping/hosts').get_json()
    assert payload['hosts'] == []
    assert payload['totalDomains'] == 0
    assert payload['activeDomains'] == 0
    assert payload['activeWindowDays'] == 30
    assert payload['listenerEnabled'] is True


def test_lists_domains_with_counts(client, db):
    db.record_podping_hosts({'feeds.transistor.fm': 412, 'anchor.fm': 7})
    payload = client.get('/api/v1/podping/hosts').get_json()
    rows = {h['domain']: h for h in payload['hosts']}
    assert rows['feeds.transistor.fm']['pingCount'] == 412
    assert rows['anchor.fm']['pingCount'] == 7
    assert rows['feeds.transistor.fm']['active'] is True
    assert rows['feeds.transistor.fm']['firstSeenAt']
    assert rows['feeds.transistor.fm']['lastSeenAt']
    assert payload['totalDomains'] == 2
    assert payload['activeDomains'] == 2


def test_stale_domain_is_listed_but_not_active(client, db):
    db.record_podping_hosts({'gone.example': 3, 'fresh.example': 1})
    _stale(db, 'gone.example', 31)
    payload = client.get('/api/v1/podping/hosts').get_json()
    rows = {h['domain']: h for h in payload['hosts']}
    assert rows['gone.example']['active'] is False
    assert rows['fresh.example']['active'] is True
    assert payload['totalDomains'] == 2
    assert payload['activeDomains'] == 1


def test_most_recently_active_first(client, db):
    db.record_podping_hosts({'older.example': 1})
    db.record_podping_hosts({'newer.example': 1})
    _stale(db, 'older.example', 5)
    domains = [h['domain'] for h in client.get('/api/v1/podping/hosts').get_json()['hosts']]
    assert domains.index('newer.example') < domains.index('older.example')


def test_limit_truncates_the_list_but_not_the_totals(client, db):
    db.record_podping_hosts({f'host{i}.example': 1 for i in range(5)})
    payload = client.get('/api/v1/podping/hosts?limit=2').get_json()
    assert len(payload['hosts']) == 2
    assert payload['totalDomains'] == 5


@pytest.mark.parametrize('raw,expected', [
    ('0', 1),      # clamped up
    ('-5', 1),
    ('99999', 500),  # clamped down to the cap
    ('abc', 100),    # unparseable falls back to the default
])
def test_limit_is_clamped(client, db, raw, expected):
    db.record_podping_hosts({'one.example': 1})
    response = client.get(f'/api/v1/podping/hosts?limit={raw}')
    assert response.status_code == 200
    assert response.get_json()['limit'] == expected


def test_reports_listener_disabled(client, db):
    db.set_setting('podping_enabled', 'false', is_default=False)
    try:
        db.record_podping_hosts({'kept.example': 2})
        payload = client.get('/api/v1/podping/hosts').get_json()
        # Rows already recorded stay visible; only the flag changes.
        assert payload['listenerEnabled'] is False
        assert payload['totalDomains'] == 1
    finally:
        db.set_setting('podping_enabled', 'true', is_default=False)
