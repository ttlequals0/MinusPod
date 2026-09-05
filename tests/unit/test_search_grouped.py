"""Grouped /search: three independent groups (shows, episodes, transcripts).
Titles get a substring LIKE pass in addition to porter unicode61 word matching."""
from pathlib import Path

import yaml

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('search_grouped_')

import database
from database.search import SEARCH_GROUP_NAMES
from main_app import app
from api import get_database

db = database.Database()

_counter = [0]


def _eid() -> str:
    # 'a' prefix keeps this module's ids disjoint from test_search_index_coverage's:
    # test_api_aliases_processed_status_to_completed writes through get_database(),
    # which after collection may be that module's Database singleton.
    _counter[0] += 1
    return f"a{_counter[0]:011x}"


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


def test_transcripts_group_honours_the_callers_limit():
    # The group hard-coded LIMIT 3, so the Advanced page could never reach past the
    # first three body-only matches however high a limit it asked for.
    slug = _feed('transcript-limit')
    for _ in range(40):
        ep_id = _eid()
        db.upsert_episode(slug, ep_id, title=f'Episode {ep_id}')
        db.save_episode_details(slug, ep_id, transcript_text='mentions targaryen dragons extensively')
        db.index_episode(ep_id, slug)
    assert len(db.search_grouped('targaryen', limit=50)['transcripts']) == 40
    assert len(db.search_grouped('targaryen', limit=5)['transcripts']) == 5


def test_transcripts_group_caps_hits_for_one_episode():
    slug = _feed('transcript-per-episode')
    ep_id = _eid()
    db.upsert_episode(slug, ep_id, title='Repetition Hour')
    db.save_episode_details(slug, ep_id,
                            transcript_text=' '.join(['sesquipedalian filler'] * 10))
    db.index_episode(ep_id, slug)
    hits = [t for t in db.search_grouped('sesquipedalian', limit=50)['transcripts']
            if t['episodeId'] == ep_id]
    # One index row per episode is what keeps a verbose episode from filling the group;
    # a multi-row index would have to reintroduce a cap to keep this at 1.
    assert len(hits) == 1


def test_transcript_hit_has_no_invented_timestamp():
    # Unique word: a shared term would pull in the 40 rows the limit test seeds.
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


def test_one_failing_group_does_not_blank_the_other_four(monkeypatch):
    _feed('isolation-show-zorblat', title='Zorblat Network')
    ep_slug = _feed('isolation-episode-zorblat')
    ep_id = _eid()
    db.upsert_episode(ep_slug, ep_id, title='Zorblat Marmalade Hour')
    db.save_episode_details(ep_slug, ep_id, transcript_text='the host says zorblat repeatedly')
    sponsor_id = db.create_known_sponsor('Zorblat Supply Co')
    db.create_ad_pattern('global', text_template='this episode is brought to you by zorblat',
                         sponsor_id=sponsor_id)
    db.rebuild_search_index()

    def boom(*args, **kwargs):
        raise RuntimeError('group failure')

    monkeypatch.setattr(type(db), '_search_shows', boom)
    result = db.search_grouped('Zorblat')
    assert result['shows'] == []
    assert any(e['episodeId'] == ep_id for e in result['episodes'])
    assert any(t['episodeId'] == ep_id for t in result['transcripts'])
    assert any(p['sponsor'] == 'Zorblat Supply Co' for p in result['patterns'])
    assert any(sp['name'] == 'Zorblat Supply Co' for sp in result['sponsors'])


def test_content_type_filter_does_not_reweight_show_ranking():
    # bm25 divides a term's contribution by the row's length, so scoring the
    # content_type term favours the shorter row and can invert a pair. The
    # denser-but-longer row must stay ahead of the short one.
    short = _feed('rank-alpha-short', title='Alphacorn')
    long_ = _feed('rank-alpha-long', title='Alphacorn Alphacorn Alphacorn Alphacorn')
    db.update_podcast(long_, description='filler word here ' * 5)
    db.rebuild_search_index()
    slugs = [s['slug'] for s in db.search_grouped('Alphacorn')['shows']]
    assert slugs.index(long_) < slugs.index(short)


# groups= restricts which of the five groups search_grouped actually queries.


def _group_call_counts(monkeypatch):
    """Counts, per group name, how many times search_grouped ran that group's query."""
    calls = {}

    def counting(name, original):
        def wrapper(self, *a, **kw):
            calls[name] = calls.get(name, 0) + 1
            return original(self, *a, **kw)
        return wrapper

    for name in SEARCH_GROUP_NAMES:
        attr = f'_search_{name}'
        monkeypatch.setattr(type(db), attr, counting(name, getattr(type(db), attr)))
    return calls


def test_groups_param_skips_unrequested_group_functions(monkeypatch):
    calls = _group_call_counts(monkeypatch)
    result = db.search_grouped('Zorblat', groups=['shows', 'episodes', 'transcripts'])
    assert calls == {'shows': 1, 'episodes': 1, 'transcripts': 1}
    assert result['patterns'] == [] and result['sponsors'] == []

    # A duplicated name collapses to one call: groups=shows,shows behaves as groups=shows.
    calls.clear()
    db.search_grouped('Zorblat', groups=['shows', 'shows'])
    assert calls == {'shows': 1}


def test_groups_param_empty_token_between_commas_is_ignored(monkeypatch):
    calls = _group_call_counts(monkeypatch)
    client = _authed_client()
    resp = client.get('/api/v1/search?q=test&groups=shows,,episodes')
    assert resp.status_code == 200
    assert calls == {'shows': 1, 'episodes': 1}


def test_groups_param_is_case_sensitive():
    client = _authed_client()
    resp = client.get('/api/v1/search?q=test&groups=Shows')
    assert resp.status_code == 400 and 'Shows' in resp.get_json()['error']


def test_groups_param_wire_format_is_one_comma_joined_value(monkeypatch):
    # openapi's style: form, explode: false means a single "?groups=a,b,c" query value,
    # not repeated "?groups=a&groups=b&groups=c"; confirm Flask still sees it that way.
    calls = _group_call_counts(monkeypatch)
    client = _authed_client()
    resp = client.get('/api/v1/search?q=test&groups=shows,episodes,transcripts')
    assert resp.status_code == 200
    assert calls == {'shows': 1, 'episodes': 1, 'transcripts': 1}


def test_groups_param_requested_groups_still_compute():
    slug = _feed('groups-subset-show', title='Vulpine Broadcasting')
    result = db.search_grouped('Vulpine', groups=['shows', 'episodes', 'transcripts'])
    assert any(s['slug'] == slug for s in result['shows'])


def test_groups_param_default_is_all_five():
    result = db.search_grouped('an-unmatched-query-xyz', groups=None)
    assert set(result.keys()) == {'shows', 'episodes', 'transcripts', 'patterns', 'sponsors'}


def test_groups_param_unknown_value_returns_400_naming_it():
    client = _authed_client()
    resp = client.get('/api/v1/search?q=test&groups=shows,bogus')
    assert resp.status_code == 400
    assert 'bogus' in resp.get_json()['error']


def test_groups_param_first_unknown_value_named_when_several_are_bad():
    client = _authed_client()
    resp = client.get('/api/v1/search?q=test&groups=firstbad,secondbad')
    assert resp.status_code == 400
    assert 'firstbad' in resp.get_json()['error']
    assert 'secondbad' not in resp.get_json()['error']


def test_groups_param_empty_value_defaults_to_all_five():
    # get_database(), not module-level db: an unrelated test's temp_db fixture can reset
    # the Database singleton before this runs, orphaning a captured reference.
    live_db = get_database()
    slug = f'groups-empty-default-{_eid()}'
    live_db.create_podcast(slug, f'https://example.com/{slug}.xml', 'The Daily Tech Show')
    ep_id = _eid()
    live_db.upsert_episode(slug, ep_id, title='Empty Groups Param Cinnabar')
    client = _authed_client()
    body = client.get('/api/v1/search?q=Cinnabar&groups=').get_json()
    assert any(e['episodeId'] == ep_id for e in body['episodes'])
    assert 'patterns' in body and 'sponsors' in body


def test_groups_param_via_api_only_requested_group_has_matches():
    live_db = get_database()
    sponsor_id = live_db.create_known_sponsor('Cinnabar Roasters Unique Co')
    live_db.create_ad_pattern('global', text_template='mentions cinnabarroastersco ad copy', sponsor_id=sponsor_id)
    live_db.rebuild_search_index()
    client = _authed_client()
    body = client.get('/api/v1/search?q=cinnabarroastersco&groups=shows,episodes').get_json()
    assert body['patterns'] == [] and body['sponsors'] == []


def test_literal_mark_tag_in_a_description_is_not_read_as_a_highlight():
    # The description is checked before the title and holds a literal <mark> tag
    # without matching, so a substring test for "<mark>" would return it unhighlighted.
    slug = _feed('literal-mark-tag')
    ep_id = _eid()
    db.upsert_episode(slug, ep_id, title='Flimflammery Hour',
                      description='see <mark>the notes</mark> for details')
    hit = next(e for e in db.search_grouped('flimflammery')['episodes']
               if e['episodeId'] == ep_id)
    assert '<mark>Flimflammery</mark>' in hit['snippet']


def test_episode_like_fallback_skipped_when_fts_found_a_row():
    # The fallback is a leading-wildcard scan of the whole episodes table, and it ran
    # whenever FTS returned fewer rows than the limit, which at limit=50 is almost always.
    slug = _feed('like-gate')
    ep_id = _eid()
    db.upsert_episode(slug, ep_id, title='Chronosynclastic Infundibulum')
    conn = db.get_connection()
    statements = []
    conn.set_trace_callback(statements.append)
    try:
        result = db.search_grouped('Chronosynclastic', limit=50)
    finally:
        conn.set_trace_callback(None)
    assert any(e['episodeId'] == ep_id for e in result['episodes'])
    assert not any('e.title LIKE' in s for s in statements), statements


def test_episode_title_substring_still_found_when_fts_finds_nothing():
    slug = _feed('like-episode-substring')
    ep_id = _eid()
    db.upsert_episode(slug, ep_id, title='Thundersnow Bulletin')
    assert any(e['episodeId'] == ep_id for e in db.search_grouped('hundersnow')['episodes'])


def test_query_under_two_characters_runs_no_queries():
    # /quick-search enforced this minimum; without it a one-character needle runs five
    # FTS queries plus the LIKE passes and matches nearly everything.
    conn = db.get_connection()
    statements = []
    conn.set_trace_callback(statements.append)
    try:
        result = db.search_grouped('a')
    finally:
        conn.set_trace_callback(None)
    assert result == {'shows': [], 'episodes': [], 'transcripts': [], 'patterns': [], 'sponsors': []}
    assert statements == []


def test_api_one_character_query_returns_five_empty_groups():
    client = _authed_client()
    body = client.get('/api/v1/search?q=+a+').get_json()
    assert body['query'] == 'a'
    assert all(body[name] == [] for name in
               ('shows', 'episodes', 'transcripts', 'patterns', 'sponsors'))


def test_retired_type_param_is_rejected_naming_groups():
    # /search validated and honoured type= before the grouped rewrite; silently ignoring
    # it hands a client built against the old contract a different payload with no error.
    client = _authed_client()
    resp = client.get('/api/v1/search?q=zorblat&type=episode')
    assert resp.status_code == 400
    assert 'groups' in resp.get_json()['error']
    # Without q it still names type=, not the missing q: the retired parameter is the
    # thing the client has to change.
    assert 'groups' in client.get('/api/v1/search?type=episode').get_json()['error']
    assert client.get('/api/v1/search?q=zorblat').status_code == 200


def test_snippet_escapes_ampersands_and_literal_mark_tags():
    # nh3 was given tags={'mark'}, so a literal <mark> in indexed text survived as a real
    # tag and the frontend rendered it as a highlight.
    slug = _feed('entity-snippet')
    ep_id = _eid()
    db.upsert_episode(slug, ep_id, title='Ligature Hour',
                      description='sponsored by AT&T with <mark>fake</mark> zibbleflux copy')
    hit = next(e for e in db.search_grouped('zibbleflux')['episodes']
               if e['episodeId'] == ep_id)
    assert 'AT&amp;T' in hit['snippet']
    assert '&lt;mark&gt;fake&lt;/mark&gt;' in hit['snippet']
    assert '<mark>zibbleflux</mark>' in hit['snippet']


def test_openapi_groups_enum_matches_the_server():
    """The spec's enum and SEARCH_GROUP_NAMES have to name the same five groups: the
    server rejects anything outside its own tuple, so drift is a 400 for every client."""
    spec = Path(__file__).resolve().parents[2] / 'openapi.yaml'
    with open(spec) as f:
        doc = yaml.safe_load(f)
    groups = next(p for p in doc['paths']['/search']['get']['parameters']
                  if p['name'] == 'groups')
    assert tuple(groups['schema']['items']['enum']) == SEARCH_GROUP_NAMES
    assert tuple(groups['schema']['default']) == SEARCH_GROUP_NAMES
