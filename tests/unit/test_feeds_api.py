"""Integration tests for the chaptersMode per-feed setting (#560 API surface).

Mirrors test_passthrough_settings_api.py's fixture style. Covers:
- GET echoes the raw chapters_mode column (null when unset).
- PATCH sets each valid value ('auto', 'generate', 'off').
- PATCH null resets the override.
- PATCH an invalid string -> 400, column left unchanged.
"""
import os
import sqlite3
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


def test_patch_generate_override_rejected_for_systemone_chapter_route(
        app_client, seeded_feed):
    db = seeded_feed['db']
    original = {key: db.get_setting(key) for key in (
        'llm_provider', 'claude_model', 'chapters_enabled', 'chapters_mode')}
    try:
        db.set_setting('llm_provider', 'typesafe')
        db.set_setting('claude_model', 'jev-latest')
        db.set_setting('chapters_enabled', 'true')
        db.set_setting('chapters_mode', 'off')
        _authed(app_client)
        response = app_client.patch(
            f"/api/v1/feeds/{seeded_feed['slug']}",
            json={'chaptersMode': 'generate'}, headers=_csrf_headers(app_client))
        assert response.status_code == 400
        assert 'chapters is unsupported' in response.get_json()['error']

        response = app_client.patch(
            f"/api/v1/feeds/{seeded_feed['slug']}",
            json={'chaptersMode': 'off'}, headers=_csrf_headers(app_client))
        assert response.status_code == 200
    finally:
        for key, value in original.items():
            if value is None:
                db.clear_setting(key)
            else:
                db.set_setting(key, value)


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


def _create_network_template(db, podcast_id, network_id):
    return db.create_cue_template(
        podcast_id=podcast_id, cue_type='ad_break_boundary',
        source_episode_id='ep-1', source_offset_s=1.0, duration_s=0.5,
        sample_rate=16000, n_coeffs=13, mfcc_blob=b'',
        scope='network', network_id=network_id,
    )


def test_patch_network_id_override_retags_owned_network_templates(app_client, seeded_feed):
    """Changing networkIdOverride must move this feed's own network-scope
    templates to the new network, so siblings there inherit them too."""
    slug = seeded_feed['slug']
    db = seeded_feed['db']
    podcast_id = db.get_podcast_by_slug(slug)['id']
    tid = _create_network_template(db, podcast_id, 'old-network')
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'networkIdOverride': 'new-network'}, headers=headers)

    assert resp.status_code == 200
    row = db.get_cue_template(tid)
    assert row['scope'] == 'network'
    assert row['network_id'] == 'new-network'


def test_patch_clearing_network_id_override_demotes_network_templates(app_client, seeded_feed):
    """Clearing the override with no auto-detected network leaves the
    template with nowhere to point, so it demotes to podcast scope."""
    slug = seeded_feed['slug']
    db = seeded_feed['db']
    podcast_id = db.get_podcast_by_slug(slug)['id']
    db.update_podcast(slug, network_id_override='old-network')
    tid = _create_network_template(db, podcast_id, 'old-network')
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'networkIdOverride': None}, headers=headers)

    assert resp.status_code == 200
    row = db.get_cue_template(tid)
    assert row['scope'] == 'podcast'
    assert row['network_id'] is None


def test_patch_network_id_override_rolls_back_if_retag_fails(app_client, seeded_feed, monkeypatch):
    """The podcast update and the template retag share one transaction, so a
    failed retag must not leave the feed's network changed."""
    slug = seeded_feed['slug']
    db = seeded_feed['db']
    db.update_podcast(slug, network_id_override='old-network')

    def _boom(*a, **k):
        raise RuntimeError('boom')
    monkeypatch.setattr(db, 'retag_network_cue_templates', _boom)
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'networkIdOverride': 'new-network'}, headers=headers)

    assert resp.status_code == 500
    assert db.get_podcast_by_slug(slug)['network_id_override'] == 'old-network'


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


# -- descriptionSkipPatterns (issue #835) --

def test_get_feed_defaults_description_skip_patterns(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)

    resp = app_client.get(f'/api/v1/feeds/{slug}')
    assert resp.status_code == 200
    assert resp.get_json()['descriptionSkipPatterns'] == []


def test_patch_sets_description_skip_patterns(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'descriptionSkipPatterns': ['*This is a preview*']},
                            headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()['descriptionSkipPatterns'] == ['*This is a preview*']
    assert seeded_feed['db'].get_podcast_by_slug(slug)['description_skip_patterns'] == \
        '["*This is a preview*"]'


def test_patch_null_resets_description_skip_patterns(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    app_client.patch(f'/api/v1/feeds/{slug}',
                     json={'descriptionSkipPatterns': ['*Ad*']}, headers=headers)
    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={'descriptionSkipPatterns': None}, headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()['descriptionSkipPatterns'] == []
    assert seeded_feed['db'].get_podcast_by_slug(slug)['description_skip_patterns'] is None


@pytest.mark.parametrize('field,column', [
    ('titleSkipPatterns', 'title_skip_patterns'),
    ('descriptionSkipPatterns', 'description_skip_patterns'),
])
@pytest.mark.parametrize('patterns', [
    'not-a-list',
    ['x' * 201],
    [''],
    ['ok'] * 51,
])
def test_patch_invalid_skip_patterns_rejected(app_client, seeded_feed, patterns, field, column):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)

    resp = app_client.patch(f'/api/v1/feeds/{slug}',
                            json={field: patterns}, headers=headers)
    assert resp.status_code == 400
    assert seeded_feed['db'].get_podcast_by_slug(slug)[column] is None


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


# -- detectionNotes (#709) --

def test_patch_detection_notes_roundtrip(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    r = app_client.patch(f'/api/v1/feeds/{slug}', json={'detectionNotes': 'Intro has three parts.'},
                         headers=_csrf_headers(app_client))
    assert r.status_code == 200
    assert r.get_json()['detectionNotes'] == 'Intro has three parts.'
    r = app_client.get(f'/api/v1/feeds/{slug}')
    assert r.get_json()['detectionNotes'] == 'Intro has three parts.'


def test_patch_detection_notes_clear_and_limit(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    app_client.patch(f'/api/v1/feeds/{slug}', json={'detectionNotes': 'x'},
                     headers=_csrf_headers(app_client))
    r = app_client.patch(f'/api/v1/feeds/{slug}', json={'detectionNotes': ''},
                         headers=_csrf_headers(app_client))
    assert r.get_json()['detectionNotes'] is None
    r = app_client.patch(f'/api/v1/feeds/{slug}', json={'detectionNotes': 'y' * 1001},
                         headers=_csrf_headers(app_client))
    assert r.status_code == 400


def test_get_feed_echoes_null_chapters_in_notes(app_client, seeded_feed):
    _authed(app_client)
    assert app_client.get(f"/api/v1/feeds/{seeded_feed['slug']}").get_json()['chaptersInNotes'] is None


@pytest.mark.parametrize('value', ['on', 'off'])
def test_patch_sets_chapters_in_notes_override(app_client, seeded_feed, value):
    slug = seeded_feed['slug']
    _authed(app_client)
    resp = app_client.patch(f'/api/v1/feeds/{slug}', json={'chaptersInNotes': value},
                            headers=_csrf_headers(app_client))
    assert resp.status_code == 200
    assert resp.get_json()['chaptersInNotes'] == value
    assert seeded_feed['db'].get_podcast_by_slug(slug)['chapters_in_notes'] == value


def test_patch_null_clears_chapters_in_notes_override(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)
    app_client.patch(f'/api/v1/feeds/{slug}', json={'chaptersInNotes': 'on'}, headers=headers)
    resp = app_client.patch(f'/api/v1/feeds/{slug}', json={'chaptersInNotes': None}, headers=headers)
    assert resp.get_json()['chaptersInNotes'] is None


def test_patch_rejects_unknown_chapters_in_notes_value(app_client, seeded_feed):
    _authed(app_client)
    resp = app_client.patch(f"/api/v1/feeds/{seeded_feed['slug']}", json={'chaptersInNotes': 'maybe'},
                            headers=_csrf_headers(app_client))
    assert resp.status_code == 400


# transcriptDifferential (2.98.0)

def test_the_api_exposes_transcript_differential_as_a_nullable_bool():
    from api.feeds import _NULLABLE_BOOL_FIELDS
    assert ('transcriptDifferential', 'transcript_differential') in _NULLABLE_BOOL_FIELDS


def test_get_feed_echoes_null_transcript_differential(app_client, seeded_feed):
    _authed(app_client)
    resp = app_client.get(f"/api/v1/feeds/{seeded_feed['slug']}")
    assert resp.status_code == 200
    assert resp.get_json()['transcriptDifferential'] is None


@pytest.mark.parametrize('value', [True, False])
def test_patch_sets_transcript_differential(app_client, seeded_feed, value):
    slug = seeded_feed['slug']
    _authed(app_client)
    resp = app_client.patch(f'/api/v1/feeds/{slug}', json={'transcriptDifferential': value},
                            headers=_csrf_headers(app_client))
    assert resp.status_code == 200
    assert resp.get_json()['transcriptDifferential'] is value
    assert bool(seeded_feed['db'].get_podcast_by_slug(slug)['transcript_differential']) is value


def test_patch_null_resets_transcript_differential(app_client, seeded_feed):
    slug = seeded_feed['slug']
    _authed(app_client)
    headers = _csrf_headers(app_client)
    app_client.patch(f'/api/v1/feeds/{slug}', json={'transcriptDifferential': True}, headers=headers)
    resp = app_client.patch(f'/api/v1/feeds/{slug}', json={'transcriptDifferential': None}, headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()['transcriptDifferential'] is None
    assert seeded_feed['db'].get_podcast_by_slug(slug)['transcript_differential'] is None


@pytest.mark.parametrize('provider', ['typesafe', 'systemone-compatible'])
def test_env_only_native_primary_rejects_feed_chapters_override(app_client, seeded_feed, monkeypatch, provider):
    db = seeded_feed['db']
    keys = ('llm_provider', 'claude_model', 'chapters_model', 'chapters_enabled', 'chapters_mode')
    original = {key: db.get_setting(key) for key in keys}
    try:
        db.clear_setting('llm_provider')
        monkeypatch.setenv('LLM_PROVIDER', provider)
        db.set_setting('claude_model', 'configured-model')
        db.set_setting('chapters_model', 'configured-model')
        db.set_setting('chapters_enabled', 'true')
        db.set_setting('chapters_mode', 'off')
        _authed(app_client)
        response = app_client.patch(f"/api/v1/feeds/{seeded_feed['slug']}",
                                    json={'chaptersMode': 'generate'}, headers=_csrf_headers(app_client))
        assert response.status_code == 400
        assert 'chapters is unsupported' in response.get_json()['error']
        assert db.get_podcast_by_slug(seeded_feed['slug'])['chapters_mode'] is None
    finally:
        for key, value in original.items():
            if value is None:
                db.clear_setting(key)
            else:
                db.set_setting(key, value)


def test_duration_limits_nullable_seconds_and_partial_range_validation(app_client, seeded_feed):
    slug, db = seeded_feed['slug'], seeded_feed['db']
    _authed(app_client)
    headers = _csrf_headers(app_client)
    assert app_client.get(f'/api/v1/feeds/{slug}').get_json()['minDurationSeconds'] is None
    response = app_client.patch(f'/api/v1/feeds/{slug}', json={
        'minDurationSeconds': 60.5, 'maxDurationSeconds': 180}, headers=headers)
    assert response.status_code == 200
    assert response.get_json()['minDurationSeconds'] == 60.5
    invalid = app_client.patch(f'/api/v1/feeds/{slug}', json={
        'minDurationSeconds': 200}, headers=headers)
    assert invalid.status_code == 400
    assert db.get_podcast_by_slug(slug)['min_duration_seconds'] == 60.5
    cleared = app_client.patch(f'/api/v1/feeds/{slug}', json={
        'minDurationSeconds': None, 'maxDurationSeconds': None}, headers=headers)
    assert cleared.status_code == 200
    assert cleared.get_json()['maxDurationSeconds'] is None


@pytest.mark.parametrize('value', [-1, True, '60', float('nan'), float('inf'), 10**400])
def test_invalid_duration_limit_leaves_feed_unchanged(app_client, seeded_feed, value):
    _authed(app_client)
    slug = seeded_feed['slug']
    response = app_client.patch(f'/api/v1/feeds/{slug}',
        json={'minDurationSeconds': value}, headers=_csrf_headers(app_client))
    assert response.status_code == 400
    assert seeded_feed['db'].get_podcast_by_slug(slug)['min_duration_seconds'] is None


def test_feed_episode_search_filters_full_dataset_and_returns_all_selection(app_client, seeded_feed):
    _authed(app_client)
    slug, db = seeded_feed['slug'], seeded_feed['db']
    db.bulk_upsert_discovered_episodes(slug, [{
        'id': f'episode-{i}', 'url': f'https://example.com/{i}.mp3',
        'title': f'Full Show {i}' if i < 30 else 'Clip 100%',
        'rss_duration': 120 if i < 30 else 30,
    } for i in range(31)])
    db.update_podcast(slug, min_duration_seconds=60)
    page = app_client.get(f'/api/v1/feeds/{slug}/episodes?search=FULL%20SHOW&limit=1&offset=29')
    assert page.status_code == 200
    assert page.get_json()['total'] == 30
    assert len(page.get_json()['episodes']) == 1
    selection = app_client.get(f'/api/v1/feeds/{slug}/episodes?search=full%20show&selection=true&limit=1')
    assert selection.status_code == 200
    assert len(selection.get_json()['selection']) == 30
    assert set(selection.get_json()['selection'][0]) == {
        'id', 'status', 'jobState', 'titleSkipped', 'descriptionSkipped', 'durationSkipped'}
    literal = app_client.get(f'/api/v1/feeds/{slug}/episodes?search=%25')
    assert literal.get_json()['total'] == 1
    assert literal.get_json()['episodes'][0]['durationSkipped'] is True
    assert literal.get_json()['episodes'][0]['titleSkipped'] is False


def test_selection_fetch_caps_at_501_and_flags_truncation(app_client, seeded_feed):
    _authed(app_client)
    slug, db = seeded_feed['slug'], seeded_feed['db']
    db.bulk_upsert_discovered_episodes(slug, [{
        'id': f'episode-{i}', 'url': f'https://example.com/{i}.mp3',
        'title': f'Episode {i}',
    } for i in range(600)])
    under_cap = app_client.get(f'/api/v1/feeds/{slug}/episodes?selection=true&limit=1')
    assert under_cap.get_json()['total'] == 600
    assert len(under_cap.get_json()['selection']) == 501
    assert under_cap.get_json()['truncated'] is True

    conn = db.get_connection()
    conn.execute(
        "DELETE FROM episodes WHERE episode_id IN ({})".format(  # noqa: S608
            ','.join('?' * 99)),
        [f'episode-{i}' for i in range(501, 600)])
    conn.commit()
    not_truncated = app_client.get(f'/api/v1/feeds/{slug}/episodes?selection=true&limit=1')
    assert not_truncated.get_json()['total'] == 501
    assert len(not_truncated.get_json()['selection']) == 501
    assert not_truncated.get_json()['truncated'] is False


def test_bulk_reprocess_overrides_duration_filter(app_client, seeded_feed):
    _authed(app_client)
    slug, db = seeded_feed['slug'], seeded_feed['db']
    db.bulk_upsert_discovered_episodes(slug, [{
        'id': 'filtered-episode', 'url': 'https://example.com/episode.mp3',
        'title': 'Clip', 'rss_duration': 30,
    }])
    db.upsert_episode(slug, 'filtered-episode', status='processed')
    db.update_podcast(slug, min_duration_seconds=60)
    response = app_client.post(f'/api/v1/feeds/{slug}/episodes/bulk',
        json={'episodeIds': ['filtered-episode'], 'action': 'reprocess_full'},
        headers=_csrf_headers(app_client))
    assert response.status_code == 200
    assert response.get_json()['queued'] == 1
    assert response.get_json()['skipped'] == 0
    assert db.get_episode(slug, 'filtered-episode')['reprocess_requested_at']


def test_full_selection_job_states_respect_sqlite_variable_limit(temp_db):
    slug = 'selection-limit'
    temp_db.create_podcast(slug, 'https://example.com/feed.xml', 'Example')
    temp_db.upsert_episode_for_processing(slug, 'queued', 'https://example.com/queued.mp3', 'Queued')
    temp_db.upsert_episode_for_processing(slug, 'running', 'https://example.com/running.mp3', 'Running')
    conn = temp_db.get_connection()
    conn.execute("UPDATE auto_process_queue SET status='processing' WHERE episode_id='running'")
    conn.commit()
    prior_limit = conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 10)
    try:
        states = temp_db.get_episode_job_states([
            *[f'id-{i}' for i in range(20)], 'queued', 'running', 'queued'])
    finally:
        conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, prior_limit)
    assert states == {(slug, 'queued'): 'queued', (slug, 'running'): 'processing'}
