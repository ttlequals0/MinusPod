"""Confirm and adjust rebuild a pattern's fingerprint, reject marks the match."""
import json

from api import get_storage
from tests.integration.fingerprint_helpers import EPISODE, SLUG, post_correction  # noqa: E402


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
    row, = fp_feed.get_fingerprint_matches(fp_pattern, 10, 0)
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
    flags = {r['match_start']: r['rejected'] for r in fp_feed.get_fingerprint_matches(fp_pattern, 10, 0)}
    assert flags == {10.0: 0, 100.0: 1}


def test_rejected_flag_survives_the_resave_that_follows(app_client, fp_feed, fp_pattern):
    marker = _fingerprint_marker(fp_pattern)
    other = _fingerprint_marker(fp_pattern, 100.0, 130.0)
    fp_feed.save_episode_details(SLUG, EPISODE, ad_markers=[marker, other])
    fp_feed.record_fingerprint_matches_from_markers(SLUG, EPISODE, [marker, other])
    assert _reject(app_client, fp_pattern).status_code == 200
    rejected_at = next(r for r in fp_feed.get_fingerprint_matches(fp_pattern, 10, 0)
                       if r['rejected'])['rejected_at']

    stored = json.loads(fp_feed.get_episode(SLUG, EPISODE)['ad_markers_json'])
    storage = get_storage()
    storage.save_combined_ads(SLUG, EPISODE, stored)
    storage.save_combined_ads(SLUG, EPISODE, [marker, other])

    rows = sorted(fp_feed.get_fingerprint_matches(fp_pattern, 10, 0), key=lambda r: r['match_start'])
    assert [(r['match_start'], r['rejected']) for r in rows] == [(10.0, 1), (100.0, 0)]
    assert rows[0]['rejected_at'] == rejected_at
