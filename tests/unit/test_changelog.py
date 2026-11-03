"""Changelog parser and GET /api/v1/changelog."""
import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('changelog_test_')

import changelog
from api import get_database
from main_app import app

SAMPLE = """# Changelog

## [Unreleased]

- not shipped

## [2.101.16] - 2026-11-03

### Added
- A long bullet that wraps
  onto a second line (#123)

## [2.101.14] - 2026-11-02

### Fixed
- Older fix

## [2.9.1] - 2026-10-01

- Oldest
"""


def test_parse_skips_unreleased_and_keeps_order():
    sections = changelog.parse_changelog(SAMPLE)
    assert [s['version'] for s in sections] == ['2.101.16', '2.101.14', '2.9.1']
    assert sections[0]['date'] == '2026-11-03'


def test_parse_unwraps_and_strips_header():
    body = changelog.parse_changelog(SAMPLE)[0]['body']
    assert body.startswith('### Added')
    assert '- A long bullet that wraps onto a second line (#123)' in body
    assert '## [' not in body


def test_load_sections_missing_file_is_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(changelog, 'CHANGELOG_PATH', tmp_path / 'none.md')
    monkeypatch.setattr(changelog, '_cache', None)
    assert changelog.load_sections() == []


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(changelog, '_cache', changelog.parse_changelog(SAMPLE))
    db = get_database()
    app.config['TESTING'] = True
    db.set_setting('app_password', 'pbkdf2:sha256:fake')
    with app.test_client() as test_client:
        with test_client.session_transaction() as session:
            session['authenticated'] = True
            session['auth_generation'] = db.get_setting_int('auth_session_generation', 0)
        yield test_client
    db.clear_setting('app_password')


def _versions(resp):
    assert resp.status_code == 200
    return [s['version'] for s in resp.get_json()['sections']]


def test_no_since_returns_newest_up_to_file_length(client):
    resp = client.get('/api/v1/changelog')
    assert _versions(resp) == ['2.101.16', '2.101.14', '2.9.1']
    assert resp.get_json()['current']


def test_since_filters_numerically(client):
    assert _versions(client.get('/api/v1/changelog?since=2.9.1')) == ['2.101.16', '2.101.14']
    assert _versions(client.get('/api/v1/changelog?since=2.101.16')) == []


def test_bad_since_is_400(client):
    resp = client.get('/api/v1/changelog?since=banana')
    assert resp.status_code == 400
    assert resp.get_json() == {'error': 'bad_since'}


def test_limit_zero_is_400(client):
    assert client.get('/api/v1/changelog?limit=0').status_code == 400


def test_limit_applies_without_since(client):
    assert _versions(client.get('/api/v1/changelog?limit=1')) == ['2.101.16']


def test_limit_clamps_to_50(client, monkeypatch):
    many = [{'version': f'3.0.{i}', 'date': '', 'raw': '', 'body': ''} for i in range(80, 0, -1)]
    monkeypatch.setattr(changelog, '_cache', many)
    assert len(_versions(client.get('/api/v1/changelog?limit=500'))) == 50
