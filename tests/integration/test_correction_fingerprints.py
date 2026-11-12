"""Confirm and adjust rebuild a pattern's fingerprint, reject marks the match."""
import json

import pytest

from api import get_storage
from tests.integration.fingerprint_helpers import (  # noqa: E402
    EPISODE, SLUG, post_correction, seed_two_sponsor_transcript,
)
from tests.integration.openapi_conformance import assert_response_conforms
from tests.fingerprint_match_helpers import get_fingerprint_matches


def _confirm(app_client, pid, start=10.0, end=40.0, **extra):
    return post_correction(app_client, {
        'type': 'confirm', 'original_ad': {'start': start, 'end': end, 'pattern_id': pid},
        **extra})


def _adjust(app_client, pid, start=20.0, end=60.0):
    return post_correction(app_client, {
        'type': 'adjust', 'original_ad': {'start': 10.0, 'end': 40.0, 'pattern_id': pid},
        'adjusted_start': start, 'adjusted_end': end})


def _reject(app_client, pid, start=10.0, end=40.0):
    return post_correction(app_client, {
        'type': 'reject', 'original_ad': {'start': start, 'end': end, 'pattern_id': pid}})


def _fingerprint_marker(pid, start=10.0, end=40.0):
    return {'start': start, 'end': end, 'detection_stage': 'fingerprint', 'pattern_id': pid,
            'fingerprint_match_start': start, 'fingerprint_match_end': end,
            'fingerprint_similarity': 0.9, 'fingerprint_coverage': 0.8,
            'validation': {'decision': 'ACCEPT'}, 'was_cut': True}


def test_confirm_stores_a_fingerprint_when_the_pattern_has_none(
        app_client, fp_feed, fp_original, fp_pattern, stub_fingerprinting):
    assert _confirm(app_client, fp_pattern).status_code == 200
    row = fp_feed.get_audio_fingerprint(fp_pattern)
    assert (row['source_podcast_slug'], row['source_episode_id'],
            row['source_start'], row['source_end']) == (SLUG, EPISODE, 10.0, 40.0)


def test_confirm_uses_the_trimmed_span(app_client, fp_feed, fp_original, fp_pattern, stub_fingerprinting):
    assert _confirm(app_client, fp_pattern, adjusted_start=15.0, adjusted_end=35.0).status_code == 200
    row = fp_feed.get_audio_fingerprint(fp_pattern)
    assert (row['source_start'], row['source_end'], row['duration']) == (15.0, 35.0, 20.0)


def test_confirm_keeps_an_existing_fingerprint(
        app_client, fp_feed, fp_original, fp_pattern, stub_fingerprinting):
    fp_feed.create_audio_fingerprint(fp_pattern, b'mine', 25.0, SLUG, EPISODE, 100.0, 125.0)
    assert _confirm(app_client, fp_pattern).status_code == 200
    row = fp_feed.get_audio_fingerprint(fp_pattern)
    assert (row['fingerprint'], row['source_start']) == (b'mine', 100.0)
    assert stub_fingerprinting == []


def test_confirm_respects_deleted_audio(app_client, fp_feed, fp_original, fp_pattern, stub_fingerprinting):
    fp_feed.set_pattern_audio_removed(fp_pattern, True)
    assert _confirm(app_client, fp_pattern).status_code == 200
    assert fp_feed.get_audio_fingerprint(fp_pattern) is None
    assert fp_feed.get_ad_pattern_by_id(fp_pattern)['audio_removed'] == 1


def test_confirm_succeeds_without_a_retained_original(app_client, fp_feed, fp_pattern, stub_fingerprinting):
    resp = _confirm(app_client, fp_pattern)
    assert resp.status_code == 200 and resp.get_json()['pattern_id'] == fp_pattern
    assert fp_feed.get_audio_fingerprint(fp_pattern) is None


def test_confirm_succeeds_when_storing_blows_up(app_client, fp_feed, fp_original, fp_pattern, monkeypatch):
    import api.patterns as patterns

    def boom(*a, **k):
        raise RuntimeError('boom')
    monkeypatch.setattr(patterns.AudioFingerprinter, 'store_fingerprint', boom)
    assert _confirm(app_client, fp_pattern).status_code == 200


def test_adjust_rebuilds_from_the_new_span(app_client, fp_feed, fp_original, fp_pattern, stub_fingerprinting):
    fp_feed.create_audio_fingerprint(fp_pattern, b'old', 30.0, SLUG, EPISODE, 10.0, 40.0)
    assert _adjust(app_client, fp_pattern).status_code == 200
    row = fp_feed.get_audio_fingerprint(fp_pattern)
    assert (row['source_start'], row['source_end'], row['duration']) == (20.0, 60.0, 40.0)
    assert row['fingerprint'] != b'old'


def test_adjust_overrides_deleted_audio_and_clears_the_marker(
        app_client, fp_feed, fp_original, fp_pattern, stub_fingerprinting):
    fp_feed.set_pattern_audio_removed(fp_pattern, True)
    assert _adjust(app_client, fp_pattern).status_code == 200
    assert fp_feed.get_ad_pattern_by_id(fp_pattern)['audio_removed'] == 0
    assert fp_feed.get_audio_fingerprint(fp_pattern)['source_start'] == 20.0


def test_failed_adjust_rebuild_leaves_the_deleted_marker(app_client, fp_feed, fp_pattern, stub_fingerprinting):
    fp_feed.set_pattern_audio_removed(fp_pattern, True)
    assert _adjust(app_client, fp_pattern).status_code == 200
    assert fp_feed.get_ad_pattern_by_id(fp_pattern)['audio_removed'] == 1
    assert fp_feed.get_audio_fingerprint(fp_pattern) is None


def test_adjust_keeps_a_switched_off_fingerprint_off(
        app_client, fp_feed, fp_original, fp_pattern, stub_fingerprinting):
    fp_feed.create_audio_fingerprint(fp_pattern, b'old', 30.0, enabled=False)
    assert _adjust(app_client, fp_pattern).status_code == 200
    assert fp_feed.get_audio_fingerprint(fp_pattern)['enabled'] == 0


def test_reject_marks_the_match_rejected(app_client, fp_feed, fp_pattern):
    fp_feed.record_fingerprint_matches_from_markers(SLUG, EPISODE, [_fingerprint_marker(fp_pattern)])
    assert _reject(app_client, fp_pattern).status_code == 200
    row, = get_fingerprint_matches(fp_feed, fp_pattern, 10, 0)
    assert row['rejected'] == 1 and row['rejected_at']
    assert fp_feed.get_ad_pattern_by_id(fp_pattern)['false_positive_count'] == 1


def test_reject_without_a_recorded_match_still_succeeds(app_client, fp_feed, fp_pattern):
    assert _reject(app_client, fp_pattern).status_code == 200
    assert fp_feed.count_fingerprint_matches(fp_pattern) == 0


def test_reject_touches_only_the_overlapping_match(app_client, fp_feed, fp_pattern):
    fp_feed.record_fingerprint_matches_from_markers(
        SLUG, EPISODE, [_fingerprint_marker(fp_pattern, 10.0, 40.0),
                        _fingerprint_marker(fp_pattern, 100.0, 130.0)])
    _reject(app_client, fp_pattern, 100.0, 130.0)
    flags = {r['match_start']: r['rejected'] for r in get_fingerprint_matches(fp_feed, fp_pattern, 10, 0)}
    assert flags == {10.0: 0, 100.0: 1}


def test_rejected_flag_survives_the_resave_that_follows(app_client, fp_feed, fp_pattern):
    marker = _fingerprint_marker(fp_pattern)
    other = _fingerprint_marker(fp_pattern, 100.0, 130.0)
    fp_feed.save_episode_details(SLUG, EPISODE, ad_markers=[marker, other])
    fp_feed.record_fingerprint_matches_from_markers(SLUG, EPISODE, [marker, other])
    assert _reject(app_client, fp_pattern).status_code == 200
    rejected_at = next(r for r in get_fingerprint_matches(fp_feed, fp_pattern, 10, 0)
                       if r['rejected'])['rejected_at']

    stored = json.loads(fp_feed.get_episode(SLUG, EPISODE)['ad_markers_json'])
    storage = get_storage()
    storage.save_combined_ads(SLUG, EPISODE, stored)
    storage.save_combined_ads(SLUG, EPISODE, [marker, other])

    rows = sorted(get_fingerprint_matches(fp_feed, fp_pattern, 10, 0), key=lambda r: r['match_start'])
    assert [(r['match_start'], r['rejected']) for r in rows] == [(10.0, 1), (100.0, 0)]
    assert rows[0]['rejected_at'] == rejected_at


CORRECTIONS = '/episodes/{slug}/{episode_id}/corrections'


def _split_confirm(app_client, start=10.0, end=70.0):
    return post_correction(app_client, {
        'type': 'confirm', 'original_ad': {'start': start, 'end': end, 'sponsor': 'Acme'}})


def _split_adjust(app_client, end=70.0):
    return post_correction(app_client, {
        'type': 'adjust', 'original_ad': {'start': 10.0, 'end': 70.0, 'sponsor': 'Acme'},
        'adjusted_start': 10.0, 'adjusted_end': end})


def _own_spans(db, response):
    """(response body, {pattern id: source span}) of the patterns this episode created."""
    ids = [row['id'] for row in db.get_connection().execute(
        "SELECT id FROM ad_patterns WHERE created_from_episode_id = ?", (EPISODE,)).fetchall()]
    rows = {pid: db.get_audio_fingerprint(pid) for pid in ids}
    return response.get_json(), {pid: (r['source_start'], r['source_end']) for pid, r in rows.items() if r}


def test_confirm_of_a_split_span_fingerprints_each_pattern_over_its_own_span(
        app_client, fp_feed, fp_original, stub_fingerprinting):
    seed_two_sponsor_transcript(fp_feed)
    response = _split_confirm(app_client)
    body, spans = _own_spans(fp_feed, response)
    assert response.status_code == 200 and body['fingerprintStored'] is True
    assert sorted(spans.values()) == [(10.0, 40.0), (40.0, 70.0)]


def test_confirm_that_creates_no_pattern_omits_the_fingerprint_fields(
        app_client, fp_feed, fp_original, stub_fingerprinting):
    fp_feed.save_original_transcript(SLUG, EPISODE, '[00:00:10.000 --> 00:01:10.000] Something else.')
    response = _split_confirm(app_client)
    body = response.get_json()
    assert response.status_code == 200
    assert 'fingerprintStored' not in body and 'fingerprintSkippedReason' not in body
    assert stub_fingerprinting == []
    assert_response_conforms('post', CORRECTIONS, response)


def test_adjust_of_a_split_span_fingerprints_each_pattern_over_its_own_span(
        app_client, fp_feed, fp_original, stub_fingerprinting):
    seed_two_sponsor_transcript(fp_feed)
    response = _split_adjust(app_client)
    body, spans = _own_spans(fp_feed, response)
    assert body['fingerprintStored'] is True
    assert sorted(spans.values()) == [(10.0, 40.0), (40.0, 70.0)]


def _split_pair(db, first_response):
    """(Acme pattern id, Beta Corp pattern id) of the split created by the first response."""
    body = first_response.get_json()
    primary = body['pattern_id']
    other, = [pid for pid in db.get_connection().execute(
        "SELECT id FROM ad_patterns WHERE created_from_episode_id = ?", (EPISODE,)).fetchall()
        for pid in [pid['id']] if pid != primary]
    return primary, other


def test_adjust_forces_only_the_marker_pattern_of_a_split(
        app_client, fp_feed, fp_original, stub_fingerprinting):
    seed_two_sponsor_transcript(fp_feed)
    acme, beta = _split_pair(fp_feed, _split_adjust(app_client))
    before = fp_feed.get_audio_fingerprint(beta)
    stub_fingerprinting.clear()
    assert _split_adjust(app_client, end=65.0).status_code == 200
    assert fp_feed.get_audio_fingerprint(beta) == before
    assert [call[1] for call in stub_fingerprinting] == [10.0]
    assert fp_feed.get_audio_fingerprint(acme)['source_end'] == 40.0


def test_adjust_leaves_the_deleted_audio_of_another_split_pattern_alone(
        app_client, fp_feed, fp_original, stub_fingerprinting):
    seed_two_sponsor_transcript(fp_feed)
    acme, beta = _split_pair(fp_feed, _split_adjust(app_client))
    fp_feed.delete_audio_fingerprint(beta)
    fp_feed.set_pattern_audio_removed(beta, True)
    fp_feed.set_pattern_audio_removed(acme, True)
    body = _split_adjust(app_client, end=65.0).get_json()
    assert body['fingerprintStored'] is True
    assert fp_feed.get_audio_fingerprint(beta) is None
    assert fp_feed.get_ad_pattern_by_id(beta)['audio_removed'] == 1
    assert fp_feed.get_ad_pattern_by_id(acme)['audio_removed'] == 0


def test_confirm_reports_the_outcome(app_client, fp_feed, fp_original, fp_pattern, stub_fingerprinting):
    body = _confirm(app_client, fp_pattern).get_json()
    assert body['fingerprintStored'] is True and 'fingerprintSkippedReason' not in body


def test_confirm_reports_an_existing_fingerprint(
        app_client, fp_feed, fp_original, fp_pattern, stub_fingerprinting):
    fp_feed.create_audio_fingerprint(fp_pattern, b'mine', 25.0, enabled=False)
    body = _confirm(app_client, fp_pattern).get_json()
    assert (body['fingerprintStored'], body['fingerprintSkippedReason']) == (False, 'fingerprint_exists')
    assert fp_feed.get_audio_fingerprint(fp_pattern)['enabled'] == 0


def test_confirm_reports_deleted_audio(app_client, fp_feed, fp_original, fp_pattern, stub_fingerprinting):
    fp_feed.set_pattern_audio_removed(fp_pattern, True)
    body = _confirm(app_client, fp_pattern).get_json()
    assert body['fingerprintSkippedReason'] == 'audio_removed'


def test_confirm_reports_a_missing_original(app_client, fp_feed, fp_pattern, stub_fingerprinting):
    assert _confirm(app_client, fp_pattern).get_json()['fingerprintSkippedReason'] == 'original_not_retained'


def test_adjust_reports_the_outcome(app_client, fp_feed, fp_original, fp_pattern, stub_fingerprinting):
    body = _adjust(app_client, fp_pattern).get_json()
    assert body['fingerprintStored'] is True and 'fingerprintSkippedReason' not in body


def test_adjust_reports_a_short_span(app_client, fp_feed, fp_original, fp_pattern, stub_fingerprinting):
    body = _adjust(app_client, fp_pattern, 20.0, 22.0).get_json()
    assert (body['fingerprintStored'], body['fingerprintSkippedReason']) == (False, 'segment_too_short')


@pytest.mark.parametrize('kind', ['create', 'confirm', 'adjust', 'reject'])
def test_corrections_responses_match_the_spec(app_client, fp_feed, fp_original, fp_pattern,
                                              stub_fingerprinting, kind):
    if kind == 'create':
        response = post_correction(app_client, {
            'type': 'create', 'start': 10.0, 'end': 40.0, 'sponsor': 'Acme',
            'text_template': 'This episode is brought to you by Acme, the easy way to file your taxes online.'})
    else:
        response = {'confirm': _confirm, 'adjust': _adjust, 'reject': _reject}[kind](app_client, fp_pattern)
    assert_response_conforms('post', CORRECTIONS, response)


def test_confirming_a_rejected_ad_restores_its_match(app_client, fp_feed, fp_original, fp_pattern,
                                                     stub_fingerprinting):
    fp_feed.record_fingerprint_matches_from_markers(SLUG, EPISODE, [_fingerprint_marker(fp_pattern)])
    _reject(app_client, fp_pattern)
    assert _confirm(app_client, fp_pattern).status_code == 200
    row, = get_fingerprint_matches(fp_feed, fp_pattern, 10, 0)
    assert (row['rejected'], row['rejected_at']) == (0, None)
    marker = _fingerprint_marker(fp_pattern)
    assert fp_feed.record_fingerprint_matches_from_markers(SLUG, EPISODE, [marker]) == 1
    assert [r['rejected'] for r in get_fingerprint_matches(fp_feed, fp_pattern, 10, 0)] == [0]


def test_adjusting_a_rejected_ad_restores_the_match_the_adjusted_bounds_cover(
        app_client, fp_feed, fp_original, fp_pattern, stub_fingerprinting):
    fp_feed.record_fingerprint_matches_from_markers(
        SLUG, EPISODE, [_fingerprint_marker(fp_pattern, 10.0, 40.0),
                        _fingerprint_marker(fp_pattern, 100.0, 140.0)])
    _reject(app_client, fp_pattern, 10.0, 40.0)
    _reject(app_client, fp_pattern, 100.0, 140.0)
    assert _adjust(app_client, fp_pattern, 12.0, 38.0).status_code == 200
    flags = {r['match_start']: r['rejected'] for r in get_fingerprint_matches(fp_feed, fp_pattern, 10, 0)}
    assert flags == {10.0: 0, 100.0: 1}


def test_confirming_without_a_conflicting_rejection_leaves_rejected_matches(
        app_client, fp_feed, fp_original, fp_pattern, stub_fingerprinting):
    fp_feed.record_fingerprint_matches_from_markers(SLUG, EPISODE, [_fingerprint_marker(fp_pattern, 100.0, 140.0)])
    fp_feed.mark_fingerprint_match_rejected(fp_pattern, SLUG, EPISODE, 100.0, 140.0)
    assert _confirm(app_client, fp_pattern).status_code == 200
    assert [r['rejected'] for r in get_fingerprint_matches(fp_feed, fp_pattern, 10, 0)] == [1]


def _merged_marker(first, second):
    """A model-detected ad whose merge folded in fingerprint matches of two patterns."""
    members = [{'stage': 'fingerprint', 'pattern_id': pid, 'start': 10.0, 'end': 40.0,
                'fingerprint_match_start': 10.0, 'fingerprint_match_end': 40.0,
                'fingerprint_similarity': 0.9, 'fingerprint_coverage': 0.8} for pid in (first, second)]
    return {'start': 10.0, 'end': 40.0, 'detection_stage': 'claude', 'pattern_id': first,
            'merged_member_spans': members, 'validation': {'decision': 'ACCEPT'}, 'was_cut': True}


def _second_pattern(db):
    return db.create_ad_pattern(scope='podcast', podcast_id=SLUG, text_template='Another read of a sponsor here.')


def _rejected_flags(db, *pattern_ids):
    return [[r['rejected'] for r in get_fingerprint_matches(db, pid, 10, 0)] for pid in pattern_ids]


def test_reject_marks_the_match_of_every_fingerprint_member(app_client, fp_feed, fp_pattern):
    other = _second_pattern(fp_feed)
    marker = _merged_marker(fp_pattern, other)
    fp_feed.save_episode_details(SLUG, EPISODE, ad_markers=[marker])
    fp_feed.record_fingerprint_matches_from_markers(SLUG, EPISODE, [marker])
    assert _reject(app_client, fp_pattern).status_code == 200
    assert _rejected_flags(fp_feed, fp_pattern, other) == [[1], [1]]


def test_confirming_a_rejected_merged_ad_restores_every_member_match(app_client, fp_feed, fp_pattern):
    other = _second_pattern(fp_feed)
    marker = _merged_marker(fp_pattern, other)
    fp_feed.save_episode_details(SLUG, EPISODE, ad_markers=[marker])
    fp_feed.record_fingerprint_matches_from_markers(SLUG, EPISODE, [marker])
    _reject(app_client, fp_pattern)
    assert _confirm(app_client, fp_pattern).status_code == 200
    assert _rejected_flags(fp_feed, fp_pattern, other) == [[0], [0]]


def test_adjusting_a_rejected_merged_ad_restores_every_member_match(app_client, fp_feed, fp_pattern):
    other = _second_pattern(fp_feed)
    marker = _merged_marker(fp_pattern, other)
    fp_feed.save_episode_details(SLUG, EPISODE, ad_markers=[marker])
    fp_feed.record_fingerprint_matches_from_markers(SLUG, EPISODE, [marker])
    _reject(app_client, fp_pattern)
    assert _adjust(app_client, fp_pattern, 12.0, 38.0).status_code == 200
    assert _rejected_flags(fp_feed, fp_pattern, other) == [[0], [0]]


@pytest.mark.parametrize('kind', ['confirm', 'adjust'])
def test_restoring_does_not_need_the_stored_marker(app_client, fp_feed, fp_pattern, kind):
    other = _second_pattern(fp_feed)
    marker = _merged_marker(fp_pattern, other)
    fp_feed.save_episode_details(SLUG, EPISODE, ad_markers=[marker])
    fp_feed.record_fingerprint_matches_from_markers(SLUG, EPISODE, [marker])
    _reject(app_client, fp_pattern)
    fp_feed.save_episode_details(SLUG, EPISODE, ad_markers=[])
    response = _confirm(app_client, fp_pattern) if kind == 'confirm' else _adjust(app_client, fp_pattern, 12.0, 38.0)
    assert response.status_code == 200
    assert _rejected_flags(fp_feed, fp_pattern, other) == [[0], [0]]
