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
