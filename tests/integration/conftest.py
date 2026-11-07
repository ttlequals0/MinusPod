"""Shared fixtures for integration tests."""
import pytest


@pytest.fixture
def _auth(monkeypatch):
    monkeypatch.delenv('ADMIN_PASSWORD', raising=False)
    yield


@pytest.fixture
def seeded_feed(app_client):
    from api import get_database
    from werkzeug.security import generate_password_hash
    db = get_database()
    # Feed-mutating endpoints require an app password to be configured.
    db.set_setting('app_password', generate_password_hash('OverrideTest123!', method='scrypt'))
    slug = 'override-endpoint-feed'
    db.create_podcast(slug, 'https://example.com/feed.xml', title='Source Show')
    yield {'slug': slug, 'db': db}
    try:
        db.delete_podcast(slug)
    finally:
        # Restore the no-password state so this doesn't leak into other
        # integration tests that share the app_client database.
        db.set_setting('app_password', '')
