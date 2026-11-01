import json
import subprocess
import threading

import requests
from unittest.mock import MagicMock, patch

import pytest

from youtube import discovery
from youtube.urls import Source


@pytest.fixture(autouse=True)
def _no_auth_args(monkeypatch):
    monkeypatch.setattr(discovery.download, 'auth_args', lambda: [])


def _completed(payload, code=0, stderr=''):
    return subprocess.CompletedProcess(['yt-dlp'], code, stdout=json.dumps(payload), stderr=stderr)


def _podcast(kind='playlist', source_id='PL1', shorts=None):
    return {'slug': 'yt', 'feed_type': 'youtube', 'youtube_source_kind': kind,
            'youtube_source_id': source_id, 'youtube_include_shorts': shorts}


def _db(key=''):
    db = MagicMock()
    db.get_secret.return_value = key
    db.get_setting.return_value = ''
    return db


YT_DLP_LISTING = {
    'title': 'Example Channel', 'description': 'd', 'channel_id': 'UCabcdefghijklmnopqrstuv',
    'playlist_count': 3, 'thumbnails': [{'url': 'https://i.ytimg.com/x.jpg'}],
    'entries': [
        {'id': 'vid000000001', 'title': 'Long one', 'duration': 3600, 'live_status': 'was_live',
         'upload_date': '20261020', 'description': 'ep', 'thumbnails': [{'url': 'https://i.ytimg.com/1.jpg'}]},
        {'id': 'vid000000002', 'title': 'Short', 'duration': 45, 'live_status': None, 'upload_date': '20261021'},
        {'id': 'vid000000003', 'title': 'Live now', 'duration': None, 'live_status': 'is_live', 'upload_date': '20261022'},
    ],
}


def test_list_videos_without_key_uses_ytdlp_and_filters(monkeypatch):
    with patch.object(discovery.ytdlp, 'run', return_value=_completed(YT_DLP_LISTING)) as run:
        episodes = discovery.list_videos(_db(), _podcast(), limit=50)
    assert [e['video_id'] for e in episodes] == ['vid000000001']
    ep = episodes[0]
    assert ep['url'] == 'https://www.youtube.com/watch?v=vid000000001'
    assert ep['rss_duration'] == 3600.0
    assert ep['published'].startswith('2026-10-20')
    assert len(ep['id']) == 12
    assert '--playlist-end' not in run.call_args.args[0]


def test_ytdlp_listing_skips_entries_without_a_title():
    listing = {**YT_DLP_LISTING, 'entries': [
        {'id': 'abcdefghijk'},
        *YT_DLP_LISTING['entries'][:1],
    ]}
    with patch.object(discovery.ytdlp, 'run', return_value=_completed(listing)):
        episodes = discovery.list_videos(_db(), _podcast(), limit=50)
    assert [e['video_id'] for e in episodes] == ['vid000000001']


def test_untitled_entry_does_not_use_a_limit_slot():
    listing = _oldest_first_listing()
    listing['entries'] = [{'id': 'abcdefghijk', 'upload_date': '20261009'},
                          {'id': 'bcdefghijkl', 'title': '[Private video]', 'upload_date': '20261008'},
                          *listing['entries'][:2]]
    with patch.object(discovery.ytdlp, 'run', return_value=_completed(listing)):
        episodes = discovery.list_videos(_db(), _podcast(), limit=2)
    assert {e['video_id'] for e in episodes} == {'vid000000001', 'vid000000002'}


def test_hide_private_off_keeps_unavailable_entries():
    listing = {**YT_DLP_LISTING, 'entries': [
        {'id': 'abcdefghijk', 'upload_date': '20261009', 'duration': 600},
        {'id': 'bcdefghijkl', 'title': '[Private video]', 'upload_date': '20261008', 'duration': 600},
        *YT_DLP_LISTING['entries'][:1],
    ]}
    podcast = {**_podcast(), 'yt_hide_private_override': 0}
    with patch.object(discovery.ytdlp, 'run', return_value=_completed(listing)):
        episodes = discovery.list_videos(_db(), podcast, limit=50)
    assert {e['video_id']: e['title'] for e in episodes} == {
        'abcdefghijk': 'Private episode', 'bcdefghijkl': 'Private episode', 'vid000000001': 'Long one'}


def test_hide_private_off_maps_deleted_placeholder_title():
    listing = {**YT_DLP_LISTING, 'entries': [{'id': 'abcdefghijk', 'title': '[Deleted video]', 'duration': 600}]}
    podcast = {**_podcast(), 'yt_hide_private_override': 0}
    with patch.object(discovery.ytdlp, 'run', return_value=_completed(listing)):
        episodes = discovery.list_videos(_db(), podcast, limit=50)
    assert [e['title'] for e in episodes] == ['Deleted episode']


def test_channel_listing_requests_slack_and_caps_after_the_filter():
    listing = _oldest_first_listing()
    listing['entries'] = [{'id': 'abcdefghijk'}, *listing['entries'][:2]]
    with patch.object(discovery.ytdlp, 'run', return_value=_completed(listing)) as run:
        episodes = discovery.list_videos(_db(), _podcast('channel', 'UCabcdefghijklmnopqrstuv'), limit=2)
    argv = run.call_args.args[0]
    assert argv[argv.index('--playlist-end') + 1] == '22'
    assert [e['video_id'] for e in episodes] == ['vid000000001', 'vid000000002']


def test_listing_passes_download_auth_args():
    with patch.object(discovery.download, 'auth_args', return_value=['--cookies', '/x']), \
            patch.object(discovery.ytdlp, 'run', return_value=_completed(YT_DLP_LISTING)) as run:
        discovery.list_videos(_db(), _podcast(), limit=50)
    argv = run.call_args.args[0]
    assert argv[argv.index('--cookies') + 1] == '/x'


def test_list_videos_keeps_shorts_when_enabled():
    with patch.object(discovery.ytdlp, 'run', return_value=_completed(YT_DLP_LISTING)):
        episodes = discovery.list_videos(_db(), _podcast(shorts=1), limit=50)
    assert [e['video_id'] for e in episodes] == ['vid000000002', 'vid000000001']


def test_channel_feed_lists_long_form_playlist():
    with patch.object(discovery.ytdlp, 'run', return_value=_completed(YT_DLP_LISTING)) as run:
        discovery.list_videos(_db(), _podcast('channel', 'UCabcdefghijklmnopqrstuv'), limit=50)
    assert 'list=UULFabcdefghijklmnopqrstuv' in run.call_args.args[0][-1]
    assert '--playlist-end' in run.call_args.args[0]


def test_ytdlp_failure_is_a_discovery_error():
    err = _completed({}, code=1, stderr='ERROR: [youtube:tab] PL1: The playlist does not exist.')
    with patch.object(discovery.ytdlp, 'run', return_value=err):
        with pytest.raises(discovery.SourceNotFound) as exc:
            discovery.list_videos(_db(), _podcast(), limit=50)
    assert 'does not exist' in str(exc.value)


def _api_pages():
    items = {'items': [{'contentDetails': {'videoId': 'vid000000001'}}, {'contentDetails': {'videoId': 'vid000000002'}}]}
    videos = {'items': [
        {'id': 'vid000000001', 'snippet': {'title': 'Long', 'description': 'd', 'publishedAt': '2026-10-20T10:00:00Z',
                                           'thumbnails': {'high': {'url': 'https://i.ytimg.com/1.jpg'}}},
         'contentDetails': {'duration': 'PT1H0M0S'}},
        {'id': 'vid000000002', 'snippet': {'title': 'Upcoming', 'description': '', 'publishedAt': '2026-10-21T10:00:00Z',
                                           'thumbnails': {}},
         'contentDetails': {'duration': 'PT0S'}, 'liveStreamingDetails': {'scheduledStartTime': '2026-11-01T00:00:00Z'}},
    ]}
    return [json.dumps(items).encode(), json.dumps(videos).encode()]


def _api_listing_with_private(private_first=False):
    items = [{'contentDetails': {'videoId': 'vid000000001', 'videoPublishedAt': '2026-10-20T10:00:00Z'}},
             {'contentDetails': {'videoId': 'vid000000009', 'videoPublishedAt': '2026-10-19T10:00:00Z'}}]
    items = {'items': items[::-1] if private_first else items}
    videos = {'items': _api_pages_videos()[:1]}
    return [json.dumps(items).encode(), json.dumps(videos).encode()]


def _api_pages_videos():
    return json.loads(_api_pages()[1])['items']


def test_api_listing_hides_private_ids_by_default():
    with patch.object(discovery, 'safe_get', return_value=_resp()), \
            patch.object(discovery, 'read_response_capped', side_effect=_api_listing_with_private()):
        episodes = discovery.list_videos(_db('KEY'), _podcast(), limit=50)
    assert [e['video_id'] for e in episodes] == ['vid000000001']


def test_api_listing_adds_placeholder_when_hide_private_off():
    podcast = {**_podcast(), 'yt_hide_private_override': 0}
    with patch.object(discovery, 'safe_get', return_value=_resp()), \
            patch.object(discovery, 'read_response_capped', side_effect=_api_listing_with_private()):
        episodes = discovery.list_videos(_db('KEY'), podcast, limit=50)
    assert [(e['video_id'], e['title']) for e in episodes] == [
        ('vid000000001', 'Long'), ('vid000000009', 'Private episode')]
    assert episodes[1]['published'] == '2026-10-19T10:00:00Z' and episodes[1]['rss_duration'] is None


def test_api_listing_keeps_playlist_order_and_placeholder_within_limit():
    podcast = {**_podcast('channel', 'UCabcdefghijklmnopqrstuv'), 'yt_hide_private_override': 0}
    with patch.object(discovery, 'safe_get', return_value=_resp()), \
            patch.object(discovery, 'read_response_capped', side_effect=_api_listing_with_private(private_first=True)):
        episodes = discovery.list_videos(_db('KEY'), podcast, limit=1)
    assert [e['video_id'] for e in episodes] == ['vid000000009']


def test_api_listing_fetches_the_slack_page():
    page1 = {'items': [{'contentDetails': {'videoId': f'vid{i:09d}'}} for i in range(50)], 'nextPageToken': 'T'}
    page2 = {'items': [{'contentDetails': {'videoId': 'vid000000099'}}]}
    empty = json.dumps({'items': []}).encode()
    with patch.object(discovery, 'safe_get', return_value=_resp()) as get, \
            patch.object(discovery, 'read_response_capped',
                         side_effect=[json.dumps(page1).encode(), json.dumps(page2).encode(), empty, empty]):
        discovery.list_videos(_db('KEY'), _podcast(), limit=50)
    assert sum('playlistItems' in c.args[0] for c in get.call_args_list) == 2


def _resp(status=200, text=''):
    return MagicMock(status_code=status, text=text)


def test_list_videos_with_key_uses_data_api(caplog):
    caplog.set_level('INFO', logger='youtube.discovery')
    with patch.object(discovery, 'safe_get', return_value=_resp()) as get, \
            patch.object(discovery, 'read_response_capped', side_effect=_api_pages()):
        episodes = discovery.list_videos(_db('KEY'), _podcast(), limit=50)
    assert [e['video_id'] for e in episodes] == ['vid000000001']
    assert '[yt] Listed 1 video(s) via the YouTube Data API' in caplog.text
    assert episodes[0]['rss_duration'] == 3600.0
    assert 'playlistItems' in get.call_args_list[0].args[0]
    assert 'KEY' not in get.call_args_list[0].args[0]
    assert get.call_args_list[0].kwargs['headers']['X-Goog-Api-Key'] == 'KEY'


def test_quota_exhausted_falls_back_to_ytdlp():
    resp = _resp(403, '{"error":{"errors":[{"reason":"quotaExceeded"}]}}')
    with patch.object(discovery, 'safe_get', return_value=resp), \
            patch.object(discovery.ytdlp, 'run', return_value=_completed(YT_DLP_LISTING)) as run:
        episodes = discovery.list_videos(_db('KEY'), _podcast(), limit=50)
    assert run.called
    assert episodes[0]['video_id'] == 'vid000000001'


def test_api_404_is_source_not_found():
    with patch.object(discovery, 'safe_get', return_value=_resp(404)):
        with pytest.raises(discovery.SourceNotFound):
            discovery._list_with_api('KEY', 'PL1', 50, False, False, hide_private=True)


def test_api_other_status_is_discovery_error():
    with patch.object(discovery, 'safe_get', return_value=_resp(500)):
        with pytest.raises(discovery.DiscoveryError) as exc:
            discovery._list_with_api('KEY', 'PL1', 50, False, False, hide_private=True)
    assert '500' in str(exc.value)


def test_resolve_handle_without_key():
    with patch.object(discovery.ytdlp, 'run', return_value=_completed(YT_DLP_LISTING)):
        info = discovery.resolve_source(_db(), Source('handle', 'ExampleChannel'))
    assert info.kind == 'channel' and info.id == 'UCabcdefghijklmnopqrstuv'
    assert info.title == 'Example Channel' and info.video_count == 3
    assert info.author == 'Example Channel'


def test_api_playlist_author_is_the_owning_channel():
    page = {'items': [{'snippet': {'title': 'List', 'channelTitle': 'Owner Channel', 'thumbnails': {}},
                       'contentDetails': {'itemCount': 4}}]}
    with patch.object(discovery, 'safe_get', return_value=_resp()), \
            patch.object(discovery, 'read_response_capped', return_value=json.dumps(page).encode()):
        info = discovery.resolve_source(_db('KEY'), Source('playlist', 'PL1'))
    assert info.author == 'Owner Channel'


def test_parse_iso8601_duration():
    assert discovery.parse_iso_duration('PT1H2M3S') == 3723.0
    assert discovery.parse_iso_duration('PT45S') == 45.0
    assert discovery.parse_iso_duration('P0D') == 0.0
    assert discovery.parse_iso_duration(None) is None


def test_api_network_error_does_not_leak_key():
    exc = requests.ConnectionError('failed https://www.googleapis.com/youtube/v3/x?key=SECRETKEY')
    with patch.object(discovery, 'safe_get', side_effect=exc):
        with pytest.raises(discovery.DiscoveryError) as err:
            discovery._list_with_api('SECRETKEY', 'PL1', 50, False, False, hide_private=True)
    assert 'SECRETKEY' not in str(err.value)
    assert 'ConnectionError' in str(err.value)


def test_api_unreadable_body_is_discovery_error():
    with patch.object(discovery, 'safe_get', return_value=_resp()), \
            patch.object(discovery, 'read_response_capped', return_value=b'\xff not json'):
        with pytest.raises(discovery.DiscoveryError) as err:
            discovery._list_with_api('KEY', 'PL1', 50, False, False, hide_private=True)
    assert 'unreadable' in str(err.value)


def test_api_malformed_page_is_discovery_error():
    page = json.dumps({'items': [{'contentDetails': {}}]}).encode()
    with patch.object(discovery, 'safe_get', return_value=_resp()), \
            patch.object(discovery, 'read_response_capped', return_value=page):
        with pytest.raises(discovery.DiscoveryError) as err:
            discovery._list_with_api('KEY', 'PL1', 50, False, False, hide_private=True)
    assert 'unexpected payload' in str(err.value)


def test_ytdlp_timeout_is_discovery_error():
    with patch.object(discovery.ytdlp, 'run', side_effect=subprocess.TimeoutExpired('yt-dlp', 180)):
        with pytest.raises(discovery.DiscoveryError) as err:
            discovery.list_videos(_db(), _podcast(), limit=50)
    assert 'timed out' in str(err.value)


def test_ytdlp_not_installed_is_discovery_error():
    with patch.object(discovery.ytdlp, 'run', side_effect=discovery.ytdlp.YtDlpNotInstalled('yt-dlp is not installed')):
        with pytest.raises(discovery.DiscoveryError) as err:
            discovery.list_videos(_db(), _podcast(), limit=50)
    assert 'not installed' in str(err.value)


def test_api_source_not_found_falls_back_to_ytdlp_on_resolve():
    with patch.object(discovery, 'safe_get', return_value=_resp(404)), \
            patch.object(discovery.ytdlp, 'run', return_value=_completed(YT_DLP_LISTING)) as run:
        info = discovery.resolve_source(_db('KEY'), Source('custom', 'Name'))
    assert run.called and info.id == 'UCabcdefghijklmnopqrstuv'


def test_api_403_without_quota_falls_back_to_ytdlp_on_list():
    with patch.object(discovery, 'safe_get', return_value=_resp(403, '{"error":"keyInvalid"}')), \
            patch.object(discovery.ytdlp, 'run', return_value=_completed(YT_DLP_LISTING)) as run:
        episodes = discovery.list_videos(_db('KEY'), _podcast(), limit=50)
    assert run.called and episodes[0]['video_id'] == 'vid000000001'


def test_api_and_ytdlp_failing_raises_the_ytdlp_error():
    with patch.object(discovery, 'safe_get', return_value=_resp(403, '{}')), \
            patch.object(discovery.ytdlp, 'run', return_value=_completed({}, code=1, stderr='ERROR: boom')):
        with pytest.raises(discovery.DiscoveryError) as exc:
            discovery.list_videos(_db('KEY'), _podcast(), limit=50)
    assert 'boom' in str(exc.value) and '403' not in str(exc.value)


def test_api_item_without_snippet_does_not_escape_as_keyerror():
    payload = json.dumps({'items': [{'id': 'UCabcdefghijklmnopqrstuv'}]}).encode()
    with patch.object(discovery, 'safe_get', return_value=_resp()), \
            patch.object(discovery, 'read_response_capped', return_value=payload), \
            patch.object(discovery.ytdlp, 'run', return_value=_completed(YT_DLP_LISTING)) as run:
        info = discovery.resolve_source(_db('KEY'), Source('handle', 'x'))
    assert run.called and info.title == 'Example Channel'
    with patch.object(discovery, 'safe_get', return_value=_resp()), \
            patch.object(discovery, 'read_response_capped', return_value=payload):
        with pytest.raises(discovery.DiscoveryError, match='unexpected payload'):
            discovery._resolve_with_api('KEY', Source('handle', 'x'))


def _oldest_first_listing():
    return {'entries': [
        {'id': f'vid00000000{n}', 'title': f'Ep {n}', 'duration': 3600, 'live_status': 'was_live',
         'upload_date': f'2026100{n}'} for n in (1, 2, 3)]}


def test_playlist_lists_everything_then_caps_to_the_newest_via_ytdlp():
    with patch.object(discovery.ytdlp, 'run', return_value=_completed(_oldest_first_listing())):
        episodes = discovery.list_videos(_db(), _podcast(), limit=2)
    assert [e['video_id'] for e in episodes] == ['vid000000003', 'vid000000002']


def test_playlist_pages_through_all_items_and_caps_to_the_newest_via_api():
    def page(ids, token=None):
        body = {'items': [{'contentDetails': {'videoId': v, 'videoPublishedAt': f'2026-10-0{v[-1]}T00:00:00Z'}}
                          for v in ids]}
        if token:
            body['nextPageToken'] = token
        return json.dumps(body).encode()

    def videos(*ids):
        return json.dumps({'items': [{'id': v, 'snippet': {'title': v, 'publishedAt': ''},
                                      'contentDetails': {'duration': 'PT1H0M0S'}} for v in ids]}).encode()

    bodies = [page(['vid000000001'], 'next'), page(['vid000000002', 'vid000000003']), videos('vid000000003', 'vid000000002')]
    with patch.object(discovery, 'safe_get', return_value=_resp()) as get, \
            patch.object(discovery, 'read_response_capped', side_effect=bodies):
        episodes = discovery.list_videos(_db('KEY'), _podcast(), limit=2)
    assert [e['video_id'] for e in episodes] == ['vid000000003', 'vid000000002']
    assert 'pageToken=next' in get.call_args_list[1].args[0]
    assert 'vid000000003%2Cvid000000002' in get.call_args_list[2].args[0]


YT_DLP_VIDEO = {
    'id': 'abc123def45', 'title': 'One Video', 'description': 'about', 'channel': 'Example Channel',
    'timestamp': 1792000000, 'duration': 90, 'live_status': 'not_live',
    'thumbnails': [{'url': 'https://i.ytimg.com/v.jpg'}],
}
API_VIDEO = {'items': [{'id': 'abc123def45', 'snippet': {
    'title': 'One Video', 'description': 'about', 'channelTitle': 'Example Channel',
    'publishedAt': '2026-10-20T10:00:00Z', 'thumbnails': {'high': {'url': 'https://i.ytimg.com/v.jpg'}}},
    'contentDetails': {'duration': 'PT1M30S'}}]}


def test_resolve_video_without_key_uses_ytdlp():
    with patch.object(discovery.ytdlp, 'run', return_value=_completed(YT_DLP_VIDEO)) as run:
        info = discovery.resolve_source(_db(), Source('video', 'abc123def45'))
    assert (info.kind, info.id, info.title, info.video_count) == ('video', 'abc123def45', 'One Video', 1)
    assert info.author == 'Example Channel' and info.artwork_url == 'https://i.ytimg.com/v.jpg'
    argv = run.call_args.args[0]
    assert '--no-playlist' in argv and argv[-1] == 'https://www.youtube.com/watch?v=abc123def45'


def test_resolve_video_with_key_uses_videos_endpoint():
    with patch.object(discovery, 'safe_get', return_value=_resp()) as get, \
            patch.object(discovery, 'read_response_capped', return_value=json.dumps(API_VIDEO).encode()):
        info = discovery.resolve_source(_db('KEY'), Source('video', 'abc123def45'))
    assert 'videos?' in get.call_args.args[0] and 'part=snippet' in get.call_args.args[0]
    assert info.title == 'One Video' and info.author == 'Example Channel' and info.video_count == 1


def test_resolve_video_api_empty_items_falls_back_to_ytdlp():
    with patch.object(discovery, 'safe_get', return_value=_resp()), \
            patch.object(discovery, 'read_response_capped', return_value=b'{"items": []}'), \
            patch.object(discovery.ytdlp, 'run', return_value=_completed(YT_DLP_VIDEO, 1, 'Video unavailable')):
        with pytest.raises(discovery.SourceNotFound):
            discovery.resolve_source(_db('KEY'), Source('video', 'abc123def45'))


def test_list_single_video_via_ytdlp_keeps_a_short_and_skips_the_playlist():
    with patch.object(discovery.ytdlp, 'run', return_value=_completed(YT_DLP_VIDEO)) as run, \
            patch.object(discovery, '_playlist_for') as playlist_for:
        episodes = discovery.list_videos(_db(), _podcast('video', 'abc123def45'), limit=50)
    assert not playlist_for.called
    assert [e['video_id'] for e in episodes] == ['abc123def45']
    assert episodes[0]['title'] == 'One Video' and episodes[0]['rss_duration'] == 90.0
    assert episodes[0]['published'].startswith('2026-')
    assert '--no-playlist' in run.call_args.args[0]


def test_list_single_video_via_api_keeps_a_short():
    with patch.object(discovery, 'safe_get', return_value=_resp()) as get, \
            patch.object(discovery, 'read_response_capped', return_value=json.dumps(API_VIDEO).encode()):
        episodes = discovery.list_videos(_db('KEY'), _podcast('video', 'abc123def45'), limit=50)
    assert len(get.call_args_list) == 1 and 'videos?' in get.call_args.args[0]
    assert episodes[0]['video_id'] == 'abc123def45' and episodes[0]['rss_duration'] == 90.0
    assert episodes[0]['published'] == '2026-10-20T10:00:00Z'


def test_list_single_video_skips_a_live_stream():
    live = dict(YT_DLP_VIDEO, live_status='is_live')
    with patch.object(discovery.ytdlp, 'run', return_value=_completed(live)):
        assert discovery.list_videos(_db(), _podcast('video', 'abc123def45'), limit=50) == []


def test_api_get_forwards_the_proxy():
    with patch.object(discovery, 'safe_get', return_value=_resp()) as get, \
            patch.object(discovery, 'read_response_capped', return_value=b'{}'):
        discovery._api_get('videos', {}, 'KEY', proxy='socks5h://p:1080')
        discovery._api_get('videos', {}, 'KEY')
    assert get.call_args_list[0].kwargs['proxies'] == {'http': 'socks5h://p:1080', 'https': 'socks5h://p:1080'}
    assert get.call_args_list[1].kwargs['proxies'] is None


def test_list_videos_threads_the_stored_proxy_to_the_api():
    db = _db('KEY')
    db.get_setting.return_value = 'http://p:8080'
    with patch.object(discovery, 'safe_get', return_value=_resp()) as get, \
            patch.object(discovery, 'read_response_capped', side_effect=_api_pages()):
        discovery.list_videos(db, _podcast(), limit=50)
    assert all(c.kwargs['proxies'] == {'http': 'http://p:8080', 'https': 'http://p:8080'}
               for c in get.call_args_list)


def test_unsupported_source_kind_is_rejected():
    with pytest.raises(ValueError, match='unsupported YouTube source kind'):
        discovery.list_videos(_db(), _podcast('handle', 'x'), limit=5)


def test_single_video_ytdlp_path_applies_the_same_filter_as_the_api():
    for extra in ({'live_status': 'is_upcoming'}, {'live_status': 'post_live'}):
        with patch.object(discovery.ytdlp, 'run', return_value=_completed(dict(YT_DLP_VIDEO, **extra))):
            assert discovery.list_videos(_db(), _podcast('video', 'abc123def45'), limit=50) == []


def test_api_error_message_never_carries_the_proxy():
    exc = requests.ConnectionError('proxy http://u:SECRET@p:1 refused')
    with patch.object(discovery, 'safe_get', side_effect=exc):
        with pytest.raises(discovery.DiscoveryError) as err:
            discovery._api_get('videos', {}, 'KEY', proxy='http://u:SECRET@p:1')
    assert 'SECRET' not in str(err.value)



def _entry(source_id, title, channel='Example Channel', channel_id=None):
    entry = {'id': source_id, 'ie_key': 'YoutubeTab', 'title': title, 'channel': channel,
             'url': f'https://www.youtube.com/playlist?list={source_id}'}
    if channel_id:
        entry['channel_id'] = channel_id
    return entry


CHAN = 'UCabcdefghijklmnopqrstuv'
CHAN2 = 'UCzyxwvutsrqponmlkjihgfe'
PL_PLAIN = 'PLplain0000000000'
PL_POD = 'PLpodcast000000000'
PL_ONLY_TAB = 'PLtabonly000000000'
CHANNEL_ENTRY = {'id': CHAN, 'ie_key': 'YoutubeTab', 'title': 'Example Channel'}


@pytest.fixture(autouse=True)
def _clean_search_caches():
    discovery._search_cache.clear()
    discovery._podcast_tab_cache.clear()
    yield
    discovery._search_cache.clear()
    discovery._podcast_tab_cache.clear()


def _fake_ytdlp(search_entries, podcasts=None, fail=False):
    def fake(url, extra, timeout):
        if '/podcasts' in url:
            if fail:
                raise discovery.DiscoveryError('This channel does not have a podcasts tab')
            return {'entries': podcasts or []}
        return {'entries': search_entries}
    return fake


def test_search_strong_title_match_outranks_unrelated_tab_podcast():
    entries = [_entry(PL_PLAIN, 'Daily Tech Show', channel_id=CHAN)]
    fake = _fake_ytdlp(entries, podcasts=[_entry(PL_POD, 'Weekly Gardening Hour')])
    with patch.object(discovery, '_ytdlp_json', side_effect=fake):
        results = discovery.search_sources(_db(), 'daily tech show')
    assert [r['id'] for r in results] == [PL_PLAIN, PL_POD]
    assert results[0]['podcast'] is False and results[1]['podcast'] is True
    assert results[0]['url'] == f'https://www.youtube.com/playlist?list={PL_PLAIN}'


def test_search_podcast_marked_exact_match_outranks_plain_exact_match():
    entries = [_entry(PL_PLAIN, 'Daily Tech Show', channel_id=CHAN),
               _entry(PL_POD, 'Daily Tech Show', channel_id=CHAN)]
    fake = _fake_ytdlp(entries, podcasts=[_entry(PL_POD, 'Daily Tech Show')])
    with patch.object(discovery, '_ytdlp_json', side_effect=fake):
        results = discovery.search_sources(_db(), 'daily tech show')
    assert [r['id'] for r in results] == [PL_POD, PL_PLAIN]


def test_search_adds_podcast_playlist_missing_from_results():
    fake = _fake_ytdlp([CHANNEL_ENTRY], podcasts=[_entry(PL_ONLY_TAB, 'Example Show', channel='')])
    with patch.object(discovery, '_ytdlp_json', side_effect=fake):
        results = discovery.search_sources(_db(), 'example show')
    added = next(r for r in results if r['id'] == PL_ONLY_TAB)
    assert added['podcast'] is True and added['channelTitle'] == 'Example Channel'
    channel = next(r for r in results if r['kind'] == 'channel')
    assert channel['podcast'] is None and channel['channelTitle'] == 'Example Channel'


def test_search_failed_podcasts_lookup_leaves_podcast_unknown():
    entries = [CHANNEL_ENTRY, _entry(PL_PLAIN, 'Example Show', channel_id=CHAN)]
    with patch.object(discovery, '_ytdlp_json', side_effect=_fake_ytdlp(entries, fail=True)):
        results = discovery.search_sources(_db(), 'example')
    assert {r['id'] for r in results} == {CHAN, PL_PLAIN}
    assert all(r['podcast'] is None for r in results)


def test_search_full_tab_listing_leaves_unlisted_playlists_unknown():
    tab = [_entry(f'PLtab{i:012d}', f'Show {i}') for i in range(discovery.SEARCH_PODCAST_TAB_CAP)]
    entries = [_entry(PL_PLAIN, 'Example Show', channel_id=CHAN)]
    with patch.object(discovery, '_ytdlp_json', side_effect=_fake_ytdlp(entries, podcasts=tab)):
        results = discovery.search_sources(_db(), 'example show')
    assert next(r for r in results if r['id'] == PL_PLAIN)['podcast'] is None


def test_search_channel_tab_is_cached_across_queries():
    entries = [CHANNEL_ENTRY]
    with patch.object(discovery, '_ytdlp_json', side_effect=_fake_ytdlp(entries)) as run:
        discovery.search_sources(_db(), 'example one')
        discovery.search_sources(_db(), 'example two')
    tab_calls = [c for c in run.call_args_list if '/podcasts' in c.args[0]]
    assert len(tab_calls) == 1 and run.call_count == 3


def test_search_looks_up_at_most_three_channels():
    ids = [f'UC{i:022d}' for i in range(5)]
    entries = [{'id': i, 'ie_key': 'YoutubeTab', 'title': f'Channel {i}'} for i in ids]
    with patch.object(discovery, '_ytdlp_json', side_effect=_fake_ytdlp(entries)) as run:
        discovery.search_sources(_db(), 'channel')
    assert len([c for c in run.call_args_list if '/podcasts' in c.args[0]]) == discovery.SEARCH_CHANNEL_LOOKUPS


def test_search_caches_same_query():
    entries = [_entry(PL_PLAIN, 'Example Show')]
    with patch.object(discovery, '_ytdlp_json', side_effect=_fake_ytdlp(entries)) as run:
        first = discovery.search_sources(_db(), 'Example  Show')
        second = discovery.search_sources(_db(), 'example show')
    assert first == second and run.call_count == 1


def test_search_concurrent_same_query_runs_one_search():
    entries = [_entry(PL_PLAIN, 'Example Show')]
    results = []
    with patch.object(discovery, '_ytdlp_json', side_effect=_fake_ytdlp(entries)) as run:
        threads = [threading.Thread(target=lambda: results.append(discovery.search_sources(_db(), 'example show')))
                   for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    assert len(results) == 2 and run.call_count == 1


def test_search_cache_is_bounded():
    with patch.object(discovery, '_ytdlp_json', side_effect=_fake_ytdlp([])):
        for i in range(discovery._search_cache._max_size + 5):
            discovery.search_sources(_db(), f'query {i}')
    assert len(discovery._search_cache._store) == discovery._search_cache._max_size


def _api_search(podcast_status):
    def fake_api(path, params, key, *, proxy=''):
        assert path != 'channels'
        if path == 'search':
            return {'items': [
                {'id': {'kind': 'youtube#playlist', 'playlistId': PL_PLAIN},
                 'snippet': {'title': 'Example Show', 'channelTitle': 'Example Channel', 'channelId': CHAN,
                             'thumbnails': {'high': {'url': 'https://i.ytimg.com/p.jpg'}}}},
                {'id': {'kind': 'youtube#channel', 'channelId': CHAN},
                 'snippet': {'title': 'Example Channel', 'channelTitle': 'Example Channel'}}]}
        assert path == 'playlists' and params['id'] == PL_PLAIN
        status = {'podcastStatus': podcast_status} if podcast_status else {}
        return {'items': [{'id': PL_PLAIN, 'status': status, 'contentDetails': {'itemCount': 42}}]}
    return fake_api


@pytest.mark.parametrize('status,expected', [('enabled', True), ('disabled', False),
                                             ('unspecified', None), (None, None)])
def test_search_with_api_maps_podcast_status(status, expected):
    with patch.object(discovery, '_api_get', side_effect=_api_search(status)), \
            patch.object(discovery.ytdlp, 'configured_proxy', return_value=''), \
            patch.object(discovery, '_channel_podcasts', return_value=None):
        results = discovery.search_sources(_db(key='k'), 'example show')
    playlist = next(r for r in results if r['kind'] == 'playlist')
    channel = next(r for r in results if r['kind'] == 'channel')
    assert playlist['podcast'] is expected and playlist['videoCount'] == 42
    assert playlist['artworkUrl'] == 'https://i.ytimg.com/p.jpg'
    assert channel['videoCount'] is None


def test_search_with_api_expands_owner_podcasts_tab():
    tab = [_entry(PL_ONLY_TAB, 'Example Show Extra')]
    with patch.object(discovery, '_api_get', side_effect=_api_search('disabled')), \
            patch.object(discovery.ytdlp, 'configured_proxy', return_value=''), \
            patch.object(discovery, '_ytdlp_json', side_effect=_fake_ytdlp([], podcasts=tab)):
        results = discovery.search_sources(_db(key='k'), 'example show')
    assert next(r for r in results if r['id'] == PL_ONLY_TAB)['podcast'] is True


def test_search_falls_back_to_ytdlp_when_api_fails():
    entries = [_entry(PL_PLAIN, 'Example Show')]
    with patch.object(discovery, '_api_get', side_effect=discovery.QuotaExceeded('quota')), \
            patch.object(discovery.ytdlp, 'configured_proxy', return_value=''), \
            patch.object(discovery, '_ytdlp_json', side_effect=_fake_ytdlp(entries)):
        results = discovery.search_sources(_db(key='k'), 'example show')
    assert [r['id'] for r in results] == [PL_PLAIN]
