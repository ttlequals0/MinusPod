"""POST /feeds/<slug>/episodes/passthrough (#746): bulk-capable per-episode
pass-through override endpoint. Mirrors test_local_episode_api.py's fixture
style (shared app_client fixture from conftest.py, local _authed/_csrf_headers
helpers) and test_podping_hosts_api.py's auth-gate pattern (the blueprint
serves everything unauthenticated while no app password is set).
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='passthrough-api-test-'))

EP1 = 'aa11bb22cc33'
EP2 = 'bb22cc33dd44'
EP_ACTIVE = 'cc33dd44ee55'


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')


def _csrf_headers(client):
    csrf = None
    for cookie in client._cookies.values():
        if cookie.key == 'minuspod_csrf':
            csrf = cookie.value
    return {'X-CSRF-Token': csrf} if csrf else {}


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    try:
        from api import limiter
        limiter.reset()
    except Exception:
        pass
    yield


@pytest.fixture
def subscribed_feed(app_client):
    from api import get_database
    db = get_database()
    slug = 'passthrough-api-subscribed'
    db.create_podcast(slug, 'https://example.com/feed.xml', 'Subscribed Test')
    yield {'slug': slug, 'db': db}
    db.delete_podcast(slug)


def _seed_episode(db, slug, episode_id, **kwargs):
    defaults = dict(
        title=f'Episode {episode_id}', status='discovered',
        original_url=f'https://example.com/{episode_id}.mp3',
    )
    defaults.update(kwargs)
    db.upsert_episode(slug, episode_id, **defaults)
    return db.get_episode(slug, episode_id)


class TestSetEpisodesPassthroughEndpoint:
    def test_requires_authentication_when_a_password_is_set(
            self, app_client, subscribed_feed):
        # The blueprint serves everything unauthenticated while no app
        # password exists, so the gate only means anything once one is set.
        slug = subscribed_feed['slug']
        db = subscribed_feed['db']
        db.set_setting('app_password', 'pbkdf2:sha256:fake', is_default=False)
        try:
            from main_app import app
            app.config['TESTING'] = True
            with app.test_client() as anon:
                response = anon.post(
                    f'/api/v1/feeds/{slug}/episodes/passthrough',
                    json={'episodeIds': [EP1], 'enabled': True},
                )
                assert response.status_code == 401
        finally:
            db.set_setting('app_password', '', is_default=False)

    def test_requires_csrf_token_when_a_password_is_set(
            self, app_client, subscribed_feed):
        slug = subscribed_feed['slug']
        db = subscribed_feed['db']
        db.set_setting('app_password', 'pbkdf2:sha256:fake', is_default=False)
        try:
            from api.auth_state import SESSION_GENERATION_KEY, current_generation
            with app_client.session_transaction() as sess:
                sess['authenticated'] = True
                sess[SESSION_GENERATION_KEY] = current_generation(db)
            response = app_client.post(
                f'/api/v1/feeds/{slug}/episodes/passthrough',
                json={'episodeIds': [EP1], 'enabled': True},
            )
            assert response.status_code == 403
        finally:
            db.set_setting('app_password', '', is_default=False)

    def test_enable_sets_flag_and_enqueues(self, app_client, subscribed_feed):
        slug = subscribed_feed['slug']
        db = subscribed_feed['db']
        _seed_episode(db, slug, EP1, status='processed')
        _seed_episode(db, slug, EP2, status='discovered')

        _authed(app_client)
        response = app_client.post(
            f'/api/v1/feeds/{slug}/episodes/passthrough',
            json={'episodeIds': [EP1, EP2], 'enabled': True},
            headers=_csrf_headers(app_client),
        )

        assert response.status_code == 200
        body = response.get_json()
        assert body['updated'] == 2
        assert body['queued'] == 2

        ep1 = db.get_episode(slug, EP1)
        assert ep1['passthrough_enabled'] == 1
        assert ep1['status'] == 'pending'

        podcast = db.get_podcast_by_slug(slug)
        queued_ids = {row['episode_id'] for row in db.get_connection().execute(
            "SELECT episode_id FROM auto_process_queue WHERE podcast_id = ?",
            (podcast['id'],),
        )}
        assert queued_ids == {EP1, EP2}

    def test_enable_rejects_actively_processing_episode(self, app_client, subscribed_feed):
        slug = subscribed_feed['slug']
        db = subscribed_feed['db']
        _seed_episode(db, slug, EP_ACTIVE, status='processing')

        _authed(app_client)
        response = app_client.post(
            f'/api/v1/feeds/{slug}/episodes/passthrough',
            json={'episodeIds': [EP_ACTIVE], 'enabled': True},
            headers=_csrf_headers(app_client),
        )

        assert response.status_code == 200
        body = response.get_json()
        # The run that owns the episode already resolved its mode, so the flag
        # is not written either: it would claim a pass-through that never ran.
        assert body['updated'] == 0
        assert body['queued'] == 0
        assert body['accepted'] == []
        assert body['rejected'] == [{'episodeId': EP_ACTIVE, 'reason': 'processing'}]
        assert not db.get_episode(slug, EP_ACTIVE)['passthrough_enabled']

    def test_enable_rejects_episode_owned_by_an_active_run(self, app_client, subscribed_feed):
        slug = subscribed_feed['slug']
        db = subscribed_feed['db']
        # Claimed run, episode status not yet flipped: the ownership registry
        # is what makes this visible.
        _seed_episode(db, slug, EP_ACTIVE, status='pending')
        podcast = db.get_podcast_by_slug(slug)
        conn = db.get_connection()
        conn.execute(
            "INSERT INTO processing_runs (run_id, podcast_id, episode_id, owner_pid, state) "
            "VALUES ('run-passthrough-1', ?, ?, ?, 'running')",
            (podcast['id'], EP_ACTIVE, os.getpid()),
        )
        conn.commit()
        try:
            _authed(app_client)
            response = app_client.post(
                f'/api/v1/feeds/{slug}/episodes/passthrough',
                json={'episodeIds': [EP_ACTIVE], 'enabled': True},
                headers=_csrf_headers(app_client),
            )
            body = response.get_json()
            assert body['rejected'] == [{'episodeId': EP_ACTIVE, 'reason': 'processing'}]
            assert body['updated'] == 0
        finally:
            conn.execute("DELETE FROM processing_runs WHERE run_id = 'run-passthrough-1'")
            conn.commit()

    def test_unknown_ids_are_rejected_per_item(self, app_client, subscribed_feed):
        slug = subscribed_feed['slug']
        db = subscribed_feed['db']
        _seed_episode(db, slug, EP1, status='processed')

        _authed(app_client)
        response = app_client.post(
            f'/api/v1/feeds/{slug}/episodes/passthrough',
            json={'episodeIds': [EP1, 'ffffffffffff'], 'enabled': True},
            headers=_csrf_headers(app_client),
        )

        body = response.get_json()
        assert body['accepted'] == [EP1]
        assert body['rejected'] == [{'episodeId': 'ffffffffffff', 'reason': 'not_found'}]
        assert body['queued'] == 1
        assert body['jobState'] == 'queued'

    def test_duplicate_ids_are_counted_once(self, app_client, subscribed_feed):
        slug = subscribed_feed['slug']
        db = subscribed_feed['db']
        _seed_episode(db, slug, EP1, status='processed')

        _authed(app_client)
        response = app_client.post(
            f'/api/v1/feeds/{slug}/episodes/passthrough',
            json={'episodeIds': [EP1, EP1, EP1], 'enabled': True},
            headers=_csrf_headers(app_client),
        )

        body = response.get_json()
        assert body['accepted'] == [EP1]
        assert body['updated'] == 1
        assert body['queued'] == 1

    def test_disable_clears_flag_without_enqueueing(self, app_client, subscribed_feed):
        slug = subscribed_feed['slug']
        db = subscribed_feed['db']
        _seed_episode(db, slug, EP1, status='processed')
        db.set_episodes_passthrough(slug, [EP1], True)

        _authed(app_client)
        response = app_client.post(
            f'/api/v1/feeds/{slug}/episodes/passthrough',
            json={'episodeIds': [EP1], 'enabled': False},
            headers=_csrf_headers(app_client),
        )

        assert response.status_code == 200
        body = response.get_json()
        assert body['updated'] == 1
        assert body['queued'] == 0
        ep1 = db.get_episode(slug, EP1)
        assert ep1['passthrough_enabled'] == 0
        # Status untouched; disabling does not force a reprocess.
        assert ep1['status'] == 'processed'

    def test_missing_episode_ids_400(self, app_client, subscribed_feed):
        slug = subscribed_feed['slug']
        _authed(app_client)
        response = app_client.post(
            f'/api/v1/feeds/{slug}/episodes/passthrough',
            json={'episodeIds': [], 'enabled': True},
            headers=_csrf_headers(app_client),
        )
        assert response.status_code == 400

    @pytest.mark.parametrize('payload', [
        [EP1],
        {'episodeIds': EP1, 'enabled': True},
        {'episodeIds': [EP1, 7], 'enabled': True},
        {'episodeIds': [''], 'enabled': True},
    ])
    def test_malformed_body_400(self, app_client, subscribed_feed, payload):
        slug = subscribed_feed['slug']
        _authed(app_client)
        response = app_client.post(
            f'/api/v1/feeds/{slug}/episodes/passthrough',
            json=payload,
            headers=_csrf_headers(app_client),
        )
        assert response.status_code == 400

    def test_non_boolean_enabled_400(self, app_client, subscribed_feed):
        slug = subscribed_feed['slug']
        _authed(app_client)
        response = app_client.post(
            f'/api/v1/feeds/{slug}/episodes/passthrough',
            json={'episodeIds': [EP1], 'enabled': 'yes'},
            headers=_csrf_headers(app_client),
        )
        assert response.status_code == 400

    def test_unknown_feed_404(self, app_client):
        _authed(app_client)
        response = app_client.post(
            '/api/v1/feeds/no-such-feed/episodes/passthrough',
            json={'episodeIds': [EP1], 'enabled': True},
            headers=_csrf_headers(app_client),
        )
        assert response.status_code == 404


class TestEpisodeSerializationCarriesPassthroughFlag:
    def test_list_and_detail_expose_passthrough_enabled(self, app_client, subscribed_feed):
        slug = subscribed_feed['slug']
        db = subscribed_feed['db']
        _seed_episode(db, slug, EP1, status='processed')
        _seed_episode(db, slug, EP2, status='processed')
        db.set_episodes_passthrough(slug, [EP1], True)

        _authed(app_client)
        list_resp = app_client.get(f'/api/v1/feeds/{slug}/episodes')
        assert list_resp.status_code == 200
        by_id = {ep['id']: ep for ep in list_resp.get_json()['episodes']}
        assert by_id[EP1]['passthroughEnabled'] is True
        assert by_id[EP2]['passthroughEnabled'] is False

        detail_resp = app_client.get(f'/api/v1/feeds/{slug}/episodes/{EP1}')
        assert detail_resp.status_code == 200
        assert detail_resp.get_json()['passthroughEnabled'] is True
