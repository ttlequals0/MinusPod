"""Adjacent-episode navigation (issue #417).

EpisodeMixin.get_episode_neighbors returns the previous (newer) and next (older)
episode within a feed, by the same newest-first order the feed list uses
(COALESCE(published_at, created_at), id). The episode page renders these as
prev/next controls.
"""

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('neighbors_test_')

import database

db = database.Database()

_counter = [0]


def _eid() -> str:
    _counter[0] += 1
    return f"{_counter[0]:012x}"


def _seed(slug, episodes):
    """episodes: list of (label, published_at|None). Inserted in list order, so
    a later index has a later created_at / higher row id. Returns {label: eid}."""
    db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    ids = {}
    for label, published in episodes:
        eid = _eid()
        ids[label] = eid
        db.upsert_episode(slug=slug, episode_id=eid, title=f'{slug} {label}',
                          original_url=f'https://example.com/{eid}.mp3',
                          published_at=published)
    return ids


def test_middle_episode_has_both_neighbors():
    ids = _seed('multi', [
        ('A', '2026-01-01T00:00:00Z'),  # oldest
        ('B', '2026-02-01T00:00:00Z'),
        ('C', '2026-03-01T00:00:00Z'),
        ('D', '2026-04-01T00:00:00Z'),  # newest
    ])
    nb = db.get_episode_neighbors('multi', ids['C'])
    assert nb['previous']['id'] == ids['D']   # newer
    assert nb['next']['id'] == ids['B']       # older
    assert nb['previous']['title'] == 'multi D'


def test_newest_has_no_previous():
    ids = _seed('newest', [
        ('A', '2026-01-01T00:00:00Z'),
        ('B', '2026-02-01T00:00:00Z'),
        ('C', '2026-03-01T00:00:00Z'),
    ])
    nb = db.get_episode_neighbors('newest', ids['C'])
    assert nb['previous'] is None
    assert nb['next']['id'] == ids['B']


def test_oldest_has_no_next():
    ids = _seed('oldest', [
        ('A', '2026-01-01T00:00:00Z'),
        ('B', '2026-02-01T00:00:00Z'),
        ('C', '2026-03-01T00:00:00Z'),
    ])
    nb = db.get_episode_neighbors('oldest', ids['A'])
    assert nb['previous']['id'] == ids['B']
    assert nb['next'] is None


def test_single_episode_feed_has_no_neighbors():
    ids = _seed('single', [('only', '2026-01-01T00:00:00Z')])
    nb = db.get_episode_neighbors('single', ids['only'])
    assert nb == {'previous': None, 'next': None}


def test_tie_is_deterministic_by_row_id():
    # Same published_at: the later-inserted episode (higher id) is the newer one.
    ids = _seed('tie', [
        ('first', '2026-05-01T00:00:00Z'),
        ('second', '2026-05-01T00:00:00Z'),
    ])
    nb_first = db.get_episode_neighbors('tie', ids['first'])
    nb_second = db.get_episode_neighbors('tie', ids['second'])
    assert nb_first['previous']['id'] == ids['second']  # second is newer
    assert nb_first['next'] is None
    assert nb_second['previous'] is None
    assert nb_second['next']['id'] == ids['first']


def test_null_published_at_falls_back_to_created_at():
    ids = _seed('nullpub', [('older', None), ('newer', None)])
    nb = db.get_episode_neighbors('nullpub', ids['older'])
    # Both rows share a NULL published_at; the COALESCE/id tiebreak keeps them
    # adjacent and the later-inserted one newer.
    assert nb['previous']['id'] == ids['newer']
    assert nb['next'] is None


def test_unknown_episode_and_feed_return_empty():
    _seed('known', [('A', '2026-01-01T00:00:00Z')])
    assert db.get_episode_neighbors('known', 'ffffffffffff') == {'previous': None, 'next': None}
    assert db.get_episode_neighbors('no-such-feed', 'ffffffffffff') == {'previous': None, 'next': None}
