"""The audio detail names the span a missing fingerprint could be built from."""
import pytest

from tests.integration.fingerprint_helpers import EPISODE, SLUG, TEMPLATE, csrf


def _detail(client, pid):
    return client.get(f'/api/v1/patterns/{pid}/audio', headers=csrf(client)).get_json()


def _pattern(db, **kw):
    args = dict(scope='podcast', podcast_id=SLUG, text_template=TEMPLATE, created_from_episode_id=EPISODE)
    args.update(kw)
    return db.create_ad_pattern(**args)


def _markers(db, markers):
    db.save_episode_details(SLUG, EPISODE, ad_markers=markers)


def test_names_the_marker_of_the_pattern_in_its_origin_episode(app_client, fp_feed, fp_original):
    pid = _pattern(fp_feed)
    _markers(fp_feed, [{'start': 50.0, 'end': 80.0, 'pattern_id': pid},
                       {'start': 10.0, 'end': 40.0, 'pattern_id': pid + 1}])
    assert _detail(app_client, pid)['addSource'] == {
        'podcastSlug': SLUG, 'episodeId': EPISODE, 'start': 50.0, 'end': 80.0, 'available': True}


def test_unavailable_when_the_original_is_not_retained(app_client, fp_feed):
    pid = _pattern(fp_feed)
    _markers(fp_feed, [{'start': 50.0, 'end': 80.0, 'pattern_id': pid}])
    assert _detail(app_client, pid)['addSource']['available'] is False


def test_earliest_marker_wins(app_client, fp_feed):
    pid = _pattern(fp_feed)
    _markers(fp_feed, [{'start': 90.0, 'end': 100.0, 'pattern_id': pid},
                       {'start': 20.0, 'end': 50.0, 'pattern_id': pid}])
    body = _detail(app_client, pid)['addSource']
    assert (body['start'], body['end'], body['available']) == (20.0, 50.0, False)


def test_adjusted_marker_uses_the_reviewer_original_span(app_client, fp_feed):
    pid = _pattern(fp_feed)
    _markers(fp_feed, [{'start': 12.0, 'end': 30.0, 'pattern_id': pid, 'reviewer_verdict': 'adjust',
                        'reviewer_original_start': 5.0, 'reviewer_original_end': 35.0}])
    body = _detail(app_client, pid)['addSource']
    assert (body['start'], body['end']) == (5.0, 35.0)


def test_null_when_the_pattern_has_audio(app_client, fp_feed, fp_audio_pattern):
    _markers(fp_feed, [{'start': 12.0, 'end': 30.0, 'pattern_id': fp_audio_pattern}])
    assert _detail(app_client, fp_audio_pattern)['addSource'] is None


def test_null_without_origin_episode_or_marker(app_client, fp_feed):
    no_origin = _pattern(fp_feed, created_from_episode_id=None)
    no_marker = _pattern(fp_feed)
    _markers(fp_feed, [{'start': 12.0, 'end': 30.0, 'pattern_id': no_origin}])
    assert _detail(app_client, no_origin)['addSource'] is None
    assert _detail(app_client, no_marker)['addSource'] is None


def test_null_for_a_pattern_without_a_podcast(app_client, fp_feed):
    pid = _pattern(fp_feed, scope='global', podcast_id=None)
    _markers(fp_feed, [{'start': 12.0, 'end': 30.0, 'pattern_id': pid}])
    assert _detail(app_client, pid)['addSource'] is None


OTHER = 'b2c3d4e5f6a1'


def _other_episode(db, processed_at, *, markers, pid, retained=True, correction='confirm', episode=OTHER):
    """A second episode of the feed holding the pattern's marker and a correction of it."""
    db.upsert_episode(slug=SLUG, episode_id=episode, original_url='https://example.com/o.mp3',
                      title='Episode Two', original_duration=300.0, status='processed')
    conn = db.get_connection()
    conn.execute("UPDATE episodes SET processed_at = ?, original_file = ? WHERE episode_id = ?",
                 (processed_at, 'o.mp3' if retained else None, episode))
    conn.commit()
    db.save_episode_details(SLUG, episode, ad_markers=markers)
    db.create_pattern_correction(correction, pattern_id=pid, episode_id=episode,
                                 podcast_id=db.get_podcast_by_slug(SLUG)['id'])


@pytest.fixture
def other_original(fp_feed):
    from api import get_storage
    path = get_storage().get_original_path(SLUG, OTHER)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'ID3')
    yield path
    path.unlink(missing_ok=True)


def test_widens_to_another_retained_episode_with_a_marker(app_client, fp_feed, other_original):
    pid = _pattern(fp_feed)
    _other_episode(fp_feed, '2026-02-01T00:00:00Z', pid=pid,
                   markers=[{'start': 70.0, 'end': 95.0, 'pattern_id': pid}])
    assert _detail(app_client, pid)['addSource'] == {
        'podcastSlug': SLUG, 'episodeId': OTHER, 'start': 70.0, 'end': 95.0, 'available': True}


def test_widens_for_a_pattern_without_a_podcast(app_client, fp_feed, other_original):
    pid = _pattern(fp_feed, scope='global', podcast_id=None, created_from_episode_id=None)
    _other_episode(fp_feed, '2026-02-01T00:00:00Z', pid=pid,
                   markers=[{'start': 70.0, 'end': 95.0, 'pattern_id': pid}])
    assert _detail(app_client, pid)['addSource']['episodeId'] == OTHER


def test_retained_origin_is_preferred(app_client, fp_feed, fp_original, other_original):
    pid = _pattern(fp_feed)
    _markers(fp_feed, [{'start': 50.0, 'end': 80.0, 'pattern_id': pid}])
    _other_episode(fp_feed, '2026-02-01T00:00:00Z', pid=pid,
                   markers=[{'start': 70.0, 'end': 95.0, 'pattern_id': pid}])
    assert _detail(app_client, pid)['addSource']['episodeId'] == EPISODE


def test_retained_episode_beats_an_expired_origin(app_client, fp_feed, other_original):
    pid = _pattern(fp_feed)
    _markers(fp_feed, [{'start': 50.0, 'end': 80.0, 'pattern_id': pid}])
    _other_episode(fp_feed, '2026-02-01T00:00:00Z', pid=pid,
                   markers=[{'start': 70.0, 'end': 95.0, 'pattern_id': pid}])
    body = _detail(app_client, pid)['addSource']
    assert (body['episodeId'], body['available']) == (OTHER, True)


@pytest.mark.parametrize('kw, marker_offset', [
    ({}, 1),
    ({'correction': 'false_positive'}, 0),
    ({'retained': False}, 0),
])
def test_skips_other_markers_expired_originals_and_false_positives(
        app_client, fp_feed, other_original, kw, marker_offset):
    pid = _pattern(fp_feed, created_from_episode_id=None)
    _other_episode(fp_feed, '2026-02-01T00:00:00Z', pid=pid, **kw,
                   markers=[{'start': 1.0, 'end': 9.0, 'pattern_id': pid + marker_offset}])
    assert _detail(app_client, pid)['addSource'] is None


def test_checks_only_the_newest_episodes(app_client, fp_feed):
    pid = _pattern(fp_feed, created_from_episode_id=None)
    for n in range(7):
        _other_episode(fp_feed, f'2026-02-0{n + 1}T00:00:00Z', pid=pid, episode=f'c{n}d4e5f6a1b2',
                       markers=[{'start': 1.0, 'end': 9.0, 'pattern_id': pid}] if n == 0 else [])
    found = fp_feed.get_pattern_correction_episodes(pid, 5)
    assert [e for _, e in found] == [f'c{n}d4e5f6a1b2' for n in (6, 5, 4, 3, 2)]


def test_correction_route_uses_the_pattern_index(fp_feed):
    from database.patterns import CORRECTION_EPISODES_SQL
    plan = ' '.join(row[3] for row in fp_feed.get_connection().execute(
        'EXPLAIN QUERY PLAN ' + CORRECTION_EPISODES_SQL, (1, 5)))
    assert 'idx_corrections_pattern' in plan


def test_rejected_and_uncut_markers_are_not_offered(app_client, fp_feed, other_original):
    pid = _pattern(fp_feed, created_from_episode_id=None)
    _other_episode(fp_feed, '2026-02-01T00:00:00Z', pid=pid, markers=[
        {'start': 1.0, 'end': 9.0, 'pattern_id': pid, 'validation': {'decision': 'REJECT'}},
        {'start': 11.0, 'end': 19.0, 'pattern_id': pid, 'was_cut': False}])
    assert _detail(app_client, pid)['addSource'] is None


def test_rejected_marker_in_the_origin_episode_is_not_offered(app_client, fp_feed, fp_original):
    pid = _pattern(fp_feed)
    _markers(fp_feed, [{'start': 50.0, 'end': 80.0, 'pattern_id': pid, 'validation': {'decision': 'REJECT'}}])
    assert _detail(app_client, pid)['addSource'] is None


def test_held_marker_is_offered(app_client, fp_feed, fp_original):
    pid = _pattern(fp_feed)
    _markers(fp_feed, [{'start': 50.0, 'end': 80.0, 'pattern_id': pid, 'held_for_review': True, 'was_cut': False}])
    assert _detail(app_client, pid)['addSource']['start'] == 50.0


def test_episode_with_a_false_positive_for_the_pattern_is_excluded(app_client, fp_feed, other_original):
    pid = _pattern(fp_feed, created_from_episode_id=None)
    _other_episode(fp_feed, '2026-02-01T00:00:00Z', pid=pid,
                   markers=[{'start': 1.0, 'end': 9.0, 'pattern_id': pid}])
    fp_feed.create_pattern_correction('false_positive', pattern_id=pid, episode_id=OTHER,
                                      podcast_id=fp_feed.get_podcast_by_slug(SLUG)['id'])
    assert _detail(app_client, pid)['addSource'] is None


def test_legacy_correction_without_a_podcast_id_still_finds_its_episode(app_client, fp_feed, other_original):
    pid = _pattern(fp_feed, created_from_episode_id=None)
    _other_episode(fp_feed, '2026-02-01T00:00:00Z', pid=pid,
                   markers=[{'start': 1.0, 'end': 9.0, 'pattern_id': pid}])
    conn = fp_feed.get_connection()
    conn.execute("UPDATE pattern_corrections SET podcast_id = NULL WHERE pattern_id = ?", (pid,))
    conn.commit()
    assert _detail(app_client, pid)['addSource']['episodeId'] == OTHER
