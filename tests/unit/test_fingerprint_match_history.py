"""Audio fingerprint match history accessors."""
import pytest


@pytest.fixture
def pattern(temp_db):
    return temp_db.create_ad_pattern(scope='podcast', text_template='acme read ' * 12,
                                     podcast_id='show')


def _record(db, pid, ep='ep1', slug='show', start=10.0, end=55.0, **kw):
    args = dict(similarity=0.9, coverage=0.8, in_scope=True, audio_only=False)
    args.update(kw)
    return db.record_fingerprint_match(pid, ep, slug, start, end, **args)


def test_record_paginate_and_summary(temp_db, pattern):
    for i in range(12):
        _record(temp_db, pattern, f'ep{i}', start=10.0 + i, end=55.0 + i,
                in_scope=(i % 2 == 0), audio_only=(i == 0))
    assert temp_db.count_fingerprint_matches(pattern) == 12
    assert len(temp_db.get_fingerprint_matches(pattern, limit=10, offset=0)) == 10
    assert len(temp_db.get_fingerprint_matches(pattern, limit=10, offset=10)) == 2
    summary = temp_db.get_audio_match_summary(pattern)
    assert summary['match_count'] == 12 and summary['out_of_scope_count'] == 6
    assert summary['rejected_count'] == 0
    assert abs(summary['mean_similarity'] - 0.9) < 1e-9
    assert summary['last_matched_at']


def test_pages_are_disjoint_and_complete(temp_db, pattern):
    made = {_record(temp_db, pattern, f'ep{i}') for i in range(25)}
    seen = []
    for offset in (0, 10, 20):
        seen += [m['id'] for m in temp_db.get_fingerprint_matches(pattern, 10, offset)]
    assert len(seen) == len(set(seen)) == 25 and set(seen) == made


def test_summary_excludes_rejected_rows_from_count_and_mean(temp_db, pattern):
    _record(temp_db, pattern, 'a', start=10, end=50, similarity=0.9)
    _record(temp_db, pattern, 'b', start=10, end=50, similarity=0.5)
    _record(temp_db, pattern, 'c', start=10, end=50, similarity=0.7, in_scope=False)
    assert temp_db.mark_fingerprint_match_rejected(pattern, 'show', 'b', 10, 50)
    summary = temp_db.get_audio_match_summary(pattern)
    assert summary['match_count'] == 2 and summary['rejected_count'] == 1
    assert summary['out_of_scope_count'] == 1
    assert abs(summary['mean_similarity'] - 0.8) < 1e-9


def test_out_of_scope_count_skips_rejected_rows(temp_db, pattern):
    _record(temp_db, pattern, 'a', start=10, end=50, in_scope=False)
    _record(temp_db, pattern, 'b', start=10, end=50, in_scope=False)
    assert temp_db.mark_fingerprint_match_rejected(pattern, 'show', 'b', 10, 50)
    summary = temp_db.get_audio_match_summary(pattern)
    assert (summary['match_count'], summary['rejected_count'], summary['out_of_scope_count']) == (1, 1, 1)


def test_summary_with_only_rejected_rows(temp_db, pattern):
    _record(temp_db, pattern, 'a', start=10, end=50)
    temp_db.mark_fingerprint_match_rejected(pattern, 'show', 'a', 10, 50)
    summary = temp_db.get_audio_match_summary(pattern)
    assert summary['match_count'] == 0 and summary['rejected_count'] == 1
    assert summary['mean_similarity'] is None


def test_summary_of_a_pattern_with_no_matches(temp_db, pattern):
    assert temp_db.get_audio_match_summary(pattern) == {
        'match_count': 0, 'rejected_count': 0, 'out_of_scope_count': 0,
        'mean_similarity': None, 'last_matched_at': None}


def test_newest_first(temp_db, pattern):
    # created_at has one-second resolution, so same-second rows order by id
    first = _record(temp_db, pattern, 'a')
    second = _record(temp_db, pattern, 'b')
    ids = [m['id'] for m in temp_db.get_fingerprint_matches(pattern, 10, 0)]
    assert ids == [second, first]


def test_record_stores_flags_and_get_by_id(temp_db, pattern):
    mid = _record(temp_db, pattern, in_scope=False, audio_only=True)
    row = temp_db.get_fingerprint_match(mid)
    assert (row['episode_id'], row['podcast_slug'], row['in_scope'],
            row['audio_only'], row['rejected']) == ('ep1', 'show', 0, 1, 0)
    assert temp_db.get_fingerprint_match(mid + 100) is None


def test_record_without_audio_only_stores_null(temp_db, pattern):
    mid = _record(temp_db, pattern, audio_only=None)
    assert temp_db.get_fingerprint_match(mid)['audio_only'] is None


def test_delete_for_episode_is_scoped_to_podcast(temp_db, pattern):
    _record(temp_db, pattern, 'epX', slug='show')
    _record(temp_db, pattern, 'epX', slug='other')
    assert temp_db.delete_fingerprint_matches_for_episode('show', 'epX') == 1
    rows = temp_db.get_fingerprint_matches(pattern, 10, 0)
    assert [r['podcast_slug'] for r in rows] == ['other']


def test_delete_for_episode_keep_rejected(temp_db, pattern):
    _record(temp_db, pattern, 'epX', start=1, end=20)
    _record(temp_db, pattern, 'epX', start=100, end=140)
    assert temp_db.mark_fingerprint_match_rejected(pattern, 'show', 'epX', 100, 140)
    assert temp_db.delete_fingerprint_matches_for_episode(
        'show', 'epX', keep_rejected=True) == 1
    left = temp_db.get_fingerprint_matches(pattern, 10, 0)
    assert len(left) == 1 and left[0]['rejected'] == 1
    assert temp_db.delete_fingerprint_matches_for_episode('show', 'epX') == 1
    assert temp_db.count_fingerprint_matches(pattern) == 0


def test_delete_for_pattern(temp_db, pattern):
    other = temp_db.create_ad_pattern(scope='podcast', text_template='other read ' * 12,
                                      podcast_id='show')
    _record(temp_db, pattern)
    _record(temp_db, other)
    assert temp_db.delete_fingerprint_matches_for_pattern(pattern) == 1
    assert temp_db.count_fingerprint_matches(pattern) == 0
    assert temp_db.count_fingerprint_matches(other) == 1


def test_move_matches_between_patterns(temp_db, pattern):
    target = temp_db.create_ad_pattern(scope='podcast', text_template='other read ' * 12,
                                       podcast_id='show')
    _record(temp_db, pattern, 'a')
    _record(temp_db, pattern, 'b')
    assert temp_db.move_fingerprint_matches(pattern, target) == 2
    assert temp_db.count_fingerprint_matches(pattern) == 0
    assert temp_db.count_fingerprint_matches(target) == 2


def test_mark_rejected_picks_the_overlapping_row(temp_db, pattern):
    near = _record(temp_db, pattern, 'epY', start=100, end=140)
    far = _record(temp_db, pattern, 'epY', start=300, end=340)
    assert temp_db.mark_fingerprint_match_rejected(pattern, 'show', 'epY', 305, 338) is True
    assert temp_db.get_fingerprint_match(far)['rejected'] == 1
    assert temp_db.get_fingerprint_match(far)['rejected_at']
    assert temp_db.get_fingerprint_match(near)['rejected'] == 0


def test_mark_rejected_largest_overlap_wins(temp_db, pattern):
    small = _record(temp_db, pattern, 'epY', start=100, end=120)
    big = _record(temp_db, pattern, 'epY', start=115, end=160)
    assert temp_db.mark_fingerprint_match_rejected(pattern, 'show', 'epY', 110, 150)
    assert temp_db.get_fingerprint_match(big)['rejected'] == 1
    assert temp_db.get_fingerprint_match(small)['rejected'] == 0


def test_mark_rejected_tie_goes_to_the_newest_row(temp_db, pattern):
    older = _record(temp_db, pattern, 'epY', start=100, end=140)
    newer = _record(temp_db, pattern, 'epY', start=100, end=140)
    assert temp_db.mark_fingerprint_match_rejected(pattern, 'show', 'epY', 100, 140)
    assert temp_db.get_fingerprint_match(newer)['rejected'] == 1
    assert temp_db.get_fingerprint_match(older)['rejected'] == 0


def test_mark_rejected_again_is_true_and_keeps_rejected_at(temp_db, pattern):
    mid = _record(temp_db, pattern, 'epY', start=100, end=140)
    assert temp_db.mark_fingerprint_match_rejected(pattern, 'show', 'epY', 100, 140)
    conn = temp_db.get_connection()
    conn.execute("UPDATE audio_fingerprint_matches SET rejected_at = '2020-01-01T00:00:00Z' "
                 "WHERE id = ?", (mid,))
    conn.commit()
    assert temp_db.mark_fingerprint_match_rejected(pattern, 'show', 'epY', 100, 140) is True
    row = temp_db.get_fingerprint_match(mid)
    assert row['rejected'] == 1 and row['rejected_at'] == '2020-01-01T00:00:00Z'


def test_mark_rejected_touching_edges_is_not_an_overlap(temp_db, pattern):
    mid = _record(temp_db, pattern, 'epY', start=100, end=140)
    assert temp_db.mark_fingerprint_match_rejected(pattern, 'show', 'epY', 140, 180) is False
    assert temp_db.mark_fingerprint_match_rejected(pattern, 'show', 'epY', 60, 100) is False
    assert temp_db.get_fingerprint_match(mid)['rejected'] == 0


def test_mark_rejected_without_overlap_changes_nothing(temp_db, pattern):
    mid = _record(temp_db, pattern, 'epY', start=100, end=140)
    assert temp_db.mark_fingerprint_match_rejected(pattern, 'show', 'epY', 200, 240) is False
    assert temp_db.get_fingerprint_match(mid)['rejected'] == 0


def test_mark_rejected_never_touches_another_podcast(temp_db, pattern):
    other = _record(temp_db, pattern, 'epY', slug='other', start=100, end=140)
    assert temp_db.mark_fingerprint_match_rejected(pattern, 'show', 'epY', 100, 140) is False
    assert temp_db.get_fingerprint_match(other)['rejected'] == 0


def test_mark_rejected_counts_in_summary(temp_db, pattern):
    _record(temp_db, pattern, 'epY', start=100, end=140)
    temp_db.mark_fingerprint_match_rejected(pattern, 'show', 'epY', 100, 140)
    assert temp_db.get_audio_match_summary(pattern)['rejected_count'] == 1


def test_deleting_a_pattern_removes_its_matches(temp_db, pattern):
    _record(temp_db, pattern)
    temp_db.delete_ad_pattern(pattern)
    assert temp_db.count_fingerprint_matches(pattern) == 0


def test_deleting_a_feed_removes_its_match_history(temp_db):
    temp_db.create_podcast('show', 'https://example.com/feed.xml', 'Show')
    shared = temp_db.create_ad_pattern(scope='global', text_template='acme read ' * 12)
    _record(temp_db, shared, 'epA', slug='show')
    _record(temp_db, shared, 'epB', slug='other')
    assert temp_db.delete_podcast('show')
    rows = temp_db.get_fingerprint_matches(shared, 10, 0)
    assert [r['podcast_slug'] for r in rows] == ['other']
