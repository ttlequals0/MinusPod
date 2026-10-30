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
