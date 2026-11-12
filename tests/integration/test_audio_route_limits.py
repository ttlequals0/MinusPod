"""Hostile numbers, load limits and edge cases of the audio routes."""
import os
import subprocess

import pytest

import api.patterns_audio as module
from api import preview_cache
from api.patterns import _store_pattern_audio
from tests.integration.fingerprint_helpers import EPISODE, SLUG, csrf  # noqa: E402
from tests.fingerprint_match_helpers import record_fingerprint_match

HUGE = '9' * 30
OVER_INT64 = '9223372036854775808'


def _audio(pid):
    return f'/api/v1/patterns/{pid}/audio'


@pytest.fixture(autouse=True)
def clean_preview_state():
    preview_cache._slice_cache.clear()
    yield
    preview_cache._slice_cache.clear()


@pytest.mark.parametrize('digits', [HUGE, '9' * 5000, '9' * 19])
def test_oversized_match_id_is_400(app_client, fp_audio_pattern, fp_audio_original, digits):
    r = app_client.get(f'{_audio(fp_audio_pattern)}/preview?matchId={digits}', headers=csrf(app_client))
    assert r.status_code == 400 and r.get_json()['error'] == 'matchId must be an integer'


def test_page_past_int_range_clamps_to_one_past_the_last_page(app_client, fp_feed, fp_audio_pattern):
    for i in range(12):
        record_fingerprint_match(fp_feed, fp_audio_pattern, EPISODE, SLUG, 10.0 + i, 40.0 + i,
                                         0.9, 0.8, True, False)
    for page in ('99999999999999999999', '9' * 400):
        r = app_client.get(f'{_audio(fp_audio_pattern)}/matches?page={page}', headers=csrf(app_client))
        body = r.get_json()
        assert r.status_code == 200 and body['matches'] == [] and body['page'] == 3
        assert body['total'] == 12


def test_page_clamp_on_an_empty_history_is_page_two(app_client, fp_audio_pattern):
    r = app_client.get(f'{_audio(fp_audio_pattern)}/matches?page=99999999999999999999',
                       headers=csrf(app_client))
    assert r.status_code == 200 and r.get_json()['page'] == 2


@pytest.mark.parametrize('method,suffix', [
    ('get', ''), ('put', ''), ('delete', ''), ('post', '/split'),
    ('post', '/protect'), ('delete', '/protect'), ('post', '/submit-to-community'),
    ('get', '/audio'), ('put', '/audio'), ('delete', '/audio'), ('get', '/audio/preview'),
    ('get', '/audio/matches'), ('post', '/audio/rebuild'),
])
@pytest.mark.parametrize('pid', ['9' * 20, OVER_INT64])
def test_oversized_pattern_id_is_404(app_client, method, suffix, pid):
    r = getattr(app_client, method)(f'/api/v1/patterns/{pid}{suffix}', json={}, headers=csrf(app_client))
    assert r.status_code == 404 and r.get_json() == {'error': 'Not found', 'status': 404}


def test_largest_pattern_id_still_routes(app_client):
    r = app_client.get('/api/v1/patterns/9223372036854775807', headers=csrf(app_client))
    assert r.status_code == 404 and r.get_json()['error'] == 'Pattern not found'


BAD_EPISODE = 'bad id'


@pytest.fixture
def bad_id_episode(fp_feed):
    """An episode row whose id cannot name a file, marked as keeping its original."""
    fp_feed.upsert_episode(SLUG, BAD_EPISODE, original_url='https://example.com/bad.mp3',
                           title='Bad', original_duration=300.0, status='processed',
                           original_file='x.mp3')
    return BAD_EPISODE


def test_malformed_stored_episode_id_is_not_retained_never_500(
        app_client, fp_feed, fp_pattern, bad_id_episode):
    fp_feed.create_audio_fingerprint(fp_pattern, b'AQAA', 6.0, source_podcast_slug=SLUG,
                                     source_episode_id=bad_id_episode, source_start=2.0, source_end=8.0)
    r = app_client.get(f'{_audio(fp_pattern)}/preview', headers=csrf(app_client))
    assert r.status_code == 404 and r.get_json()['code'] == 'original_not_retained'
    detail = app_client.get(_audio(fp_pattern), headers=csrf(app_client))
    body = detail.get_json()
    assert detail.status_code == 200 and body['available'] is False
    assert body['builtFrom']['available'] is False


def test_rebuild_from_a_malformed_episode_id_is_not_retained(
        app_client, fp_feed, fp_pattern, bad_id_episode, stub_fingerprinting):
    r = app_client.post(f'{_audio(fp_pattern)}/rebuild',
                        json={'podcastSlug': SLUG, 'episodeId': bad_id_episode, 'start': 2.0, 'end': 8.0},
                        headers=csrf(app_client))
    body = r.get_json()
    assert r.status_code == 200 and body['fingerprintStored'] is False
    assert body['fingerprintSkippedReason'] == 'original_not_retained'
    assert stub_fingerprinting == [] and fp_feed.get_audio_fingerprint(fp_pattern) is None


@pytest.mark.parametrize('start,end', [
    (10 ** 400, 5), (0, 10 ** 400), (-(10 ** 400), 5), (True, 8), ('2', 8), (2, False), (None, 8),
], ids=['huge-start', 'huge-end', 'huge-negative', 'bool-start', 'string-start', 'bool-end', 'null-start'])
def test_rebuild_rejects_unusable_span_numbers_with_a_400(
        app_client, fp_feed, fp_pattern, stub_fingerprinting, start, end):
    r = app_client.post(f'{_audio(fp_pattern)}/rebuild', headers=csrf(app_client),
                        json={'podcastSlug': SLUG, 'episodeId': EPISODE, 'start': start, 'end': end})
    assert r.status_code == 400 and r.get_json()['error'] == 'start and end must be finite numbers'
    assert stub_fingerprinting == []


def test_rebuild_rejects_a_non_finite_span_number_with_a_400(app_client, fp_pattern):
    body = '{"podcastSlug": "%s", "episodeId": "%s", "start": 1e999, "end": 8}' % (SLUG, EPISODE)
    r = app_client.post(f'{_audio(fp_pattern)}/rebuild', headers=csrf(app_client),
                        content_type='application/json', data=body)
    assert r.status_code == 400 and r.get_json()['error'] == 'start and end must be finite numbers'


def test_storing_audio_for_a_malformed_episode_id_is_not_retained(
        fp_feed, fp_pattern, bad_id_episode, stub_fingerprinting):
    result = _store_pattern_audio(fp_feed, SLUG, bad_id_episode, fp_pattern, 10.0, 40.0)
    assert (result.stored, result.skipped_reason) == (False, 'original_not_retained')
    assert stub_fingerprinting == [] and fp_feed.get_audio_fingerprint(fp_pattern) is None


def test_requires_a_session(app_client, fp_feed, fp_audio_pattern):
    from werkzeug.security import generate_password_hash
    fp_feed.set_setting('app_password', generate_password_hash('OverrideTest123!', method='scrypt'))
    try:
        with app_client.session_transaction() as sess:
            sess.clear()
        assert app_client.get(f'{_audio(fp_audio_pattern)}/matches').status_code == 401
    finally:
        fp_feed.set_setting('app_password', '')


def test_preview_is_never_stored_by_clients(app_client, fp_audio_pattern, fp_audio_original):
    r = app_client.get(f'{_audio(fp_audio_pattern)}/preview', headers=csrf(app_client))
    assert r.status_code == 200 and r.headers['Cache-Control'] == 'no-store'


@pytest.fixture
def spy(monkeypatch):
    calls = []
    real = module.tracked_run

    def run(cmd, **kw):
        calls.append(cmd)
        return real(cmd, **kw)
    monkeypatch.setattr(module, 'tracked_run', run)
    return calls


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(preview_cache, '_clock', lambda: now[0])
    return now


def _preview(client, pid, **headers):
    return client.get(f'{_audio(pid)}/preview', headers={**csrf(client), **headers})


def test_repeat_and_range_requests_encode_once(app_client, fp_audio_pattern, fp_audio_original, spy, clock):
    first = _preview(app_client, fp_audio_pattern)
    second = _preview(app_client, fp_audio_pattern, Range='bytes=0-99')
    assert first.status_code == 200 and second.status_code == 206 and len(second.data) == 100
    assert len(spy) == 1
    assert second.data == first.data[:100]


def test_cache_entry_expires_after_120_seconds(app_client, fp_audio_pattern, fp_audio_original, spy, clock):
    _preview(app_client, fp_audio_pattern)
    clock[0] += 119.0
    _preview(app_client, fp_audio_pattern)
    assert len(spy) == 1
    clock[0] += 2.0
    _preview(app_client, fp_audio_pattern)
    assert len(spy) == 2


def test_changed_file_is_encoded_again(app_client, fp_audio_pattern, fp_audio_original, spy, clock):
    _preview(app_client, fp_audio_pattern)
    stat = os.stat(fp_audio_original)
    os.utime(fp_audio_original, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    _preview(app_client, fp_audio_pattern)
    assert len(spy) == 2


def test_cache_holds_four_entries(app_client, fp_feed, fp_audio_pattern, fp_audio_original, spy, clock):
    ids = [record_fingerprint_match(fp_feed, fp_audio_pattern, EPISODE, SLUG, 1.0 + i, 4.0 + i,
                                            0.9, 0.9, True, False) for i in range(5)]
    for mid in ids:
        app_client.get(f'{_audio(fp_audio_pattern)}/preview?matchId={mid}', headers=csrf(app_client))
    assert len(spy) == 5 and len(preview_cache._slice_cache) == 4
    app_client.get(f'{_audio(fp_audio_pattern)}/preview?matchId={ids[-1]}', headers=csrf(app_client))
    assert len(spy) == 5
    app_client.get(f'{_audio(fp_audio_pattern)}/preview?matchId={ids[0]}', headers=csrf(app_client))
    assert len(spy) == 6


def test_third_concurrent_encode_is_503_with_retry_after(app_client, fp_audio_pattern, fp_audio_original, spy):
    assert module._preview_slots.acquire(blocking=False)
    assert module._preview_slots.acquire(blocking=False)
    try:
        r = _preview(app_client, fp_audio_pattern)
    finally:
        module._preview_slots.release()
        module._preview_slots.release()
    assert r.status_code == 503 and r.headers['Retry-After'] == '2'
    assert r.get_json()['status'] == 503 and r.get_json()['error']
    assert spy == []
    assert _preview(app_client, fp_audio_pattern).status_code == 200


def test_cached_slice_is_served_while_encoders_are_busy(app_client, fp_audio_pattern, fp_audio_original, spy, clock):
    _preview(app_client, fp_audio_pattern)
    assert module._preview_slots.acquire(blocking=False)
    assert module._preview_slots.acquire(blocking=False)
    try:
        assert _preview(app_client, fp_audio_pattern).status_code == 200
    finally:
        module._preview_slots.release()
        module._preview_slots.release()


def test_failed_encode_frees_its_slot_and_caches_nothing(app_client, fp_audio_pattern, fp_audio_original, monkeypatch):
    def boom(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd, stderr=b'x')
    monkeypatch.setattr(module, 'tracked_run', boom)
    for _ in range(3):
        assert _preview(app_client, fp_audio_pattern).status_code == 500
    assert len(preview_cache._slice_cache) == 0


def test_unsatisfiable_range_is_416(app_client, fp_audio_pattern, fp_audio_original):
    r = _preview(app_client, fp_audio_pattern, Range='bytes=99999999-')
    assert r.status_code == 416


def test_a_preview_is_not_served_once_the_original_is_gone(
        app_client, fp_feed, fp_audio_pattern, fp_audio_original, spy, clock):
    assert _preview(app_client, fp_audio_pattern).status_code == 200
    fp_audio_original.unlink()
    r = _preview(app_client, fp_audio_pattern)
    assert r.status_code == 404 and r.get_json()['code'] == 'original_not_retained'


def test_deleting_a_patterns_audio_drops_its_cached_slices(
        app_client, fp_feed, fp_audio_pattern, fp_audio_original, spy, clock):
    _preview(app_client, fp_audio_pattern)
    assert len(preview_cache._slice_cache) == 1
    assert app_client.delete(_audio(fp_audio_pattern), headers=csrf(app_client)).status_code == 200
    assert len(preview_cache._slice_cache) == 0
    fp_feed.create_audio_fingerprint(fp_audio_pattern, b'AQAA', 6.0, source_podcast_slug=SLUG,
                                     source_episode_id=EPISODE, source_start=2.0, source_end=8.0)
    assert _preview(app_client, fp_audio_pattern).status_code == 200
    assert len(spy) == 2


def test_rebuilding_a_patterns_audio_drops_its_cached_slices_only(
        app_client, fp_feed, fp_audio_pattern, fp_audio_original, spy, clock, stub_fingerprinting):
    other = fp_feed.create_ad_pattern(scope='podcast', podcast_id=SLUG, text_template='other read ' * 8)
    fp_feed.create_audio_fingerprint(other, b'AQAA', 6.0, source_podcast_slug=SLUG,
                                     source_episode_id=EPISODE, source_start=3.0, source_end=9.0)
    _preview(app_client, fp_audio_pattern)
    _preview(app_client, other)
    assert len(preview_cache._slice_cache) == 2
    r = app_client.post(f'{_audio(fp_audio_pattern)}/rebuild', headers=csrf(app_client),
                        json={'podcastSlug': SLUG, 'episodeId': EPISODE, 'start': 2.0, 'end': 8.0})
    assert r.status_code == 200 and r.get_json()['fingerprintStored'] is True
    assert [entry[2] for entry in preview_cache._slice_cache.values()] == [other]


def test_expired_entries_are_purged_when_a_slice_is_stored(
        app_client, fp_feed, fp_audio_pattern, fp_audio_original, spy, clock):
    first = record_fingerprint_match(fp_feed, fp_audio_pattern, EPISODE, SLUG, 1.0, 4.0, 0.9, 0.9, True, False)
    second = record_fingerprint_match(fp_feed, fp_audio_pattern, EPISODE, SLUG, 5.0, 8.0, 0.9, 0.9, True, False)
    app_client.get(f'{_audio(fp_audio_pattern)}/preview?matchId={first}', headers=csrf(app_client))
    clock[0] += preview_cache.SLICE_CACHE_TTL_S + 1
    app_client.get(f'{_audio(fp_audio_pattern)}/preview?matchId={second}', headers=csrf(app_client))
    assert len(preview_cache._slice_cache) == 1
