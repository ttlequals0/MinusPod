"""podpingCoverage: the three states plus the podping-disabled case."""
import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('podping_coverage_test_')

from main_app import app
from database import Database

SLUG = 'coverage-feed'


@pytest.fixture
def db():
    return Database()


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        with c.session_transaction() as sess:
            sess['authenticated'] = True
        yield c


@pytest.fixture
def feed(db):
    db.set_setting('podping_enabled', 'true', is_default=False)
    db.create_podcast(SLUG, 'https://feeds.megaphone.fm/coverage', 'Coverage Feed')
    conn = db.get_connection()
    conn.execute("DELETE FROM podping_hosts")
    conn.commit()
    yield SLUG
    db.delete_podcast(SLUG)


def _detail(client, slug):
    response = client.get(f'/api/v1/feeds/{slug}')
    assert response.status_code == 200
    return response.get_json()


def _from_list(client, slug):
    response = client.get('/api/v1/feeds')
    assert response.status_code == 200
    return next(f for f in response.get_json()['feeds'] if f['slug'] == slug)


def test_unseen_when_host_never_pinged(client, feed):
    assert _detail(client, feed)['podpingCoverage'] == 'unseen'
    assert _from_list(client, feed)['podpingCoverage'] == 'unseen'


def test_host_active_when_the_host_pings_but_this_feed_has_not(client, db, feed):
    db.record_podping_hosts({'feeds.megaphone.fm': 4})
    assert _detail(client, feed)['podpingCoverage'] == 'host_active'
    assert _from_list(client, feed)['podpingCoverage'] == 'host_active'


def test_received_wins_over_host_state(client, db, feed):
    db.record_podping_hosts({'feeds.megaphone.fm': 4})
    db.set_last_podping_at(feed)
    payload = _detail(client, feed)
    assert payload['podpingCoverage'] == 'received'
    assert payload['lastPodpingAt'] is not None


def test_received_even_when_the_host_is_not_currently_active(client, db, feed):
    db.set_last_podping_at(feed)
    assert _detail(client, feed)['podpingCoverage'] == 'received'


def test_coverage_is_null_when_podping_is_disabled(client, db, feed):
    db.set_setting('podping_enabled', 'false', is_default=False)
    try:
        assert _detail(client, feed)['podpingCoverage'] is None
        assert _from_list(client, feed)['podpingCoverage'] is None
    finally:
        db.set_setting('podping_enabled', 'true', is_default=False)


def test_a_different_host_does_not_grant_coverage(client, db, feed):
    db.record_podping_hosts({'anchor.fm': 9})
    assert _detail(client, feed)['podpingCoverage'] == 'unseen'


def test_declined_when_the_feed_opts_out(client, db, feed):
    db.set_podping_declaration(feed, False, [])
    db.record_podping_hosts({'feeds.megaphone.fm': 4})
    assert _detail(client, feed)['podpingCoverage'] == 'declined'
    assert _from_list(client, feed)['podpingCoverage'] == 'declined'


def test_declined_outranks_a_previously_received_ping(client, db, feed):
    db.set_last_podping_at(feed)
    db.set_podping_declaration(feed, False, [])
    assert _detail(client, feed)['podpingCoverage'] == 'declined'


def test_opting_in_does_not_by_itself_grant_coverage(client, db, feed):
    db.set_podping_declaration(feed, True, ['podping.aaa'])
    assert _detail(client, feed)['podpingCoverage'] == 'unseen'
