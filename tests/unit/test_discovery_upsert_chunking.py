"""One transaction spanning a whole archive feed held the single SQLite write
lock long enough for every other writer to exceed its 30s busy_timeout and
fail with "database is locked". Discovery writes are chunked instead.
"""
from database import episodes as episodes_mod


def _episodes(n, start=0):
    return [{'id': f'ep{i}', 'title': f'Episode {i}',
             'published': '2026-01-01T00:00:00Z',
             'original_url': f'https://example.com/{i}.mp3'}
            for i in range(start, start + n)]


def test_a_large_feed_is_written_in_several_transactions(temp_db, monkeypatch):
    temp_db.create_podcast('archive', 'https://example.com/a.xml', 'Archive')
    monkeypatch.setattr(episodes_mod, 'DISCOVERY_UPSERT_CHUNK', 10)
    opened = []
    real = temp_db.transaction
    monkeypatch.setattr(temp_db, 'transaction',
                        lambda **kw: (opened.append(1), real(**kw))[1])

    inserted = temp_db.bulk_upsert_discovered_episodes('archive', _episodes(35))

    assert inserted == 35
    assert len(opened) == 4


def test_a_small_feed_still_uses_one_transaction(temp_db, monkeypatch):
    temp_db.create_podcast('small', 'https://example.com/s.xml', 'Small')
    monkeypatch.setattr(episodes_mod, 'DISCOVERY_UPSERT_CHUNK', 50)
    opened = []
    real = temp_db.transaction
    monkeypatch.setattr(temp_db, 'transaction',
                        lambda **kw: (opened.append(1), real(**kw))[1])

    temp_db.bulk_upsert_discovered_episodes('small', _episodes(5))

    assert len(opened) == 1


def test_every_episode_lands_and_recount_is_idempotent(temp_db, monkeypatch):
    temp_db.create_podcast('archive', 'https://example.com/a.xml', 'Archive')
    monkeypatch.setattr(episodes_mod, 'DISCOVERY_UPSERT_CHUNK', 7)

    first = temp_db.bulk_upsert_discovered_episodes('archive', _episodes(20))
    second = temp_db.bulk_upsert_discovered_episodes('archive', _episodes(20))

    assert first == 20
    assert second == 0
    rows, total = temp_db.get_episodes('archive', limit=100)
    assert total == 20
    assert len({r['episode_id'] for r in rows}) == 20


def test_new_episodes_on_a_later_refresh_are_counted_once(temp_db, monkeypatch):
    temp_db.create_podcast('archive', 'https://example.com/a.xml', 'Archive')
    monkeypatch.setattr(episodes_mod, 'DISCOVERY_UPSERT_CHUNK', 5)
    temp_db.bulk_upsert_discovered_episodes('archive', _episodes(12))

    added = temp_db.bulk_upsert_discovered_episodes('archive', _episodes(18))

    assert added == 6


def test_empty_feed_opens_no_transaction(temp_db, monkeypatch):
    temp_db.create_podcast('empty', 'https://example.com/e.xml', 'Empty')
    opened = []
    real = temp_db.transaction
    monkeypatch.setattr(temp_db, 'transaction',
                        lambda **kw: (opened.append(1), real(**kw))[1])

    assert temp_db.bulk_upsert_discovered_episodes('empty', []) == 0
    assert opened == []
