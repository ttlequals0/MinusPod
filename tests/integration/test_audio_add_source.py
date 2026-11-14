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


def test_global_pattern_origin_resolves_its_feed_through_the_episode(app_client, fp_feed, fp_original):
    pid = _pattern(fp_feed, scope='global', podcast_id=None)
    _markers(fp_feed, [{'start': 12.0, 'end': 30.0, 'pattern_id': pid}])
    assert _detail(app_client, pid)['addSource'] == {
        'podcastSlug': SLUG, 'episodeId': EPISODE, 'start': 12.0, 'end': 30.0, 'available': True}


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


def _feed_episode(db, slug, episode, processed_at, markers, *, network=None, retained=True, on_disk=True):
    """An episode of another feed that holds markers; ids may repeat across feeds."""
    from api import get_storage
    if db.get_podcast_by_slug(slug) is None:
        db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    if network:
        db.get_connection().execute("UPDATE podcasts SET network_id = ? WHERE slug = ?", (network, slug))
    db.upsert_episode(slug=slug, episode_id=episode, original_url='https://example.com/x.mp3',
                      title='Episode', original_duration=300.0, status='processed')
    conn = db.get_connection()
    conn.execute("UPDATE episodes SET processed_at = ?, original_file = ? WHERE episode_id = ? AND podcast_id = "
                 "(SELECT id FROM podcasts WHERE slug = ?)", (processed_at, 'x.mp3' if retained else None, episode, slug))
    conn.commit()
    db.save_episode_details(slug, episode, ad_markers=markers)
    path = get_storage().get_original_path(slug, episode)
    if retained and on_disk:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'ID3')
    return path


@pytest.fixture(autouse=True)
def drop_unscoped_patterns(fp_feed):
    """Global and network patterns outlive the feed, so remove them for the next test file."""
    yield
    conn = fp_feed.get_connection()
    conn.execute("DELETE FROM ad_patterns WHERE scope IN ('global', 'network') AND text_template = ?", (TEMPLATE,))
    conn.commit()


@pytest.fixture
def cleanup_paths(fp_feed):
    from api import get_storage
    paths = []
    yield paths
    for slug, episode in paths:
        get_storage().get_original_path(slug, episode).unlink(missing_ok=True)
    for slug in ('other-podcast', 'net-podcast'):
        if fp_feed.get_podcast_by_slug(slug):
            fp_feed.delete_podcast(slug)


def test_text_match_marker_without_corrections_is_offered(app_client, fp_feed, cleanup_paths):
    pid = _pattern(fp_feed, scope='global', podcast_id=None, created_from_episode_id=None)
    cleanup_paths.append((SLUG, OTHER))
    _feed_episode(fp_feed, SLUG, OTHER, '2026-02-01T00:00:00Z', [{'start': 70.0, 'end': 95.0, 'pattern_id': pid}])
    assert _detail(app_client, pid)['addSource'] == {
        'podcastSlug': SLUG, 'episodeId': OTHER, 'start': 70.0, 'end': 95.0, 'available': True}


def test_global_pattern_uses_a_marker_in_any_feed_newest_first(app_client, fp_feed, cleanup_paths):
    pid = _pattern(fp_feed, scope='global', podcast_id=None, created_from_episode_id=None)
    cleanup_paths.extend([(SLUG, OTHER), ('other-podcast', 'c3d4e5f6a1b2')])
    marker = [{'start': 70.0, 'end': 95.0, 'pattern_id': pid}]
    _feed_episode(fp_feed, SLUG, OTHER, '2026-02-01T00:00:00Z', marker)
    _feed_episode(fp_feed, 'other-podcast', 'c3d4e5f6a1b2', '2026-03-01T00:00:00Z', marker)
    body = _detail(app_client, pid)['addSource']
    assert (body['podcastSlug'], body['episodeId']) == ('other-podcast', 'c3d4e5f6a1b2')


def test_podcast_scope_ignores_markers_of_other_feeds(app_client, fp_feed, cleanup_paths):
    pid = _pattern(fp_feed, created_from_episode_id=None)
    cleanup_paths.append(('other-podcast', 'c3d4e5f6a1b2'))
    _feed_episode(fp_feed, 'other-podcast', 'c3d4e5f6a1b2', '2026-02-01T00:00:00Z',
                  [{'start': 70.0, 'end': 95.0, 'pattern_id': pid}])
    assert _detail(app_client, pid)['addSource'] is None


def test_network_scope_uses_only_feeds_of_its_network(app_client, fp_feed, cleanup_paths):
    pid = _pattern(fp_feed, scope='network', podcast_id=None, network_id='Acme Network', created_from_episode_id=None)
    cleanup_paths.extend([('other-podcast', 'c3d4e5f6a1b2'), ('net-podcast', 'd4e5f6a1b2c3')])
    marker = [{'start': 70.0, 'end': 95.0, 'pattern_id': pid}]
    _feed_episode(fp_feed, 'net-podcast', 'd4e5f6a1b2c3', '2026-02-01T00:00:00Z', marker, network='Acme Network')
    _feed_episode(fp_feed, 'other-podcast', 'c3d4e5f6a1b2', '2026-03-01T00:00:00Z', marker, network='Beta Network')
    assert _detail(app_client, pid)['addSource']['podcastSlug'] == 'net-podcast'


def test_marker_episode_with_a_rejected_marker_or_false_positive_is_skipped(app_client, fp_feed, cleanup_paths):
    pid = _pattern(fp_feed, scope='global', podcast_id=None, created_from_episode_id=None)
    cleanup_paths.extend([(SLUG, OTHER), ('other-podcast', 'c3d4e5f6a1b2')])
    _feed_episode(fp_feed, SLUG, OTHER, '2026-02-01T00:00:00Z',
                  [{'start': 70.0, 'end': 95.0, 'pattern_id': pid, 'validation': {'decision': 'REJECT'}}])
    _feed_episode(fp_feed, 'other-podcast', 'c3d4e5f6a1b2', '2026-03-01T00:00:00Z',
                  [{'start': 70.0, 'end': 95.0, 'pattern_id': pid}])
    fp_feed.create_pattern_correction('false_positive', pattern_id=pid, episode_id='c3d4e5f6a1b2',
                                      podcast_id=fp_feed.get_podcast_by_slug('other-podcast')['id'])
    assert _detail(app_client, pid)['addSource'] is None


def test_only_an_expired_marker_episode_is_offered_unavailable(app_client, fp_feed, cleanup_paths):
    pid = _pattern(fp_feed, scope='global', podcast_id=None, created_from_episode_id=None)
    _feed_episode(fp_feed, SLUG, OTHER, '2026-02-01T00:00:00Z',
                  [{'start': 70.0, 'end': 95.0, 'pattern_id': pid}], on_disk=False)
    body = _detail(app_client, pid)['addSource']
    assert (body['episodeId'], body['available']) == (OTHER, False)


def test_marker_search_opens_at_most_the_cap(app_client, fp_feed, cleanup_paths):
    from api.patterns_audio import ADD_SOURCE_EPISODES
    pid = _pattern(fp_feed, scope='global', podcast_id=None, created_from_episode_id=None)
    rejected = [{'start': 1.0, 'end': 9.0, 'pattern_id': pid, 'validation': {'decision': 'REJECT'}}]
    for n in range(ADD_SOURCE_EPISODES):
        cleanup_paths.append((SLUG, f'e{n}d4e5f6a1b2'))
        _feed_episode(fp_feed, SLUG, f'e{n}d4e5f6a1b2', f'2026-03-0{n + 2}T00:00:00Z', rejected)
    cleanup_paths.append((SLUG, OTHER))
    _feed_episode(fp_feed, SLUG, OTHER, '2026-02-01T00:00:00Z', [{'start': 70.0, 'end': 95.0, 'pattern_id': pid}])
    pattern = fp_feed.get_ad_pattern_by_id(pid)
    assert len(fp_feed.get_pattern_marker_episodes(pattern, ADD_SOURCE_EPISODES)) == ADD_SOURCE_EPISODES
    assert _detail(app_client, pid)['addSource'] is None


def test_marker_prefilter_does_not_match_a_longer_pattern_id(fp_feed, cleanup_paths):
    pid = _pattern(fp_feed, scope='global', podcast_id=None, created_from_episode_id=None)
    cleanup_paths.append((SLUG, OTHER))
    _feed_episode(fp_feed, SLUG, OTHER, '2026-02-01T00:00:00Z',
                  [{'start': 70.0, 'end': 95.0, 'pattern_id': pid * 10 + 5}])
    assert fp_feed.get_pattern_marker_episodes(fp_feed.get_ad_pattern_by_id(pid), 5) == []


def test_origin_episode_id_shared_by_two_feeds_picks_the_one_holding_the_marker(app_client, fp_feed, cleanup_paths):
    pid = _pattern(fp_feed, scope='global', podcast_id=None)
    cleanup_paths.append(('other-podcast', EPISODE))
    _feed_episode(fp_feed, SLUG, EPISODE, '2026-02-01T00:00:00Z', [], retained=False)
    _feed_episode(fp_feed, 'other-podcast', EPISODE, '2026-01-01T00:00:00Z',
                  [{'start': 70.0, 'end': 95.0, 'pattern_id': pid}])
    body = _detail(app_client, pid)['addSource']
    assert (body['podcastSlug'], body['episodeId'], body['available']) == ('other-podcast', EPISODE, True)


def test_rebuild_route_accepts_a_text_match_marker_span(app_client, fp_feed, cleanup_paths, monkeypatch):
    from audio_fingerprinter import AudioFingerprint, AudioFingerprinter
    monkeypatch.setattr(AudioFingerprinter, '_find_fpcalc', lambda self: '/usr/bin/fpcalc')
    monkeypatch.setattr(AudioFingerprinter, 'generate_fingerprint',
                        lambda self, path, start=0, duration=None: AudioFingerprint(fingerprint='AQAA1', duration=duration))
    pid = _pattern(fp_feed, scope='global', podcast_id=None, created_from_episode_id=None)
    cleanup_paths.append(('other-podcast', 'c3d4e5f6a1b2'))
    _feed_episode(fp_feed, 'other-podcast', 'c3d4e5f6a1b2', '2026-02-01T00:00:00Z',
                  [{'start': 70.0, 'end': 95.0, 'pattern_id': pid}])
    src = _detail(app_client, pid)['addSource']
    resp = app_client.post(f'/api/v1/patterns/{pid}/audio/rebuild', headers=csrf(app_client), json={
        k: src[k] for k in ('podcastSlug', 'episodeId', 'start', 'end')})
    assert resp.status_code == 200 and resp.get_json()['fingerprintStored'] is True


def _break(pid, members, **kw):
    """A merged break led by another pattern, with members naming this one."""
    return {'start': 100.0, 'end': 300.0, 'pattern_id': pid + 1000, 'was_cut': True,
            'merged_member_spans': members, **kw}


def _member(pid, start, end, stage='text_pattern'):
    return {'start': start, 'end': end, 'stage': stage, 'pattern_id': pid}


def test_member_of_a_merged_marker_gives_its_own_span(app_client, fp_feed, fp_original):
    pid = _pattern(fp_feed)
    _markers(fp_feed, [_break(pid, [_member(pid, 150.0, 200.0)])])
    assert _detail(app_client, pid)['addSource'] == {
        'podcastSlug': SLUG, 'episodeId': EPISODE, 'start': 150.0, 'end': 200.0, 'available': True}


@pytest.mark.parametrize('extra', [{'validation': {'decision': 'REJECT'}}, {'was_cut': False}])
def test_member_of_a_rejected_or_uncut_parent_is_not_offered(app_client, fp_feed, fp_original, extra):
    pid = _pattern(fp_feed)
    _markers(fp_feed, [_break(pid, [_member(pid, 150.0, 200.0)], **extra)])
    assert _detail(app_client, pid)['addSource'] is None


def test_member_is_clipped_to_the_displayed_span_of_an_adjusted_parent(app_client, fp_feed, fp_original):
    pid = _pattern(fp_feed)
    _markers(fp_feed, [_break(pid, [_member(pid, 80.0, 150.0)], reviewer_verdict='adjust',
                              reviewer_original_start=100.0, reviewer_original_end=250.0)])
    body = _detail(app_client, pid)['addSource']
    assert (body['start'], body['end']) == (100.0, 150.0)


def test_member_outside_the_displayed_span_is_skipped(app_client, fp_feed, fp_original):
    pid = _pattern(fp_feed)
    _markers(fp_feed, [_break(pid, [_member(pid, 310.0, 350.0)])])
    assert _detail(app_client, pid)['addSource'] is None


def test_earliest_of_a_top_level_marker_and_a_member_wins(app_client, fp_feed, fp_original):
    pid = _pattern(fp_feed)
    _markers(fp_feed, [{'start': 400.0, 'end': 430.0, 'pattern_id': pid},
                       _break(pid, [_member(pid, 150.0, 200.0, 'fingerprint')])])
    assert _detail(app_client, pid)['addSource']['start'] == 150.0


def test_member_without_a_pattern_id_is_ignored(app_client, fp_feed, fp_original):
    pid = _pattern(fp_feed)
    _markers(fp_feed, [_break(pid, [{'start': 150.0, 'end': 200.0, 'stage': 'claude'}])])
    assert _detail(app_client, pid)['addSource'] is None


def test_rebuild_from_a_member_span_records_that_source(app_client, fp_feed, fp_original, stub_fingerprinting):
    pid = _pattern(fp_feed)
    _markers(fp_feed, [_break(pid, [_member(pid, 150.0, 200.0)])])
    src = _detail(app_client, pid)['addSource']
    resp = app_client.post(f'/api/v1/patterns/{pid}/audio/rebuild', headers=csrf(app_client),
                           json={k: src[k] for k in ('podcastSlug', 'episodeId', 'start', 'end')})
    assert resp.status_code == 200 and resp.get_json()['fingerprintStored'] is True
    row = fp_feed.get_audio_fingerprint(pid, with_blob=False)
    assert (row['source_episode_id'], row['source_start'], row['source_end']) == (EPISODE, 150.0, 200.0)
