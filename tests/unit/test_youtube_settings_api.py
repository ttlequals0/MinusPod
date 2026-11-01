import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='yt-settings-test-'))


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')


def _csrf_headers(client):
    for cookie in client._cookies.values():
        if cookie.key == 'minuspod_csrf':
            return {'X-CSRF-Token': cookie.value}
    return {}


def test_get_defaults(app_client):
    _authed(app_client)
    body = app_client.get('/api/v1/settings/youtube').get_json()
    assert body['enabled'] is False
    assert body['processDelayMinutes'] == 60
    assert body['sbProbeAttempts'] == 6
    assert body['sbProbeIntervalMinutes'] == 30
    assert body['sbWatchIntervalHours'] == 6
    assert body['sbWatchMaxDays'] == 7
    assert body['llmFallback'] is True
    assert body['captionsAsTranscript'] is True
    assert body['transcribeFallback'] is False
    assert body['apiKeyConfigured'] is False
    assert body['ytdlp']['autoUpdate'] is True
    assert body['bounds']['processDelayMinutes'] == [0, 1440]


def test_put_round_trips_and_masks_key(app_client, monkeypatch):
    import secrets_crypto
    monkeypatch.setenv('MINUSPOD_MASTER_PASSPHRASE', 'test-pass-youtube')
    secrets_crypto.reset_cache()
    _authed(app_client)
    resp = app_client.put('/api/v1/settings/youtube', json={
        'enabled': True, 'apiKey': 'AIzaSyTESTKEY', 'processDelayMinutes': 0,
        'sbProbeAttempts': 3, 'llmFallback': False, 'captionsAsTranscript': False,
        'transcribeFallback': True,
    }, headers=_csrf_headers(app_client))
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body['enabled'] is True and body['apiKeyConfigured'] is True
    assert 'apiKey' not in body
    assert body['processDelayMinutes'] == 0 and body['sbProbeAttempts'] == 3
    assert body['llmFallback'] is False and body['captionsAsTranscript'] is False
    assert body['transcribeFallback'] is True
    from api import get_database
    assert get_database().get_secret('youtube_api_key') == 'AIzaSyTESTKEY'
    resp = app_client.put('/api/v1/settings/youtube', json={'apiKey': ''}, headers=_csrf_headers(app_client))
    assert resp.get_json()['apiKeyConfigured'] is False


def test_put_rejects_out_of_range_without_partial_write(app_client):
    _authed(app_client)
    resp = app_client.put('/api/v1/settings/youtube', json={
        'sbProbeIntervalMinutes': 4, 'processDelayMinutes': 10,
    }, headers=_csrf_headers(app_client))
    assert resp.status_code == 400
    assert 'sbProbeIntervalMinutes' in resp.get_json()['error']
    assert app_client.get('/api/v1/settings/youtube').get_json()['processDelayMinutes'] == 60


def test_feed_override_resolution(app_client):
    from api import get_database
    from youtube.settings import resolve_feed, load_global
    db = get_database()
    db.create_podcast('yt-ov', 'youtube://playlist/PL1', feed_type='youtube')
    db.update_podcast('yt-ov', yt_process_delay_minutes_override=5, yt_llm_fallback_override=0,
                      yt_captions_override=0, yt_transcribe_override=1)
    resolved = resolve_feed(db, db.get_podcast_by_slug('yt-ov'))
    assert resolved.process_delay_minutes == 5
    assert resolved.llm_fallback is False
    assert resolved.captions_as_transcript is False
    assert resolved.transcribe_fallback is True
    assert load_global(db).transcribe_fallback is False
    assert resolved.probe_attempts == load_global(db).probe_attempts
    db.delete_podcast('yt-ov')


def test_put_key_without_crypto_is_409_and_writes_nothing(app_client, monkeypatch):
    import secrets_crypto
    monkeypatch.delenv('MINUSPOD_MASTER_PASSPHRASE', raising=False)
    secrets_crypto.reset_cache()
    _authed(app_client)
    resp = app_client.put('/api/v1/settings/youtube', json={
        'apiKey': 'AIzaSyTESTKEY', 'processDelayMinutes': 10,
    }, headers=_csrf_headers(app_client))
    assert resp.status_code == 409
    assert app_client.get('/api/v1/settings/youtube').get_json()['processDelayMinutes'] == 60


def test_ytdlp_extra_args_round_trip_and_trim(app_client):
    _authed(app_client)
    assert app_client.get('/api/v1/settings/youtube').get_json()['ytdlpExtraArgs'] == ''
    resp = app_client.put('/api/v1/settings/youtube', json={'ytdlpExtraArgs': '  --limit-rate 2M '},
                          headers=_csrf_headers(app_client))
    assert resp.status_code == 200
    assert resp.get_json()['ytdlpExtraArgs'] == '--limit-rate 2M'


def test_ytdlp_extra_args_rejects_bad_values(app_client):
    _authed(app_client)
    for value, text in (('--exec x', '--exec is not allowed'), ('--a "b', 'unbalanced quotes'), (5, 'must be a string')):
        resp = app_client.put('/api/v1/settings/youtube', json={'ytdlpExtraArgs': value},
                              headers=_csrf_headers(app_client))
        assert resp.status_code == 400
        assert text in resp.get_json()['error']


def test_proxy_url_round_trip_and_trim(app_client):
    _authed(app_client)
    assert app_client.get('/api/v1/settings/youtube').get_json()['proxyUrl'] == ''
    resp = app_client.put('/api/v1/settings/youtube', json={'proxyUrl': ' socks5h://proxy:1080 '},
                          headers=_csrf_headers(app_client))
    assert resp.status_code == 200
    assert resp.get_json()['proxyUrl'] == 'socks5h://proxy:1080'
    resp = app_client.put('/api/v1/settings/youtube', json={'proxyUrl': ''}, headers=_csrf_headers(app_client))
    assert resp.get_json()['proxyUrl'] == ''


def test_proxy_url_rejects_bad_values(app_client):
    _authed(app_client)
    for value, text in (('ftp://u:SECRET@h', 'must start with'), ('http://', 'name a host'), (5, 'must be a string')):
        resp = app_client.put('/api/v1/settings/youtube', json={'proxyUrl': value},
                              headers=_csrf_headers(app_client))
        assert resp.status_code == 400
        assert text in resp.get_json()['error']
        assert 'SECRET' not in resp.get_json()['error']


def test_proxy_url_password_is_masked_and_masked_put_is_a_noop(app_client):
    from api import get_database
    _authed(app_client)
    headers = _csrf_headers(app_client)
    put = app_client.put('/api/v1/settings/youtube', json={'proxyUrl': 'socks5h://u:secret@host:1080'},
                         headers=headers)
    assert put.get_json()['proxyUrl'] == 'socks5h://u:***@host:1080'
    assert 'secret' not in app_client.get('/api/v1/settings/youtube').get_data(as_text=True)
    resp = app_client.put('/api/v1/settings/youtube', json={'proxyUrl': 'socks5h://u:***@host:1080'},
                          headers=headers)
    assert resp.status_code == 200
    assert get_database().get_setting('youtube_proxy_url') == 'socks5h://u:secret@host:1080'
    app_client.put('/api/v1/settings/youtube', json={'proxyUrl': 'http://other:1'}, headers=headers)
    assert get_database().get_setting('youtube_proxy_url') == 'http://other:1'


def test_mask_proxy_url_leaves_passwordless_urls():
    from youtube import ytdlp
    assert ytdlp.mask_proxy_url('http://u@h:1') == 'http://u@h:1'
    assert ytdlp.mask_proxy_url('socks5://h:1') == 'socks5://h:1'


def test_masked_password_survives_editing_another_part_of_the_url(app_client):
    from api import get_database
    _authed(app_client)
    headers = _csrf_headers(app_client)
    app_client.put('/api/v1/settings/youtube', json={'proxyUrl': 'http://u:real@host:1080'}, headers=headers)
    app_client.put('/api/v1/settings/youtube', json={'proxyUrl': 'http://u:***@host:2080'}, headers=headers)
    assert get_database().get_setting('youtube_proxy_url') == 'http://u:real@host:2080'
    app_client.put('/api/v1/settings/youtube', json={'proxyUrl': 'http://u:fresh@host:2080'}, headers=headers)
    assert get_database().get_setting('youtube_proxy_url') == 'http://u:fresh@host:2080'


def test_changed_proxy_host_with_masked_password_is_rejected(app_client):
    from api import get_database
    _authed(app_client)
    headers = _csrf_headers(app_client)
    app_client.put('/api/v1/settings/youtube', json={'proxyUrl': 'http://u:real@host:1080'}, headers=headers)
    resp = app_client.put('/api/v1/settings/youtube', json={'proxyUrl': 'http://u:***@other:1080'}, headers=headers)
    assert resp.status_code == 400 and 'enter the proxy password again' in resp.get_json()['error']
    assert get_database().get_setting('youtube_proxy_url') == 'http://u:real@host:1080'
    resp = app_client.put('/api/v1/settings/youtube', json={'proxyUrl': 'http://u:new@other:1080'}, headers=headers)
    assert resp.status_code == 200
    assert get_database().get_setting('youtube_proxy_url') == 'http://u:new@other:1080'


def test_reset_restores_defaults_and_keeps_switch_key_cookies(app_client, monkeypatch):
    import secrets_crypto
    monkeypatch.setenv('MINUSPOD_MASTER_PASSPHRASE', 'test-pass-youtube')
    secrets_crypto.reset_cache()
    _authed(app_client)
    headers = _csrf_headers(app_client)
    assert app_client.put('/api/v1/settings/youtube', json={
        'enabled': True, 'apiKey': 'AIzaSyTESTKEY', 'processDelayMinutes': 5, 'sbProbeAttempts': 2,
        'sbWatchMaxDays': 30, 'llmFallback': False, 'captionsAsTranscript': False, 'hidePrivate': False,
        'transcribeFallback': True, 'ytdlpAutoUpdate': False, 'ytdlpExtraArgs': '--limit-rate 2M',
        'proxyUrl': 'socks5h://user:secret@proxy:1080',
    }, headers=headers).status_code == 200
    before = app_client.get('/api/v1/settings/youtube').get_json()
    assert before['proxyUrl'] and before['ytdlpExtraArgs']

    resp = app_client.post('/api/v1/settings/youtube/reset', headers=headers)
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body['processDelayMinutes'] == 60
    assert body['sbProbeAttempts'] == 6
    assert body['sbWatchMaxDays'] == 7
    assert body['llmFallback'] is True
    assert body['captionsAsTranscript'] is True
    assert body['hidePrivate'] is True
    assert body['transcribeFallback'] is False
    assert body['ytdlp']['autoUpdate'] is True
    assert body['ytdlpExtraArgs'] == ''
    assert body['proxyUrl'] == ''
    assert body['enabled'] is True
    assert body['apiKeyConfigured'] is True
    assert body['cookies'] == before['cookies']
    assert set(body) == set(app_client.get('/api/v1/settings/youtube').get_json())


def test_sb_submit_defaults_to_off_and_toggles(app_client):
    from api import get_database
    from youtube.settings import load_global
    db = get_database()
    _authed(app_client)
    body = app_client.get('/api/v1/settings/youtube').get_json()
    assert body['sbSubmitEnabled'] is False and body['sbUserIdConfigured'] is False
    try:
        resp = app_client.put('/api/v1/settings/youtube', json={'sbSubmitEnabled': True},
                              headers=_csrf_headers(app_client))
        assert resp.get_json()['sbSubmitEnabled'] is True and load_global(db).sb_submit_enabled is True
        assert app_client.put('/api/v1/settings/youtube', json={'sbSubmitEnabled': 'yes'},
                              headers=_csrf_headers(app_client)).status_code == 400
    finally:
        db.set_setting('youtube_sb_submit_enabled', 'false', is_default=False)


def test_put_sb_flag_and_user_id_round_trip(app_client, monkeypatch):
    import secrets_crypto
    monkeypatch.setenv('MINUSPOD_MASTER_PASSPHRASE', 'test-pass-youtube')
    secrets_crypto.reset_cache()
    _authed(app_client)
    headers = _csrf_headers(app_client)
    user_id = 'a' * 40
    resp = app_client.put('/api/v1/settings/youtube', json={'sbSubmitEnabled': True, 'sbUserId': user_id},
                          headers=headers)
    assert resp.status_code == 200, resp.get_json()
    body = resp.get_json()
    assert body['sbSubmitEnabled'] is True and body['sbUserIdConfigured'] is True
    assert user_id not in str(body)
    from api import get_database
    assert get_database().get_secret('youtube_sb_user_id') == user_id
    reset = app_client.post('/api/v1/settings/youtube/reset', headers=headers).get_json()
    assert reset['sbSubmitEnabled'] is False and reset['sbUserIdConfigured'] is True
    cleared = app_client.put('/api/v1/settings/youtube', json={'sbUserId': None}, headers=headers).get_json()
    assert cleared['sbUserIdConfigured'] is False


def test_put_sb_validation(app_client, monkeypatch):
    import secrets_crypto
    monkeypatch.setenv('MINUSPOD_MASTER_PASSPHRASE', 'test-pass-youtube')
    secrets_crypto.reset_cache()
    _authed(app_client)
    headers = _csrf_headers(app_client)
    for payload in ({'sbUserId': 'short'}, {'sbUserId': 'a' * 201},
                    {'sbUserId': 'a' * 31 + ' '}, {'sbUserId': 'a' * 31 + '\u00e9'}, {'sbUserId': 5}):
        resp = app_client.put('/api/v1/settings/youtube', json=payload, headers=headers)
        assert resp.status_code == 400, payload
    assert app_client.get('/api/v1/settings/youtube').get_json()['sbUserIdConfigured'] is False


GENERATE_URL = '/api/v1/settings/youtube/sb-user-id/generate'
REVEAL_URL = '/api/v1/settings/youtube/sb-user-id'


def _clear_sb(db):
    db.clear_secret('youtube_sb_user_id')
    db.clear_secret('youtube_sb_user_id_generated')


def _enable_crypto(monkeypatch):
    import secrets_crypto
    monkeypatch.setenv('MINUSPOD_MASTER_PASSPHRASE', 'test-pass-youtube')
    secrets_crypto.reset_cache()


def test_generate_sb_user_id_stores_and_returns_once(app_client, monkeypatch):
    from api import get_database
    from tests.integration.openapi_conformance import assert_response_conforms
    _enable_crypto(monkeypatch)
    _authed(app_client)
    _clear_sb(get_database())
    resp = app_client.post(GENERATE_URL, headers=_csrf_headers(app_client))
    assert_response_conforms('post', '/settings/youtube/sb-user-id/generate', resp)
    body = resp.get_json()
    assert len(body['sbUserId']) == 48 and 'sbUserIdConfigured' not in body
    assert get_database().get_secret('youtube_sb_user_id_generated') == body['sbUserId']
    assert not get_database().get_secret('youtube_sb_user_id')
    settings = app_client.get('/api/v1/settings/youtube').get_json()
    assert settings['sbUserIdConfigured'] is True
    assert body['sbUserId'] not in str(settings) and 'sbUserId' not in settings


def test_generate_sb_user_id_conflicts_when_stored_and_ignores_replace(app_client, monkeypatch):
    from api import get_database
    _enable_crypto(monkeypatch)
    _authed(app_client)
    _clear_sb(get_database())
    first = app_client.post(GENERATE_URL, headers=_csrf_headers(app_client)).get_json()['sbUserId']
    for body in ({}, {'replace': True}):
        resp = app_client.post(GENERATE_URL, json=body, headers=_csrf_headers(app_client))
        assert resp.status_code == 409 and resp.get_json()['error'] == 'configured'
    assert get_database().get_secret('youtube_sb_user_id_generated') == first


def test_get_sb_user_id_reveals_stored_or_404(app_client, monkeypatch):
    from api import get_database
    from tests.integration.openapi_conformance import assert_response_conforms
    _enable_crypto(monkeypatch)
    _authed(app_client)
    _clear_sb(get_database())
    resp = app_client.get(REVEAL_URL)
    assert resp.status_code == 404 and resp.get_json()['error'] == 'not_configured'
    generated = app_client.post(GENERATE_URL, headers=_csrf_headers(app_client)).get_json()['sbUserId']
    resp = app_client.get(REVEAL_URL)
    assert_response_conforms('get', '/settings/youtube/sb-user-id', resp)
    assert resp.get_json() == {'sbUserId': generated}


def test_generate_sb_user_id_without_crypto_is_409(app_client, monkeypatch):
    import secrets_crypto
    from api import get_database
    monkeypatch.delenv('MINUSPOD_MASTER_PASSPHRASE', raising=False)
    secrets_crypto.reset_cache()
    _authed(app_client)
    resp = app_client.post(GENERATE_URL, headers=_csrf_headers(app_client))
    assert resp.status_code == 409 and resp.get_json()['error'] == 'provider_crypto_unavailable'
    assert get_database().get_secret('youtube_sb_user_id') in (None, '')



def test_generated_flag_blocks_put_and_pasted_id_can_be_cleared(app_client, monkeypatch):
    from api import get_database
    _enable_crypto(monkeypatch)
    _authed(app_client)
    db = get_database()
    _clear_sb(db)
    pasted = 'p' * 40
    resp = app_client.put('/api/v1/settings/youtube', json={'sbUserId': pasted}, headers=_csrf_headers(app_client))
    assert resp.status_code == 200
    assert resp.get_json()['sbUserIdConfigured'] is True and resp.get_json()['sbUserIdGenerated'] is False
    resp = app_client.put('/api/v1/settings/youtube', json={'sbUserId': None}, headers=_csrf_headers(app_client))
    assert resp.get_json()['sbUserIdConfigured'] is False and resp.get_json()['sbUserIdGenerated'] is False
    generated = app_client.post(GENERATE_URL, headers=_csrf_headers(app_client)).get_json()['sbUserId']
    assert app_client.get('/api/v1/settings/youtube').get_json()['sbUserIdGenerated'] is True
    for body in ({'sbUserId': None}, {'sbUserId': ''}, {'sbUserId': pasted}, {'sbUserId': None, 'processDelayMinutes': 5}):
        resp = app_client.put('/api/v1/settings/youtube', json=body, headers=_csrf_headers(app_client))
        assert resp.status_code == 409 and resp.get_json()['error'] == 'generated'
    assert db.get_secret('youtube_sb_user_id_generated') == generated
    assert not db.get_secret('youtube_sb_user_id')
    settings = app_client.get('/api/v1/settings/youtube').get_json()
    assert settings['sbUserIdGenerated'] is True and settings['processDelayMinutes'] == 60
    resp = app_client.put('/api/v1/settings/youtube', json={'processDelayMinutes': 5}, headers=_csrf_headers(app_client))
    assert resp.status_code == 200 and resp.get_json()['sbUserIdGenerated'] is True


def test_generate_conflicts_when_pasted_id_stored_and_reset_keeps_generated(app_client, monkeypatch):
    from api import get_database
    _enable_crypto(monkeypatch)
    _authed(app_client)
    db = get_database()
    _clear_sb(db)
    pasted = 'p' * 40
    app_client.put('/api/v1/settings/youtube', json={'sbUserId': pasted}, headers=_csrf_headers(app_client))
    assert db.get_secret('youtube_sb_user_id') == pasted and not db.get_secret('youtube_sb_user_id_generated')
    resp = app_client.post(GENERATE_URL, headers=_csrf_headers(app_client))
    assert resp.status_code == 409 and resp.get_json()['error'] == 'configured'
    assert app_client.get(REVEAL_URL).get_json() == {'sbUserId': pasted}
    _clear_sb(db)
    generated = app_client.post(GENERATE_URL, headers=_csrf_headers(app_client)).get_json()['sbUserId']
    reset = app_client.post('/api/v1/settings/youtube/reset', headers=_csrf_headers(app_client)).get_json()
    assert reset['sbUserIdGenerated'] is True and db.get_secret('youtube_sb_user_id_generated') == generated


def test_put_loses_to_a_generate_that_lands_between_its_check_and_write(app_client, monkeypatch):
    from api import get_database
    from api.settings import youtube as yt_api
    _enable_crypto(monkeypatch)
    _authed(app_client)
    db = get_database()
    _clear_sb(db)
    real = yt_api.set_or_clear_secret

    def racing(db_, key, value):
        if key == 'youtube_sb_user_id':
            real(db_, 'youtube_sb_user_id_generated', 'g' * 48)
        return real(db_, key, value)
    monkeypatch.setattr(yt_api, 'set_or_clear_secret', racing)
    resp = app_client.put('/api/v1/settings/youtube', json={'sbUserId': 'p' * 40}, headers=_csrf_headers(app_client))
    assert resp.status_code == 409 and resp.get_json()['error'] == 'generated'
    assert not db.get_secret('youtube_sb_user_id')
    assert db.get_secret('youtube_sb_user_id_generated') == 'g' * 48


def test_generate_clears_a_pasted_id_written_concurrently(app_client, monkeypatch):
    from api import get_database
    from api.settings import youtube as yt_api
    _enable_crypto(monkeypatch)
    _authed(app_client)
    db = get_database()
    _clear_sb(db)
    real = yt_api.set_or_clear_secret

    def racing(db_, key, value):
        real(db_, key, value)
        if key == 'youtube_sb_user_id_generated':
            real(db_, 'youtube_sb_user_id', 'p' * 40)
    monkeypatch.setattr(yt_api, 'set_or_clear_secret', racing)
    resp = app_client.post(GENERATE_URL, headers=_csrf_headers(app_client))
    assert resp.status_code == 200
    assert not db.get_secret('youtube_sb_user_id')
    assert db.get_secret('youtube_sb_user_id_generated') == resp.get_json()['sbUserId']
