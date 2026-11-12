"""network_id_override is normalized where it is written, so readers use the plain column."""
import pytest

from database.podcasts import EFFECTIVE_NETWORK_SQL, MAX_NETWORK_ID_CHARS
from config_transfer import ConfigTransferError, _validated_feed_updates

NBSP = '\u00a0'
GATE = 'trim_podcast_network_id_overrides'


def _override(db, slug):
    return db.get_connection().execute(
        "SELECT network_id_override FROM podcasts WHERE slug = ?", (slug,)).fetchone()[0]


@pytest.fixture
def feed(temp_db):
    temp_db.create_podcast('show-a', 'https://example.com/a.xml', 'A')
    return 'show-a'


@pytest.mark.parametrize('given, stored', [
    ('  Acme Net  ', 'Acme Net'),
    (f'{NBSP}Acme Net{NBSP}\u2003', 'Acme Net'),
    ('', None),
    (f'  {NBSP} ', None),
    (None, None),
    ('Acme Net', 'Acme Net'),
])
def test_update_podcast_stores_the_trimmed_override(temp_db, feed, given, stored):
    temp_db.update_podcast(feed, network_id_override=given)
    assert _override(temp_db, feed) == stored


def test_config_import_stores_the_trimmed_override_and_rejects_a_long_one(temp_db):
    updates = _validated_feed_updates(temp_db, None, {'network_id_override': f' Acme{NBSP}'}, 'subscribed')
    temp_db.create_podcast('show-b', 'https://example.com/b.xml', 'B')
    temp_db.update_podcast('show-b', **updates)
    assert _override(temp_db, 'show-b') == 'Acme'
    with pytest.raises(ConfigTransferError):
        _validated_feed_updates(
            temp_db, None, {'network_id_override': 'x' * (MAX_NETWORK_ID_CHARS + 1)}, 'subscribed')


def test_effective_network_is_the_plain_coalesce():
    assert EFFECTIVE_NETWORK_SQL == "COALESCE(network_id_override, network_id)"


OLD_EFFECTIVE_SQL = "COALESCE(NULLIF(TRIM(network_id_override), ''), network_id)"
# Overrides whose edges Python strips but SQL TRIM does not (or blank ones), with the intended new effective network
WHITESPACE_ROWS = {'w1': (f' Acme{NBSP}', 'det', 'Acme'), 'w2': (NBSP, None, None), 'w3': (f'{NBSP}', 'det', 'det')}
PLAIN_ROWS = {'p1': ('Plain', 'det'), 'p2': ('', 'det'), 'p3': (None, 'det'), 'p4': ('Plain', None),
              'p5': ('  Padded ', 'det'), 'p6': ('   ', None)}


def test_effective_network_matches_before_and_after_the_migration(temp_db):
    conn = temp_db.get_connection()
    rows = {**{k: v for k, v in PLAIN_ROWS.items()}, **{k: v[:2] for k, v in WHITESPACE_ROWS.items()}}
    for slug, (override, detected) in rows.items():
        temp_db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
        conn.execute("UPDATE podcasts SET network_id_override = ?, network_id = ? WHERE slug = ?",
                     (override, detected, slug))
    conn.commit()
    old = {slug: conn.execute(f"SELECT {OLD_EFFECTIVE_SQL} FROM podcasts WHERE slug = ?", (slug,)).fetchone()[0]
           for slug in rows}

    conn.execute("DELETE FROM schema_migrations WHERE name = ?", (GATE,))
    temp_db._run_trim_podcast_network_id_overrides(conn)

    for slug in PLAIN_ROWS:
        assert temp_db.get_effective_network_id(slug) == (old[slug] or None), slug
    for slug, (_override_value, _detected, expected) in WHITESPACE_ROWS.items():
        assert temp_db.get_effective_network_id(slug) == expected, slug
    assert _override(temp_db, 'w1') == 'Acme' and _override(temp_db, 'w2') is None and _override(temp_db, 'p2') is None


def test_the_migration_runs_once(temp_db, feed):
    conn = temp_db.get_connection()
    conn.execute("DELETE FROM schema_migrations WHERE name = ?", (GATE,))
    temp_db._run_trim_podcast_network_id_overrides(conn)
    conn.execute("UPDATE podcasts SET network_id_override = ' padded ' WHERE slug = ?", (feed,))
    conn.commit()
    temp_db._run_trim_podcast_network_id_overrides(conn)
    assert _override(temp_db, feed) == ' padded '


def test_pattern_network_id_is_stored_trimmed(temp_db):
    pid = temp_db.create_ad_pattern(scope='network', text_template='read ' * 12, network_id=f' net1{NBSP}')
    assert temp_db.get_ad_pattern_by_id(pid)['network_id'] == 'net1'
    temp_db.update_ad_pattern(pid, network_id=f'{NBSP}net2 ')
    assert temp_db.get_ad_pattern_by_id(pid)['network_id'] == 'net2'
