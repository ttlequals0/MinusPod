"""Unit tests for cue candidate dismissal CRUD (2.44.0)."""


def test_create_and_list(temp_db):
    pid = temp_db.create_podcast('feed', 'http://x/rss', 'Feed')
    did = temp_db.create_cue_candidate_dismissal(
        pid, 'ep1', 100.0, 102.5, 'Repeats 4x', '[1, 2, 3, 4]')
    assert isinstance(did, int)
    rows = temp_db.list_cue_candidate_dismissals(pid)
    assert len(rows) == 1
    r = rows[0]
    assert r['id'] == did
    assert r['source_episode_id'] == 'ep1'
    assert r['start_s'] == 100.0 and r['end_s'] == 102.5
    assert r['label'] == 'Repeats 4x'
    assert r['fingerprint'] == '[1, 2, 3, 4]'


def test_get_and_delete(temp_db):
    pid = temp_db.create_podcast('feed', 'http://x/rss', 'Feed')
    did = temp_db.create_cue_candidate_dismissal(pid, 'ep1', 1.0, 2.0, None, '[9]')
    assert temp_db.get_cue_candidate_dismissal(did)['podcast_id'] == pid
    assert temp_db.delete_cue_candidate_dismissal(did) is True
    assert temp_db.get_cue_candidate_dismissal(did) is None
    assert temp_db.delete_cue_candidate_dismissal(did) is False


def test_scoped_by_podcast(temp_db):
    p1 = temp_db.create_podcast('feed1', 'http://x/rss', 'Feed 1')
    p2 = temp_db.create_podcast('feed2', 'http://y/rss', 'Feed 2')
    temp_db.create_cue_candidate_dismissal(p1, 'ep1', 1.0, 2.0, None, '[1]')
    assert temp_db.list_cue_candidate_dismissals(p2) == []


def test_cascade_on_podcast_delete(temp_db):
    pid = temp_db.create_podcast('feed', 'http://x/rss', 'Feed')
    did = temp_db.create_cue_candidate_dismissal(pid, 'ep1', 1.0, 2.0, None, '[1]')
    temp_db.delete_podcast('feed')
    assert temp_db.get_cue_candidate_dismissal(did) is None
