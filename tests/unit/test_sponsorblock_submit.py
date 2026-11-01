from unittest.mock import MagicMock, patch

import requests

from tests.app_bootstrap import bootstrap

bootstrap('sponsorblock_submit_test_')

from config import known_ad_evidence
from main_app.pipeline import sponsorblock as sb_stage
from youtube import sponsorblock_submit as sbs


def marker(start=10.0, end=40.0, **over):
    base = {'start': start, 'end': end, 'category': 'sponsor', 'was_cut': True, 'sponsor_known': True}
    base.update(over)
    return base


def test_eligible_filters():
    keep = marker()
    markers = [
        keep,
        marker(50, 60, held_for_review=True, was_cut=False),
        marker(70, 80, was_cut=False),
        marker(90, 100, category='intro'),
        marker(110, 120, category='cross_promo'),
        marker(130, 140, sponsor_known=False),
        marker(150, 150),
        marker(160, 170, source='sponsorblock'),
        marker(180, 190, category='self_promo', reviewer_verdict='confirmed'),
    ]
    out = sbs.eligible_markers(markers)
    assert [m['start'] for m in out] == [10.0, 180]


def test_eligible_returns_recorded_markers_too():
    markers = [marker()]
    assert sbs.eligible_markers(markers) == markers


def test_find_submission_matches_by_overlap_and_prefers_final():
    recorded = [{'start': 10.2, 'end': 40.2, 'status': 'submitted'},
                {'start': 10, 'end': 40, 'status': 'error'}]
    assert sbs.find_submission(recorded, marker())['status'] == 'submitted'
    assert sbs.find_submission([recorded[1]], marker())['status'] == 'error'
    assert sbs.find_submission([{'start': 30, 'end': 70, 'status': 'submitted'}], marker()) is None
    assert sbs.find_submission([], marker()) is None


def test_known_ad_evidence_names_matching_branches():
    assert known_ad_evidence(marker(validation={'user_confirmed': True})) == ['user confirmed', 'known sponsor']
    assert known_ad_evidence(marker(sponsor_known=False, reviewer_verdict='confirmed')) == ['reviewer confirmed']
    assert known_ad_evidence(marker(sponsor_known=False, fingerprint_match_start=3.0)) == ['fingerprint']
    assert known_ad_evidence(marker(sponsor_known=False, pattern_defined=True)) == ['pattern']
    assert known_ad_evidence(marker(sponsor_known=False, validation={'confirmed_span': {'start': 1, 'end': 2}})) \
        == ['verified span']
    assert known_ad_evidence(marker(sponsor_known=False, source='sponsorblock')) == ['community mark']
    assert known_ad_evidence(marker(sponsor_known=False)) == []


def test_build_payload_shape():
    payload = sbs.build_payload('vid', 'u' * 32, [marker(1.23456, 9.87654), marker(20, 30, category='self_promo')])
    assert set(payload) == {'videoID', 'userID', 'userAgent', 'segments'}
    assert payload['userAgent'].startswith('MinusPod/')
    assert payload['segments'][0] == {'segment': [1.235, 9.877], 'category': 'sponsor', 'actionType': 'skip'}
    assert payload['segments'][1]['category'] == 'selfpromo'


def _resp(status, body=None, text=''):
    resp = MagicMock(status_code=status, text=text)
    resp.json.return_value = body
    return resp


def test_submit_returns_status_tuple():
    payload = sbs.build_payload('vid', 'u' * 32, [marker()])
    created = [{'UUID': 'abc', 'category': 'sponsor', 'segment': [10, 40]}]
    with patch.object(sbs, 'safe_post', return_value=_resp(200, created)) as post:
        assert sbs.submit(payload) == ('submitted', '', created)
        assert post.call_args.kwargs['json'] == payload
    for code, label in ((409, 'duplicate'), (403, 'rejected'), (429, 'rate_limited'), (500, 'error')):
        with patch.object(sbs, 'safe_post', return_value=_resp(code, text='nope')):
            status, detail, segments = sbs.submit(payload)
        assert (status, segments) == (label, []) and 'nope' in detail
    with patch.object(sbs, 'safe_post', side_effect=requests.ConnectionError('down')):
        assert sbs.submit(payload)[0] == 'error'


def test_build_records_maps_outcomes():
    markers = [marker(), marker(50, 60)]
    records = sbs.build_records(markers, 'submitted', '', [{'UUID': 'b', 'segment': [50, 60]}, {'UUID': 'a', 'segment': [10, 40]}])
    assert [(r['status'], r['uuid']) for r in records] == [('submitted', 'a'), ('submitted', 'b')]
    records = sbs.build_records(markers, 'rejected', 'why', [])
    assert {(r['status'], r['detail']) for r in records} == {('rejected', 'why')}
    assert all(r['category'] == 'sponsor' and r['at'] for r in records)


def test_merge_records_replaces_overlapping_span():
    old = [{'start': 10, 'end': 40, 'status': 'error'}, {'start': 50, 'end': 60, 'status': 'submitted'}]
    new = [{'start': 10.2, 'end': 40.2, 'status': 'submitted'}]
    merged = sbs.merge_records(old, new)
    assert [(r['start'], r['status']) for r in merged] == [(50, 'submitted'), (10.2, 'submitted')]


def test_pipeline_has_no_automatic_submission():
    assert not hasattr(sb_stage, 'sponsorblock_submit')
