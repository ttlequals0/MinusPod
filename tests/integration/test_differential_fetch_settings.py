"""Per-feed differential fetch setting + DAI-likelihood hint (Layer 3).

Feeds API:
- PATCH differentialFetchEnabled true/false/null round-trips.
- Non-bool non-null values rejected with 400.
- GET detail surfaces the flag and the daiLikely hint.
Resolver:
- resolve_differential_fetch_enabled reads the column (1/0/NULL -> True/False/False).
"""
import os
import sys
import tempfile

import pytest

from tests.app_bootstrap import authenticate_test_client

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='diff-fetch-test-'))


@pytest.fixture
def _auth(monkeypatch):
    monkeypatch.delenv('ADMIN_PASSWORD', raising=False)
    yield


@pytest.fixture
def seeded_feed(app_client):
    from api import get_database
    from werkzeug.security import generate_password_hash
    db = get_database()
    db.set_setting('app_password', generate_password_hash('DiffTest123!', method='scrypt'))
    slug = 'diff-fetch-feed'
    db.create_podcast(slug, 'https://example.com/feed.xml', title='Diff Fetch Test')
    yield {'slug': slug, 'db': db}
    try:
        db.delete_podcast(slug)
    finally:
        db.set_setting('app_password', '')


def _csrf(app_client):
    return authenticate_test_client(app_client)


def test_patch_flag_true(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'differentialFetchEnabled': True},
                         headers=_csrf(app_client))
    assert r.status_code == 200
    assert r.get_json()['differentialFetchEnabled'] is True


def test_patch_flag_null_clears(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    hdr = _csrf(app_client)
    app_client.patch(f'/api/v1/feeds/{slug}',
                     json={'differentialFetchEnabled': True}, headers=hdr)
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'differentialFetchEnabled': None}, headers=hdr)
    assert r.status_code == 200
    assert r.get_json()['differentialFetchEnabled'] is None
    assert r.get_json()['differentialFetchMode'] == 'auto'


def test_mode_inherit_follows_global_and_preserves_explicit_override(
        app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    db = seeded_feed['db']
    db.set_setting('differential_fetch_mode', 'off', is_default=False)
    hdr = _csrf(app_client)
    inherited = app_client.patch(
        f'/api/v1/feeds/{slug}', json={'differentialFetchMode': 'inherit'}, headers=hdr)
    assert inherited.status_code == 200
    assert inherited.get_json()['differentialFetchMode'] == 'inherit'
    inherited_detail = app_client.get(f'/api/v1/feeds/{slug}').get_json()
    assert inherited_detail['differentialFetchEffective'] is False
    explicit = app_client.patch(
        f'/api/v1/feeds/{slug}', json={'differentialFetchMode': 'on'}, headers=hdr)
    assert explicit.status_code == 200
    assert explicit.get_json()['differentialFetchMode'] == 'on'
    explicit_detail = app_client.get(f'/api/v1/feeds/{slug}').get_json()
    assert explicit_detail['differentialFetchEffective'] is True


def test_legacy_null_patch_selects_auto_and_new_feed_starts_inherited(
        app_client, seeded_feed, _auth):
    db = seeded_feed['db']
    row = db.get_podcast_by_slug(seeded_feed['slug'])
    assert row['differential_fetch_mode'] is None
    hdr = _csrf(app_client)
    response = app_client.patch(
        f"/api/v1/feeds/{seeded_feed['slug']}",
        json={'differentialFetchEnabled': None}, headers=hdr)
    assert response.get_json()['differentialFetchMode'] == 'auto'


@pytest.mark.parametrize('legacy, expected', [(1, 'on'), (0, 'off'), (None, 'auto')])
def test_differential_migration_preserves_legacy_values_once(seeded_feed, legacy, expected):
    db = seeded_feed['db']
    slug = seeded_feed['slug']
    db.update_podcast(slug, differential_fetch_enabled=legacy, differential_fetch_mode=None)
    conn = db.get_connection()
    conn.execute("DELETE FROM schema_migrations WHERE name = 'feed_processing_defaults_763'")
    conn.commit()
    db._run_feed_processing_defaults_migration(conn)
    row = db.get_podcast_by_slug(slug)
    assert row['differential_fetch_mode'] == expected
    db.update_podcast(slug, differential_fetch_enabled=1, differential_fetch_mode='on')
    db._run_feed_processing_defaults_migration(conn)
    assert db.get_podcast_by_slug(slug)['differential_fetch_mode'] == 'on'


def test_processing_defaults_migration_survives_database_reopen(tmp_path):
    from database import Database

    previous = Database._instance
    Database._instance = None
    try:
        db = Database(data_dir=str(tmp_path))
        values = {'migration-on': 1, 'migration-off': 0, 'migration-inherit': None}
        for slug, value in values.items():
            db.create_podcast(slug, f'https://example.com/{slug}.xml', title=slug)
            db.update_podcast(slug, differential_fetch_enabled=value,
                              differential_fetch_mode=None, skip_second_pass=value)
        conn = db.get_connection()
        conn.execute(
            "DELETE FROM schema_migrations WHERE name = 'feed_processing_defaults_763'")
        conn.commit()

        Database._instance = None
        reopened = Database(data_dir=str(tmp_path))
        rows = {
            slug: reopened.get_podcast_by_slug(slug)
            for slug in values
        }
        assert rows['migration-on']['differential_fetch_mode'] == 'on'
        assert rows['migration-off']['differential_fetch_mode'] == 'off'
        assert rows['migration-inherit']['differential_fetch_mode'] == 'auto'
        assert {slug: rows[slug]['skip_second_pass'] for slug in values} == values
        assert {
            slug: (rows[slug]['source_url'], rows[slug]['title'])
            for slug in values
        } == {
            slug: (f'https://example.com/{slug}.xml', slug)
            for slug in values
        }

        reopened.update_podcast(
            'migration-inherit', differential_fetch_mode='off')
        Database._instance = None
        reopened = Database(data_dir=str(tmp_path))
        assert reopened.get_podcast_by_slug(
            'migration-inherit')['differential_fetch_mode'] == 'off'

        reopened.create_podcast(
            'created-after-migration',
            'https://example.com/created-after-migration.xml',
            title='Created after migration',
        )
        created = reopened.get_podcast_by_slug('created-after-migration')
        assert created['differential_fetch_mode'] is None
        assert created['skip_second_pass'] is None
    finally:
        Database._instance = previous


def test_patch_flag_non_bool_rejected(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    r = app_client.patch(f'/api/v1/feeds/{slug}',
                         json={'differentialFetchEnabled': 'yes'},
                         headers=_csrf(app_client))
    assert r.status_code == 400


def test_get_detail_surfaces_flag_and_dai_hint(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    db = seeded_feed['db']
    db.upsert_episode(slug, 'ep-dai',
                      original_url='https://traffic.megaphone.fm/EP1.mp3',
                      title='DAI episode')
    _csrf(app_client)
    r = app_client.get(f'/api/v1/feeds/{slug}')
    assert r.status_code == 200
    body = r.get_json()
    assert body['differentialFetchEnabled'] is None
    assert body['daiLikely'] is True


def test_dai_hint_false_for_plain_cdn(app_client, seeded_feed, _auth):
    slug = seeded_feed['slug']
    db = seeded_feed['db']
    db.upsert_episode(slug, 'ep-plain',
                      original_url='https://cdn.example.com/EP1.mp3',
                      title='Plain episode')
    _csrf(app_client)
    r = app_client.get(f'/api/v1/feeds/{slug}')
    assert r.get_json()['daiLikely'] is False


def test_resolver_reads_column_tristate(seeded_feed):
    """NULL means unset (auto), 1/0 are explicit -- the pipeline gate needs
    all three states (#519)."""
    from config import resolve_differential_fetch_setting
    db = seeded_feed['db']
    slug = seeded_feed['slug']
    podcast_id = db.get_podcast_by_slug(slug)['id']
    assert resolve_differential_fetch_setting(db, podcast_id) is None
    db.update_podcast(slug, differential_fetch_enabled=1)
    assert resolve_differential_fetch_setting(db, podcast_id) is True
    db.update_podcast(slug, differential_fetch_enabled=0)
    assert resolve_differential_fetch_setting(db, podcast_id) is False
