"""Unit tests for _run_cue_cross_episode_scan worker (D1b, #350).

Uses a mock DB and mocked AudioFingerprinter to validate payload shape and
error path without real audio.
"""
from unittest.mock import MagicMock, patch


def _make_db(podcast_id=1, episode_set_hash='aabbccdd' * 8):
    db = MagicMock()
    db.get_connection.return_value = MagicMock()
    return db


def _run(db, podcast_id, episode_set_hash, target_id, episode_ids,
         target_path, sibling_paths, fake_candidates):
    import audio_fingerprinter as afp_module
    from api.cue_templates import _run_cue_cross_episode_scan

    with patch('api.cue_templates.get_database', return_value=db):
        with patch.object(afp_module.AudioFingerprinter, 'discover_cross_episode_body',
                          return_value=fake_candidates):
            with patch.object(afp_module.AudioFingerprinter, '_generate_full_fingerprint',
                              return_value=([0] * 400, 60.0)):
                with patch.object(afp_module.AudioFingerprinter, 'is_available',
                                  return_value=True):
                    _run_cue_cross_episode_scan(
                        podcast_id, episode_set_hash,
                        target_id, episode_ids,
                        target_path, sibling_paths,
                    )


def test_payload_shape_candidates_and_echo(tmp_path):
    """Worker saves candidates plus targetEpisodeId and episodeIds echo."""
    db = _make_db()
    fake_candidates = [
        {'start': 3.0, 'end': 5.5, 'kind': 'recurring', 'episodeMatches': 3},
    ]
    _run(db, 1, 'aa' * 32, 'ep-target', ['ep-target', 'ep-b', 'ep-c'],
         str(tmp_path / 'target.mp3'),
         [str(tmp_path / 'sib.mp3')],
         fake_candidates)

    db.save_cue_cross_episode_scan_result.assert_called_once()
    call_args = db.save_cue_cross_episode_scan_result.call_args[0]
    payload = call_args[2]
    assert payload['targetEpisodeId'] == 'ep-target'
    assert set(payload['episodeIds']) == {'ep-target', 'ep-b', 'ep-c'}
    assert payload['candidates'] == fake_candidates
    db.save_cue_cross_episode_scan_error.assert_not_called()


def test_empty_candidates_are_saved(tmp_path):
    """Empty result from fingerprinter is saved, not treated as error."""
    db = _make_db()
    _run(db, 1, 'bb' * 32, 'ep-x', ['ep-x', 'ep-y'],
         str(tmp_path / 'target.mp3'),
         [str(tmp_path / 'sib.mp3')],
         [])

    db.save_cue_cross_episode_scan_result.assert_called_once()
    payload = db.save_cue_cross_episode_scan_result.call_args[0][2]
    assert payload['candidates'] == []
    db.save_cue_cross_episode_scan_error.assert_not_called()


def test_error_path_saves_error(tmp_path):
    """When fingerprinter raises, worker persists the error."""
    import audio_fingerprinter as afp_module
    from api.cue_templates import _run_cue_cross_episode_scan

    db = _make_db()

    def _boom(*args, **kwargs):
        raise RuntimeError('fpcalc missing')

    with patch('api.cue_templates.get_database', return_value=db):
        with patch.object(afp_module.AudioFingerprinter, 'discover_cross_episode_body', _boom):
            with patch.object(afp_module.AudioFingerprinter, '_generate_full_fingerprint',
                              return_value=([0] * 400, 60.0)):
                with patch.object(afp_module.AudioFingerprinter, 'is_available',
                                  return_value=True):
                    _run_cue_cross_episode_scan(
                        1, 'cc' * 32, 'ep-err', ['ep-err', 'ep-sib'],
                        str(tmp_path / 'target.mp3'),
                        [str(tmp_path / 'sib.mp3')],
                    )

    db.save_cue_cross_episode_scan_error.assert_called_once()
    db.save_cue_cross_episode_scan_result.assert_not_called()


def test_min_matches_capped_at_sibling_count(tmp_path):
    """With a single sibling (a 2-episode set) the worker must reach the
    discovery call with min_matches=1, not the default 2 (which short-circuits
    to []). Regression guard for the always-empty 2-episode scan."""
    import audio_fingerprinter as afp_module
    from api.cue_templates import _run_cue_cross_episode_scan

    db = _make_db()
    captured = {}

    def _spy(self, target_path, sibling_paths, **kwargs):
        captured['min_matches'] = kwargs.get('min_matches')
        return []

    with patch('api.cue_templates.get_database', return_value=db):
        with patch.object(afp_module.AudioFingerprinter, 'discover_cross_episode_body', _spy):
            with patch.object(afp_module.AudioFingerprinter, '_generate_full_fingerprint',
                              return_value=([0] * 400, 60.0)):
                with patch.object(afp_module.AudioFingerprinter, 'is_available',
                                  return_value=True):
                    _run_cue_cross_episode_scan(
                        1, 'ee' * 32, 'ep-a', ['ep-a', 'ep-b'],
                        '/audio/target.mp3', ['/audio/sib.mp3'])

    assert captured['min_matches'] == 1


def test_target_is_first_episode(tmp_path):
    """First episode in the sorted-then-passed list is used as target frame."""
    import audio_fingerprinter as afp_module
    from api.cue_templates import _run_cue_cross_episode_scan

    db = _make_db()
    captured = {}

    def _spy(self, target_path, sibling_paths, **kwargs):
        captured['target'] = target_path
        captured['siblings'] = sibling_paths
        return []

    with patch('api.cue_templates.get_database', return_value=db):
        with patch.object(afp_module.AudioFingerprinter, 'discover_cross_episode_body', _spy):
            with patch.object(afp_module.AudioFingerprinter, '_generate_full_fingerprint',
                              return_value=([0] * 400, 60.0)):
                with patch.object(afp_module.AudioFingerprinter, 'is_available',
                                  return_value=True):
                    _run_cue_cross_episode_scan(
                        1, 'dd' * 32,
                        'ep-first', ['ep-first', 'ep-second'],
                        '/audio/target.mp3',
                        ['/audio/sib.mp3'],
                    )

    assert captured['target'] == '/audio/target.mp3'
    assert captured['siblings'] == ['/audio/sib.mp3']


def test_episodes_indices_mapped_to_episode_ids(tmp_path):
    """Fingerprinter indices become episode IDs in request order; an episode
    the fingerprinter could not enumerate gets an explicit zero entry."""
    db = _make_db()
    fake_candidates = [{
        'start': 3.0, 'end': 5.5, 'kind': 'recurring', 'episodeMatches': 2,
        'episodes': [
            {'index': 0, 'matchCount': 1,
             'matches': [{'start': 3.0, 'end': 5.5}]},
            {'index': 2, 'matchCount': 2,
             'matches': [{'start': 10.0, 'end': 12.5},
                         {'start': 40.0, 'end': 42.5}]},
        ],
    }]
    _run(db, 1, 'dd' * 32, 'ep-target', ['ep-target', 'ep-b', 'ep-c'],
         str(tmp_path / 'target.mp3'),
         [str(tmp_path / 'sib1.mp3'), str(tmp_path / 'sib2.mp3')],
         fake_candidates)

    payload = db.save_cue_cross_episode_scan_result.call_args[0][2]
    eps = payload['candidates'][0]['episodes']
    assert [e['episodeId'] for e in eps] == ['ep-target', 'ep-b', 'ep-c']
    assert eps[0]['matchCount'] == 1
    # ep-b (index 1) was not enumerable -> explicit zero entry
    assert eps[1] == {'episodeId': 'ep-b', 'matchCount': 0, 'matches': []}
    assert eps[2]['matchCount'] == 2
    assert 'index' not in eps[0]


def test_candidates_without_episodes_pass_through(tmp_path):
    """Old-shape candidates (no episodes key) are saved unchanged."""
    db = _make_db()
    fake_candidates = [
        {'start': 3.0, 'end': 5.5, 'kind': 'recurring', 'episodeMatches': 1},
    ]
    _run(db, 1, 'ee' * 32, 'ep-a', ['ep-a', 'ep-b'],
         str(tmp_path / 'target.mp3'),
         [str(tmp_path / 'sib.mp3')],
         fake_candidates)

    payload = db.save_cue_cross_episode_scan_result.call_args[0][2]
    assert 'episodes' not in payload['candidates'][0]
