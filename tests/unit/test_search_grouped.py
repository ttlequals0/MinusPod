"""Grouped /search: three independent groups (shows, episodes, transcripts).

Word matching only for descriptions/transcripts (porter unicode61, no LIKE);
titles additionally get a substring LIKE pass. Transcripts are capped since
search_index holds one row per episode (see search_grouped design note).
"""
from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('search_grouped_')

import database
from main_app import app
from api import get_database

db = database.Database()

_counter = [0]


def _eid() -> str:
    _counter[0] += 1
    return f"{_counter[0]:012x}"


def _feed(slug, title='The Daily Tech Show'):
    db.create_podcast(slug, f'https://example.com/{slug}.xml', title)
    return slug


def _authed_client():
    client = app.test_client()
    with client.session_transaction() as sess:
        sess['authenticated'] = True
    return client


def test_word_boundary_atter_does_not_match_batteries():
    # Title deliberately excludes 'batter': this pins the description's word-only
    # match, not the title's substring LIKE pass (which would match 'atter' in
    # 'Batteries' and defeat the point of the test).
    slug = _feed('word-boundary-neg')
    ep_id = _eid()
    db.upsert_episode(slug, ep_id, title='Deep Dive Episode', description='rechargeable batteries')
    result = db.search_grouped('atter')
    assert result['shows'] == [] and result['episodes'] == [] and result['transcripts'] == []


def test_word_boundary_batter_matches_batteries():
    slug = _feed('word-boundary-pos')
    ep_id = _eid()
    db.upsert_episode(slug, ep_id, title='Deep Dive Episode', description='rechargeable batteries')
    result = db.search_grouped('batter')
    assert any(e['episodeId'] == ep_id for e in result['episodes'])


def test_queued_episode_appears_with_status():
    slug = _feed('queued-status')
    ep_id = _eid()
    db.upsert_episode(slug, ep_id, title='Quixotic Marmalade Hour', status='pending')
    result = db.search_grouped('Quixotic')
    match = next(e for e in result['episodes'] if e['episodeId'] == ep_id)
    assert match['status'] == 'pending'


def test_api_aliases_processed_status_to_completed():
    # Use get_database() (not module-level db): an unrelated test's temp_db
    # fixture can reset the Database singleton before this runs, orphaning a captured reference.
    live_db = get_database()
    slug = f'processed-alias-{_eid()}'
    live_db.create_podcast(slug, f'https://example.com/{slug}.xml', 'The Daily Tech Show')
    ep_id = _eid()
    live_db.upsert_episode(slug, ep_id, title='Vexillological Chronicles', status='processed')
    client = _authed_client()
    body = client.get('/api/v1/search?q=Vexillological').get_json()
    match = next(e for e in body['episodes'] if e['episodeId'] == ep_id)
    assert match['status'] == 'completed'


def test_transcript_cap_holds_at_three():
    slug = _feed('transcript-cap')
    for _ in range(5):
        ep_id = _eid()
        db.upsert_episode(slug, ep_id, title=f'Episode {ep_id}')
        db.save_episode_details(slug, ep_id, transcript_text='mentions targaryen dragons extensively')
        db.index_episode(ep_id, slug)
    result = db.search_grouped('targaryen')
    assert len(result['transcripts']) == 3


def test_transcript_hit_has_no_invented_timestamp():
    # Unique word: a shared term would collide with the cap test's 5 rows and
    # could fall outside the top-3 LIMIT depending on bm25 tie-breaking.
    slug = _feed('transcript-timestamp')
    ep_id = _eid()
    db.upsert_episode(slug, ep_id, title='Timestamp Show')
    db.save_episode_details(slug, ep_id, transcript_text='mentions vexillography extensively')
    db.index_episode(ep_id, slug)
    result = db.search_grouped('vexillography')
    hit = next(t for t in result['transcripts'] if t['episodeId'] == ep_id)
    assert hit['timestamp'] is None


def test_groups_are_independent():
    show_slug = _feed('independent-show-quixotic', title='Quixotic Network')
    ep_slug = _feed('independent-episode-only')
    ep_id = _eid()
    db.upsert_episode(ep_slug, ep_id, title='Quixotic Marmalade Hour')
    result = db.search_grouped('Quixotic')
    assert any(s['slug'] == show_slug for s in result['shows'])
    assert any(e['episodeId'] == ep_id for e in result['episodes'])
    assert result['transcripts'] == []


def test_one_failing_group_does_not_blank_the_others(monkeypatch):
    show_slug = _feed('isolation-show-zorblat', title='Zorblat Network')
    ep_slug = _feed('isolation-episode-zorblat')
    ep_id = _eid()
    db.upsert_episode(ep_slug, ep_id, title='Zorblat Marmalade Hour')

    def boom(*args, **kwargs):
        raise RuntimeError('group failure')

    monkeypatch.setattr(type(db), '_search_shows', boom)
    result = db.search_grouped('Zorblat')
    assert result['shows'] == []
    assert any(e['episodeId'] == ep_id for e in result['episodes'])
    assert not any(s['slug'] == show_slug for s in result['shows'])


def test_show_title_substring_like_pass():
    slug = _feed('like-substring', title='Watchdog Weekly')
    result = db.search_grouped('atchdog')
    assert any(s['slug'] == slug for s in result['shows'])


def test_description_match_is_word_only_no_like():
    slug = _feed('description-word-only')
    ep_id = _eid()
    db.upsert_episode(slug, ep_id, title=f'Episode {ep_id}', description='a deep dive into glorbnorf farming')
    result = db.search_grouped('lorbnorf')
    assert result['episodes'] == []
    result = db.search_grouped('glorb')
    assert any(e['episodeId'] == ep_id for e in result['episodes'])


def test_colon_in_query_finds_real_title_match():
    # Episode titles like "Ep 12: Title" are mainstream; a colon must not be parsed as FTS5 syntax.
    slug = _feed('colon-query')
    ep_id = _eid()
    db.upsert_episode(slug, ep_id, title='Interview: Someone Interesting')
    result = db.search_grouped('Interview: Someone')
    assert any(e['episodeId'] == ep_id for e in result['episodes'])


def test_fts_operator_characters_do_not_raise():
    empty = {'shows': [], 'episodes': [], 'transcripts': [], 'patterns': [], 'sponsors': []}
    for needle in ('foo:bar', '(', 'a OR OR b'):
        assert db.search_grouped(needle) == empty


def test_transcript_only_match_absent_from_episodes_group():
    slug = _feed('column-separation-transcript')
    ep_id = _eid()
    db.upsert_episode(slug, ep_id, title='Episode Title Unrelated')
    db.save_episode_details(slug, ep_id, transcript_text='discusses xenoglossy extensively')
    db.index_episode(ep_id, slug)
    result = db.search_grouped('xenoglossy')
    assert result['episodes'] == []
    assert any(t['episodeId'] == ep_id for t in result['transcripts'])


def test_title_or_description_match_absent_from_transcripts_group():
    slug = _feed('column-separation-episode')
    ep_id = _eid()
    db.upsert_episode(slug, ep_id, title='Discusses Ephemeralization Theory',
                       description='about ephemeralization')
    result = db.search_grouped('ephemeralization')
    assert result['transcripts'] == []
    assert any(e['episodeId'] == ep_id for e in result['episodes'])


# Patterns and sponsors are Advanced-page-only groups, always present in the response.
# They only land in search_index via a rebuild, so these run last in the file: a full
# rebuild also reindexes every podcast/episode created by the tests above it.


def test_patterns_group_matches_after_rebuild():
    sponsor_id = db.create_known_sponsor('Vermillion Botanics')
    db.create_ad_pattern('global', text_template='mentions vermillionbotanics ad copy', sponsor_id=sponsor_id)
    db.rebuild_search_index()
    result = db.search_grouped('vermillionbotanics')
    assert any(p['sponsor'] == 'Vermillion Botanics' for p in result['patterns'])


def test_sponsors_group_matches_after_rebuild():
    db.create_known_sponsor('Quixotic Roasters', aliases=['quixoticroasters'])
    db.rebuild_search_index()
    result = db.search_grouped('Quixotic Roasters')
    assert any(s['name'] == 'Quixotic Roasters' for s in result['sponsors'])


def test_grouped_response_always_has_five_keys():
    result = db.search_grouped('an-unmatched-query-xyz')
    assert set(result.keys()) == {'shows', 'episodes', 'transcripts', 'patterns', 'sponsors'}
