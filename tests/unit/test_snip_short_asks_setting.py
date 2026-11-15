"""snip_short_asks setting: global default, per-feed column, resolver, API, config transfer."""

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('snip_short_asks_setting_test_', passphrase='snip-short-asks-test-passphrase')

import database
from main_app import app


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


@pytest.fixture
def db():
    return database.Database()


@pytest.fixture
def feed_slug(db, preserve_setting):
    preserve_setting('snip_short_asks')
    slug = 'snip-short-asks-feed'
    db.create_podcast(slug, 'https://example.com/feed.xml', title='Snip Short Asks')
    yield slug
    db.delete_podcast(slug)


class TestResolver:
    def test_default_is_off(self, db, feed_slug):
        assert db.resolve_feed_bool(feed_slug, 'snip_short_asks', 'snip_short_asks') is False

    def test_per_feed_null_inherits_global(self, db, feed_slug):
        db.set_setting('snip_short_asks', 'true', is_default=False)
        assert db.resolve_feed_bool(feed_slug, 'snip_short_asks', 'snip_short_asks') is True

    def test_per_feed_off_beats_global_on(self, db, feed_slug):
        db.set_setting('snip_short_asks', 'true', is_default=False)
        db.update_podcast(feed_slug, snip_short_asks=0)
        assert db.resolve_feed_bool(feed_slug, 'snip_short_asks', 'snip_short_asks') is False

    def test_per_feed_on_beats_global_off(self, db, feed_slug):
        db.set_setting('snip_short_asks', 'false', is_default=False)
        db.update_podcast(feed_slug, snip_short_asks=1)
        assert db.resolve_feed_bool(feed_slug, 'snip_short_asks', 'snip_short_asks') is True

    def test_unknown_feed_falls_back_to_global(self, db, feed_slug):
        db.set_setting('snip_short_asks', 'true', is_default=False)
        assert db.resolve_feed_bool('no-such-feed', 'snip_short_asks', 'snip_short_asks') is True

    def test_upstream_show_segments_resolver_unchanged_in_behaviour(self, db, feed_slug):
        db.update_podcast(feed_slug, detect_show_segments=1)
        assert db.resolve_detect_show_segments(feed_slug) is True
        assert db.resolve_feed_bool(feed_slug, 'detect_show_segments', 'detect_show_segments') is True

    def test_podcast_dict_avoids_lookup(self, db, feed_slug):
        db.update_podcast(feed_slug, snip_short_asks=1)
        podcast = db.get_podcast_by_slug(feed_slug)
        assert db.resolve_feed_bool(feed_slug, 'snip_short_asks', 'snip_short_asks', podcast=podcast) is True


class TestApi:
    def test_global_put_get_round_trip(self, client, feed_slug):
        assert client.get('/api/v1/settings').get_json()['snipShortAsks']['value'] is False
        resp = client.put('/api/v1/settings/ad-detection', json={'snipShortAsks': True})
        assert resp.status_code == 200, resp.data
        assert client.get('/api/v1/settings').get_json()['snipShortAsks']['value'] is True
        client.put('/api/v1/settings/ad-detection', json={'snipShortAsks': False})
        assert client.get('/api/v1/settings').get_json()['snipShortAsks']['value'] is False

    def test_global_put_rejects_non_bool_like_feed_patch(self, client, db, feed_slug):
        db.set_setting('snip_short_asks', 'true', is_default=False)
        for bad in ('yes', 1, 0, 'false', [], {}):
            resp = client.put('/api/v1/settings/ad-detection', json={'snipShortAsks': bad})
            assert resp.status_code == 400, bad
            assert resp.get_json()['error'] == 'snipShortAsks must be true, false, or null'
            same = client.patch(f'/api/v1/feeds/{feed_slug}', json={'snipShortAsks': bad})
            assert same.get_json()['error'] == resp.get_json()['error']
        assert client.get('/api/v1/settings').get_json()['snipShortAsks']['value'] is True

    def test_global_put_null_resets_to_off_and_echoes_stored(self, client, db, feed_slug):
        resp = client.put('/api/v1/settings/ad-detection', json={'snipShortAsks': True})
        assert resp.get_json()['snipShortAsks'] is True
        resp = client.put('/api/v1/settings/ad-detection', json={'snipShortAsks': None})
        assert resp.status_code == 200
        assert resp.get_json()['snipShortAsks'] is False
        assert client.get('/api/v1/settings').get_json()['snipShortAsks']['value'] is False

    def test_global_round_trips_report_default_flag(self, client, db, feed_slug):
        def state():
            return client.get('/api/v1/settings').get_json()['snipShortAsks']

        def put(value):
            resp = client.put('/api/v1/settings/ad-detection', json={'snipShortAsks': value})
            assert resp.status_code == 200, resp.data

        db.clear_setting('snip_short_asks')
        assert state() == {'value': False, 'isDefault': True}
        put(True)
        assert state() == {'value': True, 'isDefault': False}
        put(None)
        assert state() == {'value': False, 'isDefault': True}
        put(False)
        assert state() == {'value': False, 'isDefault': False}
        put(None)
        assert state() == {'value': False, 'isDefault': True}

    def test_global_put_without_field_does_not_echo(self, client, feed_slug):
        resp = client.put('/api/v1/settings/ad-detection', json={'whatsNewOnUpgrade': True})
        assert 'snipShortAsks' not in resp.get_json()

    def test_per_feed_patch_true_false_null(self, client, db, feed_slug):
        for sent, stored in ((True, 1), (False, 0), (None, None)):
            resp = client.patch(f'/api/v1/feeds/{feed_slug}', json={'snipShortAsks': sent})
            assert resp.status_code == 200, resp.data
            assert resp.get_json()['snipShortAsks'] is sent
            assert db.get_podcast_by_slug(feed_slug)['snip_short_asks'] == stored

    def test_feed_get_reports_field(self, client, feed_slug):
        resp = client.get(f'/api/v1/feeds/{feed_slug}')
        assert 'snipShortAsks' in resp.get_json()


class TestConfigTransfer:
    def test_feed_column_is_exported_as_a_bool(self):
        import config_transfer
        assert 'snip_short_asks' in config_transfer.FEED_COLUMNS
        assert 'snip_short_asks' in config_transfer.FEED_BOOL_COLUMNS

    def test_feed_value_survives_export_import(self, db, feed_slug):
        import config_transfer
        db.update_podcast(feed_slug, snip_short_asks=1)
        row = db.get_podcast_by_slug(feed_slug)
        exported = {c: row.get(c) for c in config_transfer.FEED_COLUMNS if c in row}
        assert exported['snip_short_asks'] == 1
