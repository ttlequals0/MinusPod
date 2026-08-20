"""Integration tests for the chaptersMode per-feed setting (#560 API surface).

Mirrors test_passthrough_settings_api.py's fixture style. Covers:
- GET echoes the raw chapters_mode column (null when unset).
- PATCH sets each valid value ('auto', 'generate', 'off').
- PATCH null resets the override.
- PATCH an invalid string -> 400, column left unchanged.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='chapters-mode-api-test-'))


@pytest.fixture
def seeded_feed(app_client):
    from api import get_database
    db = get_database()
    slug = 'chapters-mode-api-feed'
    db.create_podcast(slug, 'https://example.com/feed.xml', 'Chapters Mode API Test')
    yield {'slug': slug, 'db': db}
    db.delete_podcast(slug)


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


def test_get_feed_echoes_null_chapters_mode(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)

    resp = app_client.get(f'/api/v1/feeds/{slug}')
    assert resp.status_code == 200
    assert resp.get_json()['chaptersMode'] is None


@pytest.mark.parametrize('mode', ['auto', 'generate', 'off'])
def test_patch_sets_each_valid_value(app_client, seeded_feed, mode):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'chaptersMode': mode}, headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()['chaptersMode'] == mode
    assert app_client.get(f'/api/v1/feeds/{slug}').get_json()['chaptersMode'] == mode
    assert seeded_feed['db'].get_podcast_by_slug(slug)['chapters_mode'] == mode


def test_patch_null_resets_chapters_mode(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    app_client.patch(f'/api/v1/feeds/{slug}', json={'chaptersMode': 'generate'}, headers=headers)
    resp = app_client.patch(f'/api/v1/feeds/{slug}', json={'chaptersMode': None}, headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()['chaptersMode'] is None
    assert seeded_feed['db'].get_podcast_by_slug(slug)['chapters_mode'] is None


def test_patch_invalid_value_rejected_and_column_unchanged(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    app_client.patch(f'/api/v1/feeds/{slug}', json={'chaptersMode': 'generate'}, headers=headers)
    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'chaptersMode': 'bogus'}, headers=headers)
    assert resp.status_code == 400
    body = resp.get_json()
    assert 'error' in body
    assert 'chaptersMode' in body['error']
    assert seeded_feed['db'].get_podcast_by_slug(slug)['chapters_mode'] == 'generate'


def test_a_reject_override_above_the_global_ceiling_is_rejected(app_client, seeded_feed):
    """The validator clamps it back, so the feed would show a value it never
    uses. The global hard ceiling defaults to 900s."""
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'maxAdDurationRejectOverride': 1200},
                            headers=headers)

    assert resp.status_code == 400
    assert 'cannot exceed' in resp.get_json()['error']


def test_a_reject_override_under_the_ceiling_is_accepted(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'maxAdDurationRejectOverride': 600},
                            headers=headers)

    assert resp.status_code == 200
    assert app_client.get(f'/api/v1/feeds/{slug}').get_json()[
        'maxAdDurationRejectOverride'] == 600


# -- ownEpisodeGuids (#598) --

@pytest.fixture
def no_feed_refresh(monkeypatch):
    """PATCHing ownEpisodeGuids force-refreshes the served feed; stub the fetch."""
    import main_app.feeds as feeds_mod
    monkeypatch.setattr(feeds_mod, 'refresh_rss_feed', lambda *a, **k: True)


def test_new_feed_defaults_to_own_episode_guids(app_client, seeded_feed):
    # create_podcast is the add-feed path, so a newly added feed starts True.
    _authed(app_client)
    resp = app_client.get(f'/api/v1/feeds/{seeded_feed["slug"]}')
    assert resp.status_code == 200
    assert resp.get_json()['ownEpisodeGuids'] is True


def test_patch_own_episode_guids_round_trip(app_client, seeded_feed, no_feed_refresh):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    for sent, stored in ((False, 0), (True, 1), (None, None)):
        resp = app_client.patch(f'/api/v1/feeds/{slug}',
                                json={'ownEpisodeGuids': sent}, headers=headers)
        assert resp.status_code == 200
        assert resp.get_json()['ownEpisodeGuids'] is sent
        assert seeded_feed['db'].get_podcast_by_slug(slug)['own_episode_guids'] == stored


def test_patch_own_episode_guids_rejects_non_bool(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'ownEpisodeGuids': 'yes'}, headers=headers)
    assert resp.status_code == 400
    assert 'ownEpisodeGuids' in resp.get_json()['error']
    assert seeded_feed['db'].get_podcast_by_slug(slug)['own_episode_guids'] == 1


def test_own_episode_guids_migration_idempotent(app_client, seeded_feed):
    db = seeded_feed['db']
    conn = db.get_connection()
    cols = {row['name'] for row in conn.execute("PRAGMA table_info(podcasts)").fetchall()}
    assert 'own_episode_guids' in cols
    assert db._add_column_if_missing(conn, 'podcasts', 'own_episode_guids',
                                     'INTEGER', cols) is False


# -- queuePriority (#625) --

def _insert_pending_queue_row(db, podcast_id, episode_id):
    conn = db.get_connection()
    conn.execute(
        """INSERT INTO auto_process_queue
           (podcast_id, episode_id, original_url, title, status, priority, created_at)
           VALUES (?, ?, ?, ?, 'pending', 0, datetime('now'))""",
        (podcast_id, episode_id, f'https://example.com/{episode_id}.mp3', 'Test')
    )
    conn.commit()


def test_get_feed_defaults_queue_priority_to_normal(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)

    resp = app_client.get(f'/api/v1/feeds/{slug}')
    assert resp.status_code == 200
    assert resp.get_json()['queuePriority'] == 'normal'


@pytest.mark.parametrize('value,db_value', [('high', 10), ('normal', None), ('low', -10)])
def test_patch_sets_each_queue_priority_value(app_client, seeded_feed, value, db_value):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'queuePriority': value}, headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()['queuePriority'] == value
    assert app_client.get(f'/api/v1/feeds/{slug}').get_json()['queuePriority'] == value
    assert seeded_feed['db'].get_podcast_by_slug(slug)['queue_priority'] == db_value


def test_patch_null_resets_queue_priority(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    app_client.patch(f'/api/v1/feeds/{slug}', json={'queuePriority': 'high'}, headers=headers)
    resp = app_client.patch(f'/api/v1/feeds/{slug}', json={'queuePriority': None}, headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()['queuePriority'] == 'normal'
    assert seeded_feed['db'].get_podcast_by_slug(slug)['queue_priority'] is None


def test_patch_invalid_queue_priority_rejected_and_column_unchanged(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    app_client.patch(f'/api/v1/feeds/{slug}', json={'queuePriority': 'high'}, headers=headers)
    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'queuePriority': 'urgent'}, headers=headers)
    assert resp.status_code == 400
    body = resp.get_json()
    assert 'error' in body
    assert 'queuePriority' in body['error']
    assert seeded_feed['db'].get_podcast_by_slug(slug)['queue_priority'] == 10


def test_patch_queue_priority_restamps_pending_queue_rows(app_client, seeded_feed):
    slug = seeded_feed['slug']
    db = seeded_feed['db']
    podcast_id = db.get_podcast_by_slug(slug)['id']
    _insert_pending_queue_row(db, podcast_id, 'ep-pending')
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'queuePriority': 'high'}, headers=headers)
    assert resp.status_code == 200

    conn = db.get_connection()
    row = conn.execute(
        "SELECT priority FROM auto_process_queue WHERE episode_id = 'ep-pending'"
    ).fetchone()
    assert row['priority'] == 10


def test_patch_queue_priority_same_value_skips_restamp(app_client, seeded_feed, monkeypatch):
    slug = seeded_feed['slug']
    db = seeded_feed['db']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    app_client.patch(f'/api/v1/feeds/{slug}', json={'queuePriority': 'high'}, headers=headers)

    restamp_calls = []
    monkeypatch.setattr(
        type(db), 'restamp_pending_priorities',
        lambda self, *args, **kwargs: restamp_calls.append((args, kwargs))
    )

    resp = app_client.patch(f'/api/v1/feeds/{slug}', json={'queuePriority': 'high'}, headers=headers)

    assert resp.status_code == 200
    assert restamp_calls == []


# -- titleSkipPatterns / titleSkipAction (episode title blacklist) --

def test_get_feed_defaults_title_skip_fields(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)

    resp = app_client.get(f'/api/v1/feeds/{slug}')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['titleSkipPatterns'] == []
    assert body['titleSkipAction'] == 'serve_original'


def test_patch_sets_title_skip_patterns(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'titleSkipPatterns': ['Weekly Sponsor*', 'Ad Break*']},
                            headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()['titleSkipPatterns'] == ['Weekly Sponsor*', 'Ad Break*']
    assert seeded_feed['db'].get_podcast_by_slug(slug)['title_skip_patterns'] == \
        '["Weekly Sponsor*", "Ad Break*"]'


def test_patch_null_resets_title_skip_patterns(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    app_client.patch(f'/api/v1/feeds/{slug}',
                     json={'titleSkipPatterns': ['Ad Break*']}, headers=headers)
    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'titleSkipPatterns': None}, headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()['titleSkipPatterns'] == []
    assert seeded_feed['db'].get_podcast_by_slug(slug)['title_skip_patterns'] is None


@pytest.mark.parametrize('patterns', [
    'not-a-list',
    ['x' * 201],
    [''],
    ['ok'] * 51,
])
def test_patch_invalid_title_skip_patterns_rejected(app_client, seeded_feed, patterns):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'titleSkipPatterns': patterns}, headers=headers)
    assert resp.status_code == 400
    assert seeded_feed['db'].get_podcast_by_slug(slug)['title_skip_patterns'] is None


def test_patch_sets_title_skip_action_hide(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'titleSkipAction': 'hide'}, headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()['titleSkipAction'] == 'hide'
    assert seeded_feed['db'].get_podcast_by_slug(slug)['title_skip_action'] == 'hide'


def test_patch_null_resets_title_skip_action(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    app_client.patch(f'/api/v1/feeds/{slug}', json={'titleSkipAction': 'hide'}, headers=headers)
    resp = app_client.patch(f'/api/v1/feeds/{slug}', json={'titleSkipAction': None}, headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()['titleSkipAction'] == 'serve_original'
    assert seeded_feed['db'].get_podcast_by_slug(slug)['title_skip_action'] is None


def test_patch_invalid_title_skip_action_rejected(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'titleSkipAction': 'delete'}, headers=headers)
    assert resp.status_code == 400
    assert seeded_feed['db'].get_podcast_by_slug(slug)['title_skip_action'] is None


# -- lowAdYieldAction per-feed override --

def test_get_feed_echoes_null_low_ad_yield_action(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)

    resp = app_client.get(f'/api/v1/feeds/{slug}')
    assert resp.status_code == 200
    assert resp.get_json()['lowAdYieldAction'] is None


@pytest.mark.parametrize('action', ['nothing', 'redetect', 'reprocess', 'full'])
def test_patch_sets_each_low_ad_yield_action(app_client, seeded_feed, action):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'lowAdYieldAction': action}, headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()['lowAdYieldAction'] == action
    assert seeded_feed['db'].get_podcast_by_slug(slug)['low_ad_yield_action'] == action


def test_patch_null_clears_low_ad_yield_action(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    app_client.patch(f'/api/v1/feeds/{slug}',
                     json={'lowAdYieldAction': 'full'}, headers=headers)
    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'lowAdYieldAction': None}, headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()['lowAdYieldAction'] is None
    assert seeded_feed['db'].get_podcast_by_slug(slug)['low_ad_yield_action'] is None


def test_patch_invalid_low_ad_yield_action_rejected(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    app_client.patch(f'/api/v1/feeds/{slug}',
                     json={'lowAdYieldAction': 'full'}, headers=headers)
    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'lowAdYieldAction': 'panic'}, headers=headers)
    assert resp.status_code == 400
    assert 'lowAdYieldAction' in resp.get_json()['error']
    assert seeded_feed['db'].get_podcast_by_slug(slug)['low_ad_yield_action'] == 'full'


# -- episodeLogs per-feed override (#660) --

def test_get_feed_echoes_null_episode_logs(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)

    resp = app_client.get(f'/api/v1/feeds/{slug}')
    assert resp.status_code == 200
    assert resp.get_json()['episodeLogs'] is None


@pytest.mark.parametrize('value', ['on', 'off'])
def test_patch_sets_each_episode_logs_value(app_client, seeded_feed, value):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'episodeLogs': value}, headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()['episodeLogs'] == value
    assert seeded_feed['db'].get_podcast_by_slug(slug)['episode_logs'] == value


def test_patch_null_clears_episode_logs(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    app_client.patch(f'/api/v1/feeds/{slug}',
                     json={'episodeLogs': 'off'}, headers=headers)
    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'episodeLogs': None}, headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()['episodeLogs'] is None
    assert seeded_feed['db'].get_podcast_by_slug(slug)['episode_logs'] is None


def test_patch_invalid_episode_logs_rejected(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    app_client.patch(f'/api/v1/feeds/{slug}',
                     json={'episodeLogs': 'off'}, headers=headers)
    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'episodeLogs': 'sometimes'}, headers=headers)
    assert resp.status_code == 400
    assert 'episodeLogs' in resp.get_json()['error']
    assert seeded_feed['db'].get_podcast_by_slug(slug)['episode_logs'] == 'off'
