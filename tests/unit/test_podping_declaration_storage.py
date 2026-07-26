"""Persisting the upstream <podcast:podping> declaration per feed."""
import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('podping_decl_store_test_')

from database import Database

SLUG = 'podping-decl-feed'


@pytest.fixture
def db():
    d = Database()
    d.create_podcast(SLUG, 'https://feeds.example.com/show', 'Decl Feed')
    yield d
    d.delete_podcast(SLUG)


def test_defaults_to_unknown(db):
    assert db.get_podping_declaration(SLUG) == {
        'uses_podping': None, 'hive_accounts': []}


def test_round_trips_accounts_and_opt_in(db):
    db.set_podping_declaration(SLUG, True, ['podping.aaa', 'podping.bbb'])
    assert db.get_podping_declaration(SLUG) == {
        'uses_podping': True, 'hive_accounts': ['podping.aaa', 'podping.bbb']}


def test_round_trips_opt_out(db):
    db.set_podping_declaration(SLUG, False, [])
    assert db.get_podping_declaration(SLUG) == {
        'uses_podping': False, 'hive_accounts': []}


def test_clearing_back_to_unknown(db):
    db.set_podping_declaration(SLUG, True, ['podping.aaa'])
    db.set_podping_declaration(SLUG, None, [])
    assert db.get_podping_declaration(SLUG) == {
        'uses_podping': None, 'hive_accounts': []}


def test_unknown_slug_is_unknown(db):
    assert db.get_podping_declaration('no-such-feed') == {
        'uses_podping': None, 'hive_accounts': []}


def test_corrupt_json_degrades_to_no_accounts(db):
    db.set_podping_declaration(SLUG, True, ['podping.aaa'])
    conn = db.get_connection()
    conn.execute("UPDATE podcasts SET podping_hive_accounts = ? WHERE slug = ?",
                 ('{not json', SLUG))
    conn.commit()
    assert db.get_podping_declaration(SLUG) == {
        'uses_podping': True, 'hive_accounts': []}


def test_all_declarations_maps_slug_to_rules(db):
    db.set_podping_declaration(SLUG, True, ['podping.aaa'])
    rules = db.get_all_podping_declarations()
    assert rules[SLUG] == {'uses_podping': True, 'hive_accounts': ['podping.aaa']}


def test_columns_helper_matches_what_the_refresh_path_writes():
    from database.podcasts import podping_declaration_columns
    # checked_at is always stamped so a tagless feed is distinguishable from
    # one whose body has never been read.
    for uses, accounts, expected_uses, expected_json in [
        (None, [], None, None),
        (False, [], 0, None),
        (True, ['podping.aaa'], 1, '["podping.aaa"]'),
    ]:
        cols = podping_declaration_columns(uses, accounts)
        assert cols['podping_uses'] == expected_uses
        assert cols['podping_hive_accounts'] == expected_json
        assert cols['podping_checked_at']


def test_parsed_feed_round_trips_through_storage(db):
    """The parser output feeds the storage layer without reshaping."""
    from rss_parser import RSSParser
    feed = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:podcast="https://podcastindex.org/namespace/1.0">
  <channel>
    <title>Decl Feed</title>
    <podcast:podping>
      <podcast:hiveAccount account="podping.aaa"/>
      <podcast:hiveAccount account="podping.bbb"/>
    </podcast:podping>
  </channel>
</rss>"""
    parsed = RSSParser.extract_podping_declaration(feed)
    db.set_podping_declaration(SLUG, parsed['uses_podping'], parsed['hive_accounts'])
    assert db.get_podping_declaration(SLUG) == parsed
