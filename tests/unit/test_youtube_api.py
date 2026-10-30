import io
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='yt-api-test-'))

from youtube.discovery import SourceInfo, SourceNotFound


def _authed(client):
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    client.get('/api/v1/auth/status')


def _csrf_headers(client):
    for cookie in client._cookies.values():
        if cookie.key == 'minuspod_csrf':
            return {'X-CSRF-Token': cookie.value}
    return {}


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    try:
        from api import limiter
        limiter.reset()
    except Exception:
        pass
    yield


def _enable(db):
    db.set_setting('youtube_enabled', 'true', is_default=False)


def test_preview_disabled_short_circuits(app_client):
    _authed(app_client)
    from api import get_database
    get_database().set_setting('youtube_enabled', 'false', is_default=False)
    resp = app_client.post('/api/v1/youtube/preview', json={'url': 'https://www.youtube.com/@x'},
                           headers=_csrf_headers(app_client))
    assert resp.status_code == 200
    assert resp.get_json() == {'enabled': False, 'source': None, 'rssMatches': []}


def test_preview_returns_source_and_rss_matches(app_client):
    _authed(app_client)
    from api import get_database
    _enable(get_database())
    info = SourceInfo('channel', 'UCabcdefghijklmnopqrstuv', 'The Daily Tech Show', 'desc', 'https://i.ytimg.com/a.jpg', 412)
    matches = [{'title': 'The Daily Tech Show', 'feedUrl': 'https://feeds.example.com/dts.xml', 'artworkUrl': ''},
               {'title': 'Daily Tech', 'feedUrl': 'https://feeds.example.com/dt.xml', 'artworkUrl': ''},
               {'title': 'Unrelated Cooking', 'feedUrl': 'https://feeds.example.com/cook.xml', 'artworkUrl': ''},
               {'title': 'Tech Show Daily', 'feedUrl': 'https://feeds.example.com/tsd.xml', 'artworkUrl': ''}]
    with patch('api.youtube.discovery.resolve_source', return_value=info), \
            patch('api.youtube.search_directory', return_value=matches):
        resp = app_client.post('/api/v1/youtube/preview', json={'url': 'https://www.youtube.com/@dailytech'},
                               headers=_csrf_headers(app_client))
    body = resp.get_json()
    assert resp.status_code == 200, body
    assert body['source']['id'] == 'UCabcdefghijklmnopqrstuv' and body['source']['videoCount'] == 412
    assert body['source']['alreadyAdded'] is None
    titles = [m['title'] for m in body['rssMatches']]
    assert 'Unrelated Cooking' not in titles and len(titles) <= 3


def test_preview_not_found_is_404(app_client):
    _authed(app_client)
    from api import get_database
    _enable(get_database())
    with patch('api.youtube.discovery.resolve_source', side_effect=SourceNotFound('Playlist not found or private')):
        resp = app_client.post('/api/v1/youtube/preview', json={'url': 'https://www.youtube.com/playlist?list=PLbh0Jamvptwfp_qc439PLuyKJ-tWUt222'},
                               headers=_csrf_headers(app_client))
    assert resp.status_code == 404
    assert 'not found' in resp.get_json()['error']


def test_preview_rejects_non_youtube_url(app_client):
    _authed(app_client)
    from api import get_database
    _enable(get_database())
    resp = app_client.post('/api/v1/youtube/preview', json={'url': 'https://example.com/feed.xml'},
                           headers=_csrf_headers(app_client))
    assert resp.status_code == 400


def test_add_youtube_feed_and_patch_overrides(app_client):
    _authed(app_client)
    from api import get_database
    db = get_database()
    _enable(db)
    headers = _csrf_headers(app_client)
    with patch('main_app.feeds.refresh_single_feed', return_value=True), \
            patch('api.feeds.get_storage') as storage:
        storage.return_value.download_artwork.return_value = True
        resp = app_client.post('/api/v1/feeds', json={
            'feedType': 'youtube', 'sourceKind': 'playlist', 'sourceId': 'PLbh0Jamvptwfp_qc439PLuyKJ-tWUt222',
            'title': 'Example Playlist', 'description': 'd', 'artworkUrl': 'https://i.ytimg.com/a.jpg',
            'includeShorts': False,
        }, headers=headers)
    assert resp.status_code == 201, resp.get_json()
    slug = resp.get_json()['slug']
    try:
        row = db.get_podcast_by_slug(slug)
        assert row['feed_type'] == 'youtube'
        assert row['source_url'] == 'youtube://playlist/PLbh0Jamvptwfp_qc439PLuyKJ-tWUt222'
        assert row['youtube_include_shorts'] == 0

        dup = app_client.post('/api/v1/feeds', json={
            'feedType': 'youtube', 'sourceKind': 'playlist', 'sourceId': 'PLbh0Jamvptwfp_qc439PLuyKJ-tWUt222',
            'title': 'Again'}, headers=headers)
        assert dup.status_code == 409

        feed = app_client.get(f'/api/v1/feeds/{slug}').get_json()
        assert feed['feedType'] == 'youtube' and feed['youtubeSourceKind'] == 'playlist'
        assert feed['ytProcessDelayMinutesOverride'] is None
        assert feed['youtubeSourceUrl'].startswith('https://www.youtube.com/playlist?list=')

        patched = app_client.patch(f'/api/v1/feeds/{slug}', json={
            'ytProcessDelayMinutesOverride': 5, 'ytLlmOnTopOverride': True, 'ytCaptionsOverride': False,
            'youtubeIncludeShorts': True}, headers=headers)
        assert patched.status_code == 200, patched.get_json()
        body = patched.get_json()
        assert body['ytProcessDelayMinutesOverride'] == 5 and body['ytLlmOnTopOverride'] is True
        assert body['ytCaptionsOverride'] is False and body['youtubeIncludeShorts'] is True

        bad = app_client.patch(f'/api/v1/feeds/{slug}', json={'ytProbeIntervalMinutesOverride': 2}, headers=headers)
        assert bad.status_code == 400
    finally:
        db.delete_podcast(slug)


def test_patch_override_rejected_on_subscribed_feed(app_client):
    _authed(app_client)
    from api import get_database
    db = get_database()
    db.create_podcast('yt-api-sub', 'https://example.com/feed.xml', 'Sub')
    try:
        resp = app_client.patch('/api/v1/feeds/yt-api-sub', json={'ytProcessDelayMinutesOverride': 5},
                                headers=_csrf_headers(app_client))
        assert resp.status_code == 400
    finally:
        db.delete_podcast('yt-api-sub')


def test_cookies_roundtrip(app_client):
    _authed(app_client)
    headers = _csrf_headers(app_client)
    body = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1800000000\tPREF\tf6=1\n"
    resp = app_client.post('/api/v1/youtube/cookies', json={'cookies': body}, headers=headers)
    assert resp.status_code == 200 and resp.get_json()['lines'] == 1
    assert app_client.get('/api/v1/settings/youtube').get_json()['cookies']['set'] is True
    bad = app_client.post('/api/v1/youtube/cookies', json={'cookies': 'nope'}, headers=headers)
    assert bad.status_code == 400
    gone = app_client.delete('/api/v1/youtube/cookies', headers=headers)
    assert gone.get_json()['set'] is False


def test_ytdlp_update_endpoint(app_client):
    _authed(app_client)
    with patch('api.youtube.ytdlp.update_binary', return_value={'updated': True, 'version': '2026.10.27', 'error': None}):
        resp = app_client.post('/api/v1/youtube/ytdlp/update', headers=_csrf_headers(app_client))
    assert resp.status_code == 200 and resp.get_json()['version'] == '2026.10.27'


def test_test_key_sends_header_not_query(app_client):
    _authed(app_client)
    resp_obj = MagicMock(status_code=200, text='')
    body = b'{"items": [{"snippet": {"title": "Example Channel"}}]}'
    with patch('youtube.discovery.safe_get', return_value=resp_obj) as get, \
            patch('youtube.discovery.read_response_capped', return_value=body):
        resp = app_client.post('/api/v1/youtube/test-key', json={'apiKey': 'AIzaSyTESTKEY'},
                               headers=_csrf_headers(app_client))
    assert resp.status_code == 200 and resp.get_json() == {'ok': True, 'channelTitle': 'Example Channel'}
    assert 'AIzaSyTESTKEY' not in get.call_args.args[0]
    assert get.call_args.kwargs['headers']['X-Goog-Api-Key'] == 'AIzaSyTESTKEY'


def test_test_key_rejected_key_is_400_without_key_in_message(app_client):
    _authed(app_client)
    resp_obj = MagicMock(status_code=400, text='API key not valid')
    with patch('youtube.discovery.safe_get', return_value=resp_obj):
        resp = app_client.post('/api/v1/youtube/test-key', json={'apiKey': 'AIzaSyBADKEY'},
                               headers=_csrf_headers(app_client))
    assert resp.status_code == 400
    assert 'AIzaSyBADKEY' not in resp.get_json()['error']


def test_test_key_without_any_key_is_400(app_client):
    _authed(app_client)
    resp = app_client.post('/api/v1/youtube/test-key', json={}, headers=_csrf_headers(app_client))
    assert resp.status_code == 400


_PNG_BYTES = bytes.fromhex(
    '89504e470d0a1a0a0000000d49484452000000010000000108020000009077'
    '3df40000000c4944415478da6360606060000000050001a5f6454000000000'
    '49454e44ae426082'
)


def test_artwork_upload_allowed_on_youtube_feed(app_client):
    _authed(app_client)
    from api import get_database
    db = get_database()
    db.create_podcast('yt-art-upload', 'youtube://playlist/PL1', title='YT', feed_type='youtube')
    db.update_podcast('yt-art-upload', youtube_source_kind='playlist', youtube_source_id='PL1')
    try:
        resp = app_client.post(
            '/api/v1/feeds/yt-art-upload/artwork',
            data={'file': (io.BytesIO(_PNG_BYTES), 'cover.png')},
            headers=_csrf_headers(app_client), content_type='multipart/form-data')
        assert resp.status_code == 200, resp.get_json()
    finally:
        db.delete_podcast('yt-art-upload')
