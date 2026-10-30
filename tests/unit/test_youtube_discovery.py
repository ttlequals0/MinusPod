import json
import subprocess

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
        'abcdefghijk': 'abcdefghijk', 'bcdefghijkl': '[Private video]', 'vid000000001': 'Long one'}


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


def _resp(status=200, text=''):
    return MagicMock(status_code=status, text=text)


def test_list_videos_with_key_uses_data_api():
    with patch.object(discovery, 'safe_get', return_value=_resp()) as get, \
            patch.object(discovery, 'read_response_capped', side_effect=_api_pages()):
        episodes = discovery.list_videos(_db('KEY'), _podcast(), limit=50)
    assert [e['video_id'] for e in episodes] == ['vid000000001']
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
            discovery._list_with_api('KEY', 'PL1', 50, False, False)


def test_api_other_status_is_discovery_error():
    with patch.object(discovery, 'safe_get', return_value=_resp(500)):
        with pytest.raises(discovery.DiscoveryError) as exc:
            discovery._list_with_api('KEY', 'PL1', 50, False, False)
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
            discovery._list_with_api('SECRETKEY', 'PL1', 50, False, False)
    assert 'SECRETKEY' not in str(err.value)
    assert 'ConnectionError' in str(err.value)


def test_api_unreadable_body_is_discovery_error():
    with patch.object(discovery, 'safe_get', return_value=_resp()), \
            patch.object(discovery, 'read_response_capped', return_value=b'\xff not json'):
        with pytest.raises(discovery.DiscoveryError) as err:
            discovery._list_with_api('KEY', 'PL1', 50, False, False)
    assert 'unreadable' in str(err.value)


def test_api_malformed_page_is_discovery_error():
    page = json.dumps({'items': [{'contentDetails': {}}]}).encode()
    with patch.object(discovery, 'safe_get', return_value=_resp()), \
            patch.object(discovery, 'read_response_capped', return_value=page):
        with pytest.raises(discovery.DiscoveryError) as err:
            discovery._list_with_api('KEY', 'PL1', 50, False, False)
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
