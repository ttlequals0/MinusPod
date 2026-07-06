"""Unit tests for the cue_cross_episode_scans DB state machine (D1b, #350).

Mirrors test_database.py's test_cue_candidate_scan_state_machine /
test_cue_candidate_scan_error_and_staleness but for the new family keyed by
(podcast_id, episode_set_hash).
"""
import json



def test_claim_starts_scan(temp_db):
    pid = temp_db.create_podcast('xep-feed-a', 'http://x/a.xml', 'A')
    h = 'aabbccdd' * 8  # 64-char hex hash
    assert temp_db.claim_cue_cross_episode_scan(pid, h, 900) == 'started'


def test_double_claim_returns_scanning(temp_db):
    pid = temp_db.create_podcast('xep-feed-b', 'http://x/b.xml', 'B')
    h = 'aabbccdd' * 8
    temp_db.claim_cue_cross_episode_scan(pid, h, 900)
    assert temp_db.claim_cue_cross_episode_scan(pid, h, 900) == 'scanning'


def test_save_result_marks_ready(temp_db):
    pid = temp_db.create_podcast('xep-feed-c', 'http://x/c.xml', 'C')
    h = 'aabbccdd' * 8
    temp_db.claim_cue_cross_episode_scan(pid, h, 900)
    payload = {
        'candidates': [{'start': 1.0, 'end': 2.5, 'kind': 'recurring', 'episodeMatches': 3}],
        'targetEpisodeId': 'ep-target',
        'episodeIds': ['ep-target', 'ep-b'],
    }
    temp_db.save_cue_cross_episode_scan_result(pid, h, payload)
    row = temp_db.get_cue_cross_episode_scan(pid, h)
    assert row['status'] == 'ready'
    stored = json.loads(row['result_json'])
    assert stored['candidates'][0]['start'] == 1.0
    assert stored['targetEpisodeId'] == 'ep-target'


def test_ready_claim_without_force_returns_ready(temp_db):
    pid = temp_db.create_podcast('xep-feed-d', 'http://x/d.xml', 'D')
    h = 'aabbccdd' * 8
    temp_db.claim_cue_cross_episode_scan(pid, h, 900)
    temp_db.save_cue_cross_episode_scan_result(pid, h, {'candidates': []})
    assert temp_db.claim_cue_cross_episode_scan(pid, h, 900) == 'ready'


def test_force_reclaims_ready(temp_db):
    pid = temp_db.create_podcast('xep-feed-e', 'http://x/e.xml', 'E')
    h = 'aabbccdd' * 8
    temp_db.claim_cue_cross_episode_scan(pid, h, 900)
    temp_db.save_cue_cross_episode_scan_result(pid, h, {'candidates': []})
    assert temp_db.claim_cue_cross_episode_scan(pid, h, 900, force=True) == 'started'


def test_save_error_marks_error(temp_db):
    pid = temp_db.create_podcast('xep-feed-f', 'http://x/f.xml', 'F')
    h = 'aabbccdd' * 8
    temp_db.claim_cue_cross_episode_scan(pid, h, 900)
    temp_db.save_cue_cross_episode_scan_error(pid, h, 'decode failed')
    row = temp_db.get_cue_cross_episode_scan(pid, h)
    assert row['status'] == 'error'
    assert row['error'] == 'decode failed'


def test_fresh_error_is_not_reclaimed(temp_db):
    pid = temp_db.create_podcast('xep-feed-g', 'http://x/g.xml', 'G')
    h = 'aabbccdd' * 8
    temp_db.claim_cue_cross_episode_scan(pid, h, 900)
    temp_db.save_cue_cross_episode_scan_error(pid, h, 'boom')
    assert temp_db.claim_cue_cross_episode_scan(pid, h, 900) == 'error'


def test_force_reclaims_error(temp_db):
    pid = temp_db.create_podcast('xep-feed-h', 'http://x/h.xml', 'H')
    h = 'aabbccdd' * 8
    temp_db.claim_cue_cross_episode_scan(pid, h, 900)
    temp_db.save_cue_cross_episode_scan_error(pid, h, 'boom')
    assert temp_db.claim_cue_cross_episode_scan(pid, h, 900, force=True) == 'started'


def test_stale_scanning_is_reclaimable(temp_db):
    pid = temp_db.create_podcast('xep-feed-i', 'http://x/i.xml', 'I')
    h = 'aabbccdd' * 8
    temp_db.claim_cue_cross_episode_scan(pid, h, 900)
    # stale_seconds=0 means the row is always stale
    assert temp_db.claim_cue_cross_episode_scan(pid, h, 0) == 'started'


def test_get_returns_none_when_absent(temp_db):
    pid = temp_db.create_podcast('xep-feed-j', 'http://x/j.xml', 'J')
    assert temp_db.get_cue_cross_episode_scan(pid, 'nonexistent-hash') is None


def test_different_hashes_are_independent(temp_db):
    pid = temp_db.create_podcast('xep-feed-k', 'http://x/k.xml', 'K')
    h1 = 'aa' * 32
    h2 = 'bb' * 32
    temp_db.claim_cue_cross_episode_scan(pid, h1, 900)
    temp_db.save_cue_cross_episode_scan_result(pid, h1, {'candidates': [{'start': 1.0}]})
    # h2 has no row yet
    assert temp_db.get_cue_cross_episode_scan(pid, h2) is None
    assert temp_db.claim_cue_cross_episode_scan(pid, h2, 900) == 'started'
