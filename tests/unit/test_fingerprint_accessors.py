"""Fingerprint row accessors: source span, enabled flag, scope query, audio-removed."""
import pytest


@pytest.fixture
def pattern(temp_db):
    sid = temp_db.create_known_sponsor('Acme', segment_category='sponsor')
    return temp_db.create_ad_pattern(scope='podcast', text_template='acme read ' * 12,
                                     sponsor_id=sid, podcast_id='example-podcast')


def test_create_stores_source_and_defaults_enabled(temp_db, pattern):
    temp_db.create_audio_fingerprint(pattern, b'AQAA', 12.0, 'example-podcast', 'ep1', 3.0, 15.0)
    fp = temp_db.get_audio_fingerprint(pattern)
    assert (fp['source_podcast_slug'], fp['source_episode_id'],
            fp['source_start'], fp['source_end']) == ('example-podcast', 'ep1', 3.0, 15.0)
    assert fp['enabled'] == 1


def test_positional_three_argument_call_still_works(temp_db, pattern):
    temp_db.create_audio_fingerprint(pattern, b'AQAA', 12.0)
    fp = temp_db.get_audio_fingerprint(pattern)
    assert fp['enabled'] == 1 and fp['source_episode_id'] is None


def test_explicit_enabled_false_is_written_on_insert(temp_db, pattern):
    temp_db.create_audio_fingerprint(pattern, b'AQAA', 12.0, enabled=False)
    assert temp_db.get_audio_fingerprint(pattern)['enabled'] == 0


def test_rebuild_keeps_enabled_off_when_enabled_is_none(temp_db, pattern):
    temp_db.create_audio_fingerprint(pattern, b'one', 12.0)
    temp_db.set_fingerprint_enabled(pattern, False)
    temp_db.create_audio_fingerprint(pattern, b'two', 13.0, 'example-podcast', 'ep2', 1.0, 2.0)
    fp = temp_db.get_audio_fingerprint(pattern)
    assert fp['enabled'] == 0
    assert fp['fingerprint'] == b'two' and fp['duration'] == 13.0


def test_explicit_enabled_overwrites_on_conflict(temp_db, pattern):
    temp_db.create_audio_fingerprint(pattern, b'one', 12.0)
    temp_db.set_fingerprint_enabled(pattern, False)
    temp_db.create_audio_fingerprint(pattern, b'two', 13.0, enabled=True)
    assert temp_db.get_audio_fingerprint(pattern)['enabled'] == 1


def test_conflict_replaces_source_columns(temp_db, pattern):
    temp_db.create_audio_fingerprint(pattern, b'one', 12.0, 'example-podcast', 'ep1', 3.0, 15.0)
    temp_db.create_audio_fingerprint(pattern, b'two', 12.0)
    fp = temp_db.get_audio_fingerprint(pattern)
    assert fp['source_podcast_slug'] is None and fp['source_episode_id'] is None
    assert fp['source_start'] is None and fp['source_end'] is None


def test_set_enabled_reports_whether_a_row_changed(temp_db, pattern):
    assert temp_db.set_fingerprint_enabled(pattern, False) is False
    temp_db.create_audio_fingerprint(pattern, b'AQAA', 12.0)
    assert temp_db.set_fingerprint_enabled(pattern, False) is True


def test_scope_query_filters_disabled_and_inactive(temp_db, pattern):
    temp_db.create_audio_fingerprint(pattern, b'AQAA', 12.0)
    assert [r['pattern_id'] for r in temp_db.get_active_fingerprints_with_scope()] == [pattern]
    temp_db.set_fingerprint_enabled(pattern, False)
    assert temp_db.get_active_fingerprints_with_scope() == []
    temp_db.set_fingerprint_enabled(pattern, True)
    temp_db.update_ad_pattern(pattern, is_active=0)
    assert temp_db.get_active_fingerprints_with_scope() == []


def test_scope_query_row_shape(temp_db, pattern):
    temp_db.create_audio_fingerprint(pattern, b'AQAA', 12.0)
    row = temp_db.get_active_fingerprints_with_scope()[0]
    assert {'pattern_id', 'fingerprint', 'duration', 'sponsor', 'category', 'scope',
            'network_id', 'podcast_id', 'source', 'sponsor_id', 'sponsor_tags'} <= set(row)
    assert row['sponsor'] == 'Acme' and row['scope'] == 'podcast'
    assert row['podcast_id'] == 'example-podcast' and row['sponsor_tags'] == []


def test_scope_query_parses_sponsor_tags(temp_db, pattern):
    row = temp_db.get_ad_pattern_by_id(pattern)
    conn = temp_db.get_connection()
    conn.execute("UPDATE known_sponsors SET tags = ? WHERE id = ?",
                 ('["dai", "network"]', row['sponsor_id']))
    conn.commit()
    temp_db.create_audio_fingerprint(pattern, b'AQAA', 12.0)
    assert temp_db.get_active_fingerprints_with_scope()[0]['sponsor_tags'] == ['dai', 'network']


def test_scope_query_without_sponsor_has_empty_tags(temp_db):
    pid = temp_db.create_ad_pattern(scope='global', text_template='generic read ' * 12)
    temp_db.create_audio_fingerprint(pid, b'AQAA', 12.0)
    row = temp_db.get_active_fingerprints_with_scope()[0]
    assert row['sponsor_tags'] == [] and row['sponsor'] is None


def test_audio_removed_flag(temp_db, pattern):
    assert temp_db.get_ad_pattern_by_id(pattern)['audio_removed'] == 0
    temp_db.set_pattern_audio_removed(pattern, True)
    assert temp_db.get_ad_pattern_by_id(pattern)['audio_removed'] == 1
    temp_db.set_pattern_audio_removed(pattern, False)
    assert temp_db.get_ad_pattern_by_id(pattern)['audio_removed'] == 0


@pytest.mark.parametrize('raw', ['null', '"tag"', '{"a": 1}', '5', 'not json'])
def test_scope_query_sponsor_tags_is_always_a_list(temp_db, pattern, raw):
    temp_db.create_audio_fingerprint(pattern, b'AQAA', 12.0)
    conn = temp_db.get_connection()
    conn.execute("UPDATE known_sponsors SET tags = ?", (raw,))
    conn.commit()
    assert temp_db.get_active_fingerprints_with_scope()[0]['sponsor_tags'] == []


def test_audio_states_match_the_summaries_and_skip_missing_patterns(temp_db, pattern):
    bare = temp_db.create_ad_pattern(scope='global', text_template='bare read ' * 12)
    off = temp_db.create_ad_pattern(scope='global', text_template='off read ' * 12)
    temp_db.create_audio_fingerprint(pattern, b'AQAA', 12.0)
    temp_db.create_audio_fingerprint(off, b'AQAA', 12.0)
    temp_db.set_fingerprint_enabled(off, False)
    ids = [pattern, bare, off, 987654]
    states = temp_db.get_audio_states(ids)
    assert states == {pattern: 'active', bare: 'none', off: 'off'}
    assert states == {pid: s['state'] for pid, s in temp_db.get_audio_summaries(ids).items()}
    assert temp_db.get_audio_states([]) == {}


def test_fingerprint_without_blob_keeps_every_other_column(temp_db, pattern):
    temp_db.create_audio_fingerprint(pattern, b'AQAA', 12.0)
    full = temp_db.get_audio_fingerprint(pattern)
    meta = temp_db.get_audio_fingerprint(pattern, with_blob=False)
    assert 'fingerprint' not in meta
    assert meta == {k: v for k, v in full.items() if k != 'fingerprint'}
