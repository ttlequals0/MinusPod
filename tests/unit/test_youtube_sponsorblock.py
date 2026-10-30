import json
from unittest.mock import MagicMock, patch

from youtube import sponsorblock as sb


def _resp(status, payload=None):
    r = MagicMock(status_code=status)
    r.json.return_value = payload
    r.text = json.dumps(payload) if payload is not None else ''
    return r


PAYLOAD = [
    {'videoID': 'other00000001', 'segments': [{'segment': [0, 5], 'UUID': 'x', 'category': 'sponsor',
                                                'actionType': 'skip', 'votes': 3, 'locked': 0, 'videoDuration': 100}]},
    {'videoID': 'vid000000001', 'segments': [
        {'segment': [10.5, 40.0], 'UUID': 'u1', 'category': 'sponsor', 'actionType': 'skip', 'votes': 5, 'locked': 0, 'videoDuration': 3600.2},
        {'segment': [100.0, 130.0], 'UUID': 'u2', 'category': 'selfpromo', 'actionType': 'mute', 'votes': 0, 'locked': 0, 'videoDuration': 3600.2},
        {'segment': [200.0, 210.0], 'UUID': 'u3', 'category': 'filler', 'actionType': 'skip', 'votes': 9, 'locked': 0, 'videoDuration': 3600.2},
        {'segment': [300.0, 320.0], 'UUID': 'u4', 'category': 'intro', 'actionType': 'skip', 'votes': -2, 'locked': 0, 'videoDuration': 3600.2},
        {'segment': [400.0, 420.0], 'UUID': 'u5', 'category': 'outro', 'actionType': 'skip', 'votes': -2, 'locked': 1, 'videoDuration': 3600.2},
        {'segment': [0, 3600.2], 'UUID': 'u6', 'category': 'sponsor', 'actionType': 'full', 'votes': 1, 'locked': 0, 'videoDuration': 3600.2},
    ]},
]


def test_fetch_uses_hash_prefix_and_filters_video():
    with patch.object(sb, 'safe_get', return_value=_resp(200, PAYLOAD)) as get:
        segments = sb.fetch_segments('vid000000001')
    url = get.call_args.args[0]
    assert '/api/skipSegments/' in url and 'service=YouTube' in url
    assert url.split('/api/skipSegments/')[1].split('?')[0] == sb.hash_prefix('vid000000001')
    assert get.call_args.kwargs['headers']['X-CLIENT-NAME'].startswith('MinusPod/')
    assert [s['UUID'] for s in segments] == ['u1', 'u2', 'u3', 'u4', 'u5', 'u6']


def test_fetch_404_means_no_marks_and_5xx_retries_once():
    with patch.object(sb, 'safe_get', return_value=_resp(404)):
        assert sb.fetch_segments('vid000000001') == []
    with patch.object(sb, 'safe_get', side_effect=[_resp(503), _resp(503)]) as get:
        assert sb.fetch_segments('vid000000001') is None
    assert get.call_count == 2


def test_to_markers_maps_categories_actions_and_sanity_rules():
    markers, note = sb.to_markers(PAYLOAD[1]['segments'], downloaded_duration=3600.0)
    assert note == 'full'
    by_uuid = {m['sb_uuid']: m for m in markers}
    assert set(by_uuid) == {'u1', 'u2', 'u5'}
    assert by_uuid['u1']['category'] == 'sponsor' and by_uuid['u1']['sb_action'] == 'skip'
    assert by_uuid['u2']['category'] == 'self_promo' and by_uuid['u2']['sb_action'] == 'mute'
    assert by_uuid['u5']['category'] == 'outro' and by_uuid['u5']['sb_locked'] is True
    assert all(m['source'] == 'sponsorblock' and m['confidence'] == 1.0 and m['was_cut'] for m in markers)
    assert [m['start'] for m in markers] == sorted(m['start'] for m in markers)
    assert sb.uuid_set(markers) == ['u1', 'u2', 'u5']


def test_to_markers_drops_response_on_duration_mismatch():
    markers, note = sb.to_markers(PAYLOAD[1]['segments'], downloaded_duration=3500.0)
    assert markers == [] and note == 'duration_mismatch'


def test_known_ad_includes_sponsorblock():
    from config import is_known_ad
    assert is_known_ad({'category': 'sponsor', 'source': 'sponsorblock'}) is True
    assert is_known_ad({'category': 'intro', 'source': 'sponsorblock'}) is False


def test_to_markers_skips_malformed_segments():
    good = {'segment': [10, 20], 'UUID': 'g', 'category': 'sponsor', 'actionType': 'skip', 'votes': 1, 'locked': 0}
    segments = [
        'not a dict',
        dict(good, UUID='str-vote', votes='abc'),
        dict(good, UUID='nan', segment=[float('nan'), 5]),
        dict(good, UUID='inf', segment=[1, float('inf')]),
        dict(good, UUID='short', segment=[1]),
        dict(good, UUID='inf-vote', votes=float('inf')),
        good,
        dict(good, UUID='past-end', segment=[90, 500]),
    ]
    markers, _ = sb.to_markers(segments, downloaded_duration=100.0)
    assert [m['sb_uuid'] for m in markers] == ['g', 'past-end']
    assert markers[1]['end'] == 100.0


def test_fetch_ignores_non_dict_entries_and_segments():
    payload = ['junk', {'videoID': 'vid000000001', 'segments': ['x', {'segment': [1, 2], 'UUID': 'a'}]}]
    with patch.object(sb, 'safe_get', return_value=_resp(200, payload)):
        assert sb.fetch_segments('vid000000001') == [{'segment': [1, 2], 'UUID': 'a'}]


def test_fetch_logs_one_warning_when_the_server_keeps_failing(caplog):
    with patch.object(sb, 'safe_get', side_effect=[_resp(503), _resp(503)]):
        with caplog.at_level('WARNING', logger=sb.logger.name):
            assert sb.fetch_segments('vid000000001') is None
    assert len([r for r in caplog.records if 'server errors' in r.message]) == 1
