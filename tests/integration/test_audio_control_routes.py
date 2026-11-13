"""Detail, toggle, delete and rebuild routes of a pattern's audio."""
import pytest

from tests.app_bootstrap import authenticate_test_client
from tests.integration.fingerprint_helpers import EPISODE, SLUG, csrf  # noqa: E402
from tests.fingerprint_match_helpers import record_fingerprint_match


def _audio(pid):
    return f'/api/v1/patterns/{pid}/audio'


def _get(client, pid):
    return client.get(_audio(pid), headers=csrf(client))


def _put(client, pid, body):
    return client.put(_audio(pid), json=body, headers=csrf(client))


def _rebuild(client, pid, **body):
    payload = {'podcastSlug': SLUG, 'episodeId': EPISODE, 'start': 10.0, 'end': 40.0}
    payload.update(body)
    payload = {k: v for k, v in payload.items() if v is not ...}
    return client.post(f'{_audio(pid)}/rebuild', json=payload, headers=csrf(client))


def _record(db, pid, start=10.0, end=40.0, **kw):
    args = dict(similarity=0.9, coverage=0.8, in_scope=True, audio_only=False)
    args.update(kw)
    return record_fingerprint_match(db, pid, EPISODE, SLUG, start, end, **args)


def test_detail_without_audio(app_client, fp_pattern):
    body = _get(app_client, fp_pattern).get_json()
    assert body['state'] == 'none' and body['available'] is False
    assert body['builtFrom'] is None and body['source'] is None and body['length'] is None
    assert body['counts'] == {'matches': 0, 'rejected': 0, 'outOfScope': 0}
    assert body['meanSimilarity'] is None and body['lastMatchedAt'] is None


def test_detail_active_with_source(app_client, fp_audio_pattern, fp_original):
    body = _get(app_client, fp_audio_pattern).get_json()
    assert body['state'] == 'active' and body['available'] is True and body['length'] == 6.0
    assert body['createdAt']
    assert body['builtFrom'] == {'podcastSlug': SLUG, 'episodeId': EPISODE,
                                 'start': 2.0, 'end': 8.0, 'available': True}
    assert body['source'] == {'podcastSlug': SLUG, 'episodeId': EPISODE, 'start': 2.0, 'end': 8.0}


def test_detail_source_follows_the_newest_retained_match(app_client, fp_feed, fp_audio_pattern, fp_original):
    _record(fp_feed, fp_audio_pattern, 20.0, 50.0)
    body = _get(app_client, fp_audio_pattern).get_json()
    assert body['source'] == {'podcastSlug': SLUG, 'episodeId': EPISODE, 'start': 20.0, 'end': 50.0}
    assert body['builtFrom']['start'] == 2.0


def test_detail_without_retained_original(app_client, fp_audio_pattern):
    body = _get(app_client, fp_audio_pattern).get_json()
    assert body['available'] is False and body['source'] is None
    assert body['builtFrom']['available'] is False and body['state'] == 'active'


def test_detail_counts_keep_rejected_and_out_of_scope_apart(app_client, fp_feed, fp_audio_pattern):
    _record(fp_feed, fp_audio_pattern, 10.0, 40.0, similarity=0.8)
    _record(fp_feed, fp_audio_pattern, 50.0, 80.0, similarity=0.6, in_scope=False)
    _record(fp_feed, fp_audio_pattern, 100.0, 130.0, similarity=0.1)
    assert fp_feed.mark_fingerprint_match_rejected(fp_audio_pattern, SLUG, EPISODE, 100.0, 130.0)
    body = _get(app_client, fp_audio_pattern).get_json()
    assert body['counts'] == {'matches': 2, 'rejected': 1, 'outOfScope': 1}
    assert body['meanSimilarity'] == pytest.approx(0.7)
    assert body['lastMatchedAt']


def test_detail_unknown_pattern_is_404(app_client, fp_feed):
    assert _get(app_client, 987654).status_code == 404


def test_toggle_off_and_on(app_client, fp_audio_pattern):
    off = _put(app_client, fp_audio_pattern, {'enabled': False})
    assert off.status_code == 200 and off.get_json()['state'] == 'off'
    assert _get(app_client, fp_audio_pattern).get_json()['state'] == 'off'
    assert _put(app_client, fp_audio_pattern, {'enabled': True}).get_json()['state'] == 'active'


@pytest.mark.parametrize('body', [{}, {'enabled': 'yes'}, {'enabled': 1}, {'enabled': None}, [True], 'x'])
def test_toggle_needs_a_boolean(app_client, fp_audio_pattern, body):
    assert _put(app_client, fp_audio_pattern, body).status_code == 400
    assert _get(app_client, fp_audio_pattern).get_json()['state'] == 'active'


def test_toggle_without_audio_is_404_no_audio(app_client, fp_pattern):
    r = _put(app_client, fp_pattern, {'enabled': False})
    assert r.status_code == 404 and r.get_json()['code'] == 'no_audio'


def test_toggle_unknown_pattern_is_404(app_client, fp_feed):
    r = _put(app_client, 987654, {'enabled': False})
    assert r.status_code == 404 and 'code' not in r.get_json()


def test_delete_removes_audio_and_history_and_sets_the_flag(app_client, fp_feed, fp_audio_pattern):
    _record(fp_feed, fp_audio_pattern)
    r = app_client.delete(_audio(fp_audio_pattern), headers=csrf(app_client))
    assert r.status_code == 200 and r.get_json() == {'deleted': True}
    assert fp_feed.get_audio_fingerprint(fp_audio_pattern) is None
    assert fp_feed.count_fingerprint_matches(fp_audio_pattern) == 0
    assert fp_feed.get_ad_pattern_by_id(fp_audio_pattern)['audio_removed'] == 1
    assert fp_feed.get_ad_pattern_by_id(fp_audio_pattern) is not None
    again = app_client.delete(_audio(fp_audio_pattern), headers=csrf(app_client))
    assert again.status_code == 404 and again.get_json()['code'] == 'no_audio'


def test_delete_without_audio_is_404_no_audio_and_keeps_the_flag_clear(app_client, fp_feed, fp_pattern):
    r = app_client.delete(_audio(fp_pattern), headers=csrf(app_client))
    assert r.status_code == 404 and r.get_json()['code'] == 'no_audio'
    assert fp_feed.get_ad_pattern_by_id(fp_pattern)['audio_removed'] == 0


def test_delete_unknown_pattern_is_404(app_client, fp_feed):
    assert app_client.delete(_audio(987654), headers=csrf(app_client)).status_code == 404


def test_rebuild_from_span(app_client, fp_feed, fp_original, fp_pattern, stub_fingerprinting):
    r = _rebuild(app_client, fp_pattern)
    body = r.get_json()
    assert r.status_code == 200 and body['fingerprintStored'] is True
    assert 'fingerprintSkippedReason' not in body
    assert body['state'] == 'active' and body['builtFrom']['start'] == 10.0
    assert 'audio' not in body
    row = fp_feed.get_audio_fingerprint(fp_pattern)
    assert (row['source_podcast_slug'], row['source_episode_id'],
            row['source_start'], row['source_end']) == (SLUG, EPISODE, 10.0, 40.0)


def test_rebuild_clears_audio_removed_and_keeps_the_off_flag(
        app_client, fp_feed, fp_original, fp_audio_pattern, stub_fingerprinting):
    fp_feed.set_fingerprint_enabled(fp_audio_pattern, False)
    fp_feed.set_pattern_audio_removed(fp_audio_pattern, True)
    assert _rebuild(app_client, fp_audio_pattern).get_json()['fingerprintStored'] is True
    assert fp_feed.get_ad_pattern_by_id(fp_audio_pattern)['audio_removed'] == 0
    assert fp_feed.get_audio_fingerprint(fp_audio_pattern)['enabled'] == 0


def test_rebuild_after_delete_audio_is_explicit(app_client, fp_feed, fp_original, fp_audio_pattern, stub_fingerprinting):
    assert fp_feed.delete_pattern_audio(fp_audio_pattern)
    assert _rebuild(app_client, fp_audio_pattern).get_json()['fingerprintStored'] is True


def test_rebuild_short_span_is_200_segment_too_short(app_client, fp_feed, fp_original, fp_audio_pattern, stub_fingerprinting):
    r = _rebuild(app_client, fp_audio_pattern, start=10.0, end=12.0)
    body = r.get_json()
    assert r.status_code == 200
    assert (body['fingerprintStored'], body['fingerprintSkippedReason']) == (False, 'segment_too_short')
    assert fp_feed.get_audio_fingerprint(fp_audio_pattern)['source_start'] == 2.0


def test_rebuild_without_retained_original_is_200_original_not_retained(
        app_client, fp_feed, fp_pattern, stub_fingerprinting):
    r = _rebuild(app_client, fp_pattern)
    body = r.get_json()
    assert r.status_code == 200
    assert (body['fingerprintStored'], body['fingerprintSkippedReason']) == (False, 'original_not_retained')
    assert body['state'] == 'none'


@pytest.mark.parametrize('change', [
    {'start': -1.0}, {'start': 40.0, 'end': 40.0}, {'start': 50.0, 'end': 40.0},
    {'start': float('nan')}, {'end': float('inf')}, {'start': 'a'}, {'end': True}, {'start': None},
    {'start': ...}, {'end': ...}, {'podcastSlug': ...}, {'episodeId': ...},
    {'podcastSlug': ''}, {'episodeId': 5}, {'end': 300.5}, {'start': 290.0, 'end': 400.0},
])
def test_rebuild_rejects_bad_spans_and_bodies(app_client, fp_feed, fp_original, fp_audio_pattern,
                                              stub_fingerprinting, change):
    r = _rebuild(app_client, fp_audio_pattern, **change)
    assert r.status_code == 400
    assert fp_feed.get_audio_fingerprint(fp_audio_pattern)['source_start'] == 2.0
    assert stub_fingerprinting == []


def test_rebuild_needs_a_json_object(app_client, fp_audio_pattern):
    for body in ([1], 'x'):
        r = app_client.post(f'{_audio(fp_audio_pattern)}/rebuild', json=body, headers=csrf(app_client))
        assert r.status_code == 400


def test_rebuild_span_to_the_exact_end_of_a_known_duration_is_allowed(
        app_client, fp_feed, fp_original, fp_audio_pattern, stub_fingerprinting):
    assert _rebuild(app_client, fp_audio_pattern, start=250.0, end=300.0).get_json()['fingerprintStored']


def test_rebuild_unknown_duration_skips_the_bound(app_client, fp_feed, fp_original, fp_audio_pattern, stub_fingerprinting):
    fp_feed.upsert_episode(SLUG, EPISODE, original_duration=None)
    assert _rebuild(app_client, fp_audio_pattern, start=1000.0, end=1100.0).status_code == 200


def test_rebuild_unknown_pattern_and_episode_are_404(app_client, fp_feed, fp_audio_pattern):
    assert _rebuild(app_client, 987654).status_code == 404
    assert _rebuild(app_client, fp_audio_pattern, episodeId='ffffffffffff').status_code == 404
    assert _rebuild(app_client, fp_audio_pattern, podcastSlug='no-such-feed').status_code == 404


def test_rebuild_uses_the_episode_of_the_given_podcast(app_client, fp_feed, fp_original, fp_audio_pattern, stub_fingerprinting):
    fp_feed.create_podcast('other-feed', 'https://example.com/other.xml', 'Other')
    fp_feed.upsert_episode('other-feed', EPISODE, original_url='https://example.com/o.mp3',
                           title='Same id elsewhere', original_duration=300.0, status='processed')
    try:
        body = _rebuild(app_client, fp_audio_pattern, podcastSlug='other-feed').get_json()
        assert body['fingerprintSkippedReason'] == 'original_not_retained'
    finally:
        fp_feed.delete_podcast('other-feed')


def test_routes_need_a_session_and_mutations_need_the_csrf_token(app_client, fp_feed, fp_audio_pattern):
    from werkzeug.security import generate_password_hash
    fp_feed.set_setting('app_password', generate_password_hash('OverrideTest123!', method='scrypt'))
    try:
        base = _audio(fp_audio_pattern)
        assert app_client.get(base).status_code == 401
        assert app_client.get(f'{base}/preview').status_code == 401
        headers = authenticate_test_client(app_client)
        assert app_client.put(base, json={'enabled': False}).status_code == 403
        assert app_client.delete(base).status_code == 403
        assert app_client.post(f'{base}/rebuild', json={}).status_code == 403
        assert fp_feed.get_audio_fingerprint(fp_audio_pattern)['enabled'] == 1
        assert app_client.put(base, json={'enabled': False}, headers=headers).status_code == 200
    finally:
        fp_feed.set_setting('app_password', '')


def test_rebuild_response_is_flat(app_client, fp_feed, fp_original, fp_pattern, stub_fingerprinting):
    body = _rebuild(app_client, fp_pattern).get_json()
    assert {'fingerprintStored', 'state', 'available', 'length', 'counts'} <= set(body)


def test_rebuild_span_over_600_seconds_is_400(app_client, fp_feed, fp_original, fp_audio_pattern, stub_fingerprinting):
    fp_feed.upsert_episode(SLUG, EPISODE, original_duration=None)
    assert _rebuild(app_client, fp_audio_pattern, start=0.0, end=600.5).status_code == 400
    assert _rebuild(app_client, fp_audio_pattern, start=0.0, end=600.0).status_code == 200


def test_rebuild_clamps_the_span_to_the_decoded_audio(
        app_client, fp_feed, fp_audio_original, fp_pattern, stub_fingerprinting):
    body = _rebuild(app_client, fp_pattern, start=6.0, end=40.0).get_json()
    assert body['fingerprintStored'] is True
    assert 5.5 < body['length'] < 6.6
    assert 11.5 < body['builtFrom']['end'] < 12.5


def test_rebuild_keeps_the_match_history(app_client, fp_feed, fp_original, fp_audio_pattern, stub_fingerprinting):
    record_fingerprint_match(fp_feed, fp_audio_pattern, EPISODE, SLUG, 1.0, 4.0, 0.9, 0.9, True, False)
    body = _rebuild(app_client, fp_audio_pattern).get_json()
    assert body['counts']['matches'] == 1


def test_last_matched_at_ignores_rejected_rows(app_client, fp_feed, fp_audio_pattern):
    conn = fp_feed.get_connection()
    kept = record_fingerprint_match(fp_feed, fp_audio_pattern, EPISODE, SLUG, 1.0, 4.0, 0.9, 0.9, True, False)
    late = record_fingerprint_match(fp_feed, fp_audio_pattern, EPISODE, SLUG, 20.0, 24.0, 0.9, 0.9, True, False)
    conn.execute("UPDATE audio_fingerprint_matches SET created_at = '2026-01-01T00:00:00Z' WHERE id = ?", (kept,))
    conn.execute("UPDATE audio_fingerprint_matches SET created_at = '2026-02-01T00:00:00Z', rejected = 1 "
                 "WHERE id = ?", (late,))
    conn.commit()
    body = app_client.get(_audio(fp_audio_pattern), headers=csrf(app_client)).get_json()
    assert body['lastMatchedAt'] == '2026-01-01T00:00:00Z'
    conn.execute("UPDATE audio_fingerprint_matches SET rejected = 1")
    conn.commit()
    assert app_client.get(_audio(fp_audio_pattern), headers=csrf(app_client)).get_json()['lastMatchedAt'] is None


def test_detail_carries_the_backfill_flag(app_client, fp_pattern, fp_feed):
    conn = fp_feed.get_connection()
    assert _get(app_client, fp_pattern).get_json()['historyBackfillPending'] is False
    conn.execute("DELETE FROM schema_migrations WHERE name = 'backfill_fingerprint_matches_v1'")
    conn.commit()
    try:
        assert _get(app_client, fp_pattern).get_json()['historyBackfillPending'] is True
    finally:
        conn.execute("INSERT OR IGNORE INTO schema_migrations (name) VALUES ('backfill_fingerprint_matches_v1')")
        conn.commit()


def _legacy(db, pid):
    """Blank the recorded source, as a fingerprint stored before sources were recorded."""
    conn = db.get_connection()
    conn.execute("UPDATE audio_fingerprints SET source_podcast_slug = NULL, source_episode_id = NULL, "
                 "source_start = NULL, source_end = NULL WHERE pattern_id = ?", (pid,))
    conn.commit()


def test_legacy_fingerprint_offers_the_resolved_span_as_built_from(app_client, fp_feed, fp_audio_pattern, fp_original):
    _legacy(fp_feed, fp_audio_pattern)
    _record(fp_feed, fp_audio_pattern, 20.0, 50.0)
    body = _get(app_client, fp_audio_pattern).get_json()
    assert body['builtFrom'] == {'podcastSlug': SLUG, 'episodeId': EPISODE,
                                 'start': 20.0, 'end': 50.0, 'available': True}


def test_rebuild_records_the_source_of_a_legacy_fingerprint(
        app_client, fp_feed, fp_original, fp_audio_pattern, stub_fingerprinting):
    _legacy(fp_feed, fp_audio_pattern)
    _record(fp_feed, fp_audio_pattern, 20.0, 50.0)
    assert _rebuild(app_client, fp_audio_pattern, start=22.0, end=48.0).status_code == 200
    row = fp_feed.get_audio_fingerprint(fp_audio_pattern)
    assert (row['source_podcast_slug'], row['source_episode_id'],
            row['source_start'], row['source_end']) == (SLUG, EPISODE, 22.0, 48.0)
    assert _get(app_client, fp_audio_pattern).get_json()['builtFrom']['start'] == 22.0


def test_legacy_fingerprint_without_a_resolvable_source_has_no_built_from(app_client, fp_feed, fp_audio_pattern):
    _legacy(fp_feed, fp_audio_pattern)
    body = _get(app_client, fp_audio_pattern).get_json()
    assert body['builtFrom'] is None and body['source'] is None


def test_legacy_built_from_skips_the_newest_out_of_scope_match(app_client, fp_feed, fp_audio_pattern, fp_original):
    _legacy(fp_feed, fp_audio_pattern)
    _record(fp_feed, fp_audio_pattern, 20.0, 50.0, in_scope=True)
    _record(fp_feed, fp_audio_pattern, 60.0, 90.0, in_scope=False)
    body = _get(app_client, fp_audio_pattern).get_json()
    assert body['source']['start'] == 60.0
    assert (body['builtFrom']['start'], body['builtFrom']['end']) == (20.0, 50.0)


def test_legacy_built_from_is_null_with_only_out_of_scope_matches(app_client, fp_feed, fp_audio_pattern, fp_original):
    _legacy(fp_feed, fp_audio_pattern)
    _record(fp_feed, fp_audio_pattern, 60.0, 90.0, in_scope=False)
    body = _get(app_client, fp_audio_pattern).get_json()
    assert body['builtFrom'] is None and body['source']['start'] == 60.0
