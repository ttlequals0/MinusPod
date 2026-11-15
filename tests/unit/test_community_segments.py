"""Show-bound community segments: export, sync, binding, pipeline hooks, states (fixtures use a fake show)."""
import copy
import hashlib
import json
import uuid
from unittest.mock import patch

import pytest

import community_segments as seg
import community_segments_core as core
import community_sync
from config import HOLD_REASON_SHOW_SEGMENT_DISAGREEMENT, HOLD_REASON_SHOW_SEGMENT_SUGGESTION
from main_app.pipeline import detect

GUID = '3f1c9a2e-0b7d-5e44-9c1a-7d2e8f6a1b30'
SLUG = 'copper-kettle'
FEED = 'https://feeds.kettle.example/hour.xml'
ASK = ("If you are enjoying the copper kettle hour please leave us a review on your podcast app and follow the "
       "show so you never miss an episode and tell a friend who loves a good cup of tea")
GUESTS = ('Priya in Leeds', 'Tomas in Graz', 'Ines in Porto', 'Kofi in Accra', 'Lena in Bergen', 'Ola in Oslo')
FILLER = ("today we talk about the history of tea houses and the way kettles changed over the centuries from iron "
          "to copper and later to electric models that boil water in about a minute")


def _ts(t):
    return f'{int(t // 3600):02d}:{int(t % 3600 // 60):02d}:{t % 60:06.3f}'


def _transcript(lines):
    return '\n'.join(f'[{_ts(s)} --> {_ts(e)}] {text}' for s, e, text in lines)


def _episode_lines(i, ask_at=300.0):
    """Filler, the ask with a per-episode guest line, filler; returns (lines, ask start, ask end)."""
    ask = f'{ASK}. This week our question came from {GUESTS[i % len(GUESTS)]}.'
    return [(0.0, 12.0, FILLER + '.'), (ask_at, ask_at + 14.0, ask), (ask_at + 14.0, ask_at + 26.0, FILLER + '.')]


def _segments(lines):
    return [{'start': s, 'end': e, 'text': t} for s, e, t in lines]


@pytest.fixture
def db(temp_db):
    temp_db.create_podcast(SLUG, FEED, 'Copper Kettle Hour')
    temp_db.set_setting('community_show_segments', 'true')
    temp_db.set_setting('community_sync_enabled', 'true')
    return temp_db


def _podcast(db, slug=SLUG):
    return db.get_podcast_row(slug)


def _store_episodes(db, n, slug=SLUG, ask=True):
    ids = []
    for i in range(n):
        eid = f'ep{i:04d}abcd'
        db.upsert_episode(slug=slug, episode_id=eid, original_url=f'https://kettle.example/{i}.mp3',
                          title=f'Episode {i}', original_duration=1800.0)
        lines = _episode_lines(i) if ask else [(0.0, 12.0, FILLER + '.')]
        db.save_original_transcript(slug, eid, _transcript(lines))
        ids.append(eid)
    return ids


def _set_identity(db, guid=GUID, slug=SLUG):
    seg.refresh_identity(db, _podcast(db, slug), fetch=lambda url: guid)


def _entry(category='interaction', guid=GUID, short_ask=False, text=None, durations=(12.0, 14.0, 16.0)):
    texts = [text] * 5 if text else [f'{ASK}. This week our question came from {g}.' for g in GUESTS[:5]]
    body = core.entry_body(core.stable_core(texts, short_ask=short_ask), guid, 'podcast_guid', category=category,
                           short_ask=short_ask, durations=list(durations), language='en', confirms=5,
                           episodes=5, span_days=21, community_id=str(uuid.uuid4()),
                           submitted_at='2026-11-09T00:00:00Z', app_version='2.101.34')
    return body


def _manifest(*bodies):
    files = {}
    items = []
    for b in bodies:
        raw = json.dumps(b).encode()
        path = f"seg-{b['community_id'].replace('-', '')[:8]}.json"
        files[path] = (b, raw)
        items.append({'community_id': b['community_id'], 'version': b['version'],
                      'content_hash': hashlib.sha256(raw).hexdigest(), 'path': path})
    calls = []

    def fetch(path):
        calls.append(path)
        return files[path]
    return {'manifest_version': 2, 'patterns': [], 'show_segments': items}, fetch, calls


def _import(db, *bodies):
    manifest, fetch, _calls = _manifest(*bodies)
    return seg.apply_segment_manifest(db, manifest, fetch)


def _bound(db, body):
    _import(db, body)
    _set_identity(db)
    _store_episodes(db, 3)
    assert seg.bind_feed(db, _podcast(db)) == 1
    return seg.get_entries(db, community_id=body['community_id'])[0]


# ---------------------------------------------------------------- tables and identity (B3)

def test_channel_guid_is_read_from_the_head_only():
    head = (b'<rss><channel><title>Kettle</title><podcast:guid> 3F1C9A2E-0b7d-5e44-9c1a-7d2e8f6a1b30 '
            b'</podcast:guid><item><podcast:guid>other</podcast:guid></item>')
    assert seg.parse_podcast_guid(head) == GUID
    assert seg.parse_podcast_guid(b'<rss><channel><item><podcast:guid>x</podcast:guid>') is None


def test_show_keys_prefer_the_origin_guid_and_keep_the_url_guid(db):
    from utils.feed_guid import compute_feed_guid
    assert seg.show_keys(db, _podcast(db)) == [(compute_feed_guid(FEED), 'feed_url_guid')]
    _set_identity(db)
    assert seg.show_keys(db, _podcast(db)) == [(GUID, 'podcast_guid'), (compute_feed_guid(FEED), 'feed_url_guid')]


def test_identity_is_refetched_at_most_weekly(db):
    calls = []
    fetch = lambda url: calls.append(url) or GUID  # noqa: E731
    seg.refresh_identity(db, _podcast(db), fetch)
    seg.refresh_identity(db, _podcast(db), fetch)
    assert calls == [FEED]


def test_fetch_failure_keeps_the_last_known_guid(db):
    _set_identity(db)

    def boom(url):
        raise OSError('offline')
    from datetime import timedelta
    from utils.time import utc_now
    seg.refresh_identity(db, _podcast(db), boom, now=utc_now() + timedelta(days=8))
    assert seg.show_keys(db, _podcast(db))[0] == (GUID, 'podcast_guid')


@pytest.mark.parametrize('url,feed_type,private', [
    (FEED, 'subscribed', False),
    ('https://feeds.kettle.example/hour.xml?token=abc', 'subscribed', True),
    ('https://feeds.kettle.example/hour.xml?k=' + 'a' * 32, 'subscribed', True),
    ('https://feeds.kettle.example/hour.xml?format=mp3', 'subscribed', False),
    (FEED, 'local', True),
])
def test_private_feeds_are_never_exported(url, feed_type, private):
    assert seg.private_feed({'source_url': url, 'feed_type': feed_type}) is private


# ---------------------------------------------------------------- export (B2)

def _show_row(db, category='interaction', text=None, created_by='show_promo', **kw):
    pid = db.create_ad_pattern(scope='podcast', text_template=text or ASK, intro_variants=[], outro_variants=[],
                               podcast_id=SLUG, created_by=created_by, category=category, duration=14.0, **kw)
    return pid


def _confirm_episodes(db, pid, ids, days_apart=4):
    conn = db.get_connection()
    for i, eid in enumerate(ids):
        cid = db.create_pattern_correction('confirm', pattern_id=pid, episode_id=eid,
                                           original_bounds={'start': 300.0, 'end': 314.0},
                                           podcast_id=_podcast(db)['id'])
        conn.execute("UPDATE pattern_corrections SET created_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?) "
                     "WHERE id = ?", (f'-{(len(ids) - i) * days_apart} days', cid))
    conn.commit()


def _exportable(db, n=5, **kw):
    ids = _store_episodes(db, n)
    _set_identity(db)
    pid = _show_row(db, **kw)
    _confirm_episodes(db, pid, ids)
    return pid


def test_an_eligible_show_row_exports_an_opaque_entry(db):
    pid = _exportable(db)
    bundle, rejected = seg.build_segment_bundle(db, [pid])
    assert rejected == []
    entry = bundle['patterns'][0]
    assert entry['kind'] == 'show_segment' and entry['category'] == 'interaction' and entry['short_ask'] is False
    assert entry['bucket'] == core.bucket_for(GUID) and entry['key_kind'] == 'podcast_guid'
    assert 12 <= len(entry['core']) <= 400 and len(entry['head']) == 6
    assert entry['evidence'] == {'confirms': 5, 'episodes': 5, 'span_weeks': 2}
    text = json.dumps(bundle).lower()
    for leak in ('kettle', 'priya', 'review', SLUG, 'feeds.'):
        assert leak not in text
    from tools.show_segment_validator import entry_problems
    assert entry_problems(entry) == []


def test_reexport_keeps_the_community_id_and_bumps_the_version(db):
    pid = _exportable(db)
    first = seg.build_segment_bundle(db, [pid])[0]['patterns'][0]
    second = seg.build_segment_bundle(db, [pid])[0]['patterns'][0]
    assert second['community_id'] == first['community_id'] and second['version'] == 2


def test_guest_names_drop_out_of_the_core(db):
    pid = _exportable(db)
    entry = seg.build_segment_bundle(db, [pid])[0]['patterns'][0]
    secret = core.show_secret(GUID)
    assert core.fingerprint(secret, 'from priya in') not in entry['core']
    assert core.fingerprint(secret, 'leave us a') in entry['core']


@pytest.mark.parametrize('setup,reason', [
    (lambda db, pid: db.get_connection().execute(
        'UPDATE ad_patterns SET false_positive_count = 1 WHERE id = ?', (pid,)), 'false-positive'),
    (lambda db, pid: db.get_connection().execute('UPDATE ad_patterns SET is_active = 0 WHERE id = ?', (pid,)),
     'disabled'),
    (lambda db, pid: db.get_connection().execute(
        "UPDATE pattern_corrections SET created_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE pattern_id = ?",
        (pid,)), 'over 14 days'),
    (lambda db, pid: db.get_connection().execute(
        "DELETE FROM pattern_corrections WHERE id = (SELECT MAX(id) FROM pattern_corrections)"), 'needs 5'),
    (lambda db, pid: db.get_connection().execute(
        "UPDATE pattern_corrections SET created_at = strftime('%Y-%m-%dT%H:%M:%SZ', created_at, '-200 days')"),
     'older than 180'),
    (lambda db, pid: db.get_connection().execute(
        "UPDATE podcasts SET source_url = 'https://feeds.kettle.example/hour.xml?token=x'"), 'private'),
])
def test_every_export_gate_names_its_reason(db, setup, reason):
    pid = _exportable(db)
    setup(db, pid)
    db.get_connection().commit()
    _bundle, rejected = seg.build_segment_bundle(db, [pid])
    assert reason in rejected[0]['reasons'][0]


def test_a_reject_correction_refuses_export(db):
    pid = _exportable(db)
    db.create_pattern_correction('false_positive', pattern_id=pid, episode_id='ep0000abcd',
                                 original_bounds={'start': 300.0, 'end': 314.0})
    assert 'false-positive' in seg.build_segment_bundle(db, [pid])[1][0]['reasons'][0]


@pytest.mark.parametrize('category', ['interaction', 'self_promo', 'cross_promo'])
def test_a_row_naming_a_known_sponsor_is_refused_in_any_category(db, category):
    db.create_known_sponsor('Acmeteas')
    pid = _exportable(db, category=category, text=ASK + ' and thanks to Acmeteas for the support')
    assert 'sponsor stays sponsor' in seg.build_segment_bundle(db, [pid])[1][0]['reasons'][0]


def test_sponsor_rows_auto_rows_and_recap_rows_are_refused(db):
    sponsor_id = db.create_known_sponsor('Acmeteas')
    sponsor_row = _show_row(db, category='sponsor', sponsor_id=sponsor_id)
    auto_row = _show_row(db, created_by='auto')
    recap_row = _show_row(db, category='recap', created_by='show_segment')
    _bundle, rejected = seg.build_segment_bundle(db, [sponsor_row, auto_row, recap_row])
    reasons = [r['reasons'][0] for r in rejected]
    assert 'sponsor stays sponsor' in reasons[0]
    assert 'not a show segment' in reasons[1]
    assert 'not exported in v1' in reasons[2]


SHORT = 'Please rate and review the copper kettle hour on your podcast app'


def test_a_short_ask_row_exports_with_the_flag_and_small_shape(db):
    ids = _store_episodes(db, 5, ask=False)
    for eid in ids:
        db.save_episode_details(SLUG, eid, transcript_text=None)
        db.get_connection().execute(
            'UPDATE episode_details SET original_transcript_text = ? WHERE episode_id = '
            '(SELECT id FROM episodes WHERE episode_id = ?)', (_transcript([(300.0, 304.0, SHORT + '.')]), eid))
    db.get_connection().commit()
    _set_identity(db)
    pid = _show_row(db, text=SHORT)
    _confirm_episodes(db, pid, ids)
    db.get_connection().execute(
        "UPDATE pattern_corrections SET original_bounds = '{\"start\": 300.0, \"end\": 304.0}'")
    db.get_connection().commit()
    bundle, rejected = seg.build_segment_bundle(db, [pid])
    assert rejected == []
    entry = bundle['patterns'][0]
    assert entry['short_ask'] is True and 4 <= len(entry['core']) <= 40
    assert len(entry['head']) == 3 and len(entry['tail']) == 3


# ---------------------------------------------------------------- receiver sync (B5)

def test_old_receiver_ignores_the_new_key_with_zero_fetches(temp_db):
    body = _entry()
    manifest, _fetch, _calls = _manifest(body)
    with patch.object(community_sync, '_fetch_pattern_file') as fetch_file:
        with_key = community_sync.apply_manifest(temp_db, manifest)
        without = community_sync.apply_manifest(temp_db, {'manifest_version': 2, 'patterns': []})
    assert with_key == without
    assert fetch_file.call_count == 0
    assert temp_db.get_connection().execute('SELECT COUNT(*) FROM ad_patterns').fetchone()[0] == 0


def test_sync_inserts_updates_by_hash_and_never_writes_ad_patterns(db):
    a, b = _entry(), _entry('self_promo')
    manifest, fetch, calls = _manifest(a, b)
    assert seg.apply_segment_manifest(db, manifest, fetch)['inserted'] == 2
    assert seg.apply_segment_manifest(db, manifest, fetch)['skipped'] == 2
    assert len(calls) == 2  # unchanged hashes are not fetched again
    b2 = copy.deepcopy(b)
    b2['version'] = 2
    b2['duration']['p90'] = 20.0
    manifest2, fetch2, _ = _manifest(a, b2)
    assert seg.apply_segment_manifest(db, manifest2, fetch2)['updated'] == 1
    assert db.get_connection().execute('SELECT COUNT(*) FROM ad_patterns').fetchone()[0] == 0


def test_sync_rejects_malformed_or_tampered_files(db):
    bad = _entry()
    bad['text_template'] = 'leave us a review'
    manifest, fetch, _ = _manifest(bad)
    assert seg.apply_segment_manifest(db, manifest, fetch)['errors'] == 1
    good = _entry()
    manifest, fetch, _ = _manifest(good)
    manifest['show_segments'][0]['content_hash'] = '0' * 64
    assert seg.apply_segment_manifest(db, manifest, fetch)['errors'] == 1
    assert seg.get_entries(db) == []


def test_sync_rejects_unsafe_paths():
    with pytest.raises(ValueError):
        seg._fetch_segment_file('../community/index.json')


def test_revoke_protect_and_the_mass_delete_guard(db):
    bodies = [_entry() for _ in range(4)]
    _import(db, *bodies)
    seg.set_state(db, seg.get_entries(db, community_id=bodies[0]['community_id'])[0]['id'], 'protect')
    # Three of four absent (75 %): refused.
    manifest, fetch, _ = _manifest(bodies[3])
    assert seg.apply_segment_manifest(db, manifest, fetch)['revoked'] == 0
    # One absent of the three unprotected (33 %): revoked; the protected one stays.
    manifest, fetch, _ = _manifest(*bodies[1:3])
    out = seg.apply_segment_manifest(db, {**manifest, 'show_segments': manifest['show_segments']}, fetch)
    states = {e['community_id']: e['state'] for e in seg.get_entries(db)}
    assert out['revoked'] == 1 and states[bodies[3]['community_id']] == 'revoked'
    assert states[bodies[0]['community_id']] == 'unbound'


def test_category_filter_deactivates_and_restores(db):
    body = _entry('cross_promo')
    _import(db, body)
    db.set_setting('community_sync_categories', json.dumps(['sponsor', 'interaction']))
    out = _import(db, body)
    row = seg.get_entries(db)[0]
    assert out['filtered'] == 1 and row['is_active'] == 0 and row['disabled_reason'] == 'category_filtered'
    db.set_setting('community_sync_categories', json.dumps(['cross_promo']))
    _import(db, body)
    assert seg.get_entries(db)[0]['is_active'] == 1


def test_show_segments_are_off_on_a_fresh_install(temp_db):
    """Default off: an untouched database never syncs, binds or annotates."""
    temp_db.create_podcast(SLUG, FEED, 'Copper Kettle Hour')
    temp_db.set_setting('community_sync_enabled', 'true')
    assert seg.is_enabled(temp_db) is False
    ads = [_llm(601.0, 612.0, 'interaction')]
    assert seg.annotate(temp_db, SLUG, EP, _episode_segments(), ads, 1800.0) is ads
    assert seg.enforce_holds(ads) == [_llm(601.0, 612.0, 'interaction')]


def test_tick_is_a_no_op_unless_both_switches_are_on(temp_db):
    def boom(*a, **k):
        raise AssertionError('must not fetch')
    temp_db.set_setting('community_sync_enabled', 'true')
    assert seg.community_segments_tick(temp_db, force=True, fetch_manifest=boom) is None
    temp_db.set_setting('community_sync_enabled', 'false')
    temp_db.set_setting('community_show_segments', 'true')
    assert seg.community_segments_tick(temp_db, force=True, fetch_manifest=boom) is None


def test_tick_syncs_identifies_and_binds(db):
    body = _entry()
    manifest, fetch, _ = _manifest(body)
    _store_episodes(db, 3)
    out = seg.community_segments_tick(db, force=True, fetch_manifest=lambda: manifest, fetch_file=fetch,
                                      fetch_guid=lambda url: GUID)
    assert out['inserted'] == 1
    assert seg.get_entries(db)[0]['state'] == 'candidate'


# ---------------------------------------------------------------- binding (B6)

def test_binds_after_two_stored_transcripts_match(db):
    body = _entry()
    _import(db, body)
    _set_identity(db)
    _store_episodes(db, 1)
    assert seg.bind_feed(db, _podcast(db)) == 0
    assert seg.get_entries(db)[0]['state'] == 'unbound'
    _store_episodes(db, 2)
    assert seg.bind_feed(db, _podcast(db)) == 1
    row = seg.get_entries(db)[0]
    assert row['state'] == 'candidate' and row['bound_podcast_id'] == _podcast(db)['id']


def _same_bucket_guid(guid):
    target = core.bucket_for(guid)
    for i in range(200000):
        other = str(uuid.UUID(int=i + 1))
        if other != guid and core.bucket_for(other) == target:
            return other
    raise AssertionError('no collision found')


def test_a_wrong_feed_in_the_same_bucket_never_binds(db):
    _import(db, _entry())
    _set_identity(db, guid=_same_bucket_guid(GUID))
    _store_episodes(db, 5)
    assert seg.bind_feed(db, _podcast(db)) == 0


def test_bound_entries_are_capped_per_category(db):
    bodies = [_entry() for _ in range(seg.BOUND_PER_CATEGORY + 1)]
    _import(db, *bodies)
    _set_identity(db)
    _store_episodes(db, 3)
    assert seg.bind_feed(db, _podcast(db)) == seg.BOUND_PER_CATEGORY


# ---------------------------------------------------------------- pipeline hooks (B7)

EP = 'ep9999abcd'


def _episode_segments():
    return _segments(_episode_lines(9, ask_at=600.0))


def _llm(start, end, category, **kw):
    return {'start': start, 'end': end, 'category': category, 'confidence': 0.7, 'sponsor': None,
            'reason': 'llm', **kw}


def _annotate(db, ads, actions=None):
    return seg.annotate(db, SLUG, EP, _episode_segments(), ads, 1800.0, actions)


def test_feature_off_returns_the_same_list_object(db):
    _bound(db, _entry())
    db.set_setting('community_show_segments', 'false')
    ads = [_llm(601.0, 612.0, 'interaction')]
    assert _annotate(db, ads) is ads


def test_no_bound_entries_returns_the_same_list_object(db):
    ads = [_llm(601.0, 612.0, 'interaction')]
    assert _annotate(db, ads) is ads


def test_corroborates_a_same_family_marker(db):
    _bound(db, _entry('self_promo'))
    ads = [_llm(601.0, 613.0, 'interaction')]
    out = _annotate(db, ads)
    ad = out[0]
    assert ad['category'] == 'self_promo' and ad['confidence'] == pytest.approx(0.8)
    assert ad['show_segment']['state'] == 'candidate' and 'hold' not in ad['show_segment']
    assert (ad['start'], ad['end']) == (600.0, 614.0)
    assert not ad.get('held_for_review') and not ad.get('pattern_defined')
    from config import known_ad_evidence
    assert known_ad_evidence(ad) == []


def test_a_sponsor_marker_is_left_exactly_as_it_was(db):
    entry = _bound(db, _entry())
    sponsor = _llm(600.5, 613.0, 'sponsor', sponsor='Acmeteas')
    before = copy.deepcopy(sponsor)
    out = _annotate(db, [sponsor])
    assert out == [before]
    assert [e['kind'] for e in seg.events(db, entry['id'], 'disagree')] == ['disagree']


def test_known_ad_evidence_wins_with_a_strike(db):
    entry = _bound(db, _entry())
    marker = _llm(600.5, 613.0, 'interaction', reviewer_verdict='confirmed')
    before = copy.deepcopy(marker)
    assert _annotate(db, [marker]) == [before]
    assert seg.events(db, entry['id'], 'disagree')


def test_an_edge_move_past_the_cap_is_held_as_a_disagreement(db):
    _bound(db, _entry())
    ad = _annotate(db, [_llm(603.0, 613.0, 'interaction')])[0]
    assert ad['hold_reason'] == HOLD_REASON_SHOW_SEGMENT_DISAGREEMENT and ad['start'] == 603.0


def test_other_family_marker_is_held_as_a_disagreement(db):
    entry = _bound(db, _entry())
    out = _annotate(db, [_llm(600.5, 613.0, 'outro')])
    assert out[0]['held_for_review'] is True
    assert out[0]['hold_reason'] == HOLD_REASON_SHOW_SEGMENT_DISAGREEMENT and out[0]['category'] == 'outro'
    assert seg.events(db, entry['id'], 'disagree')[0]['hold_id'] == out[0]['hold_id']


def test_candidate_suggests_a_held_marker_where_nothing_was_detected(db):
    entry = _bound(db, _entry())
    out = _annotate(db, [])
    assert len(out) == 1
    ad = out[0]
    assert ad['detection_stage'] == 'show_segment' and ad['held_for_review'] is True
    assert ad['hold_reason'] == HOLD_REASON_SHOW_SEGMENT_SUGGESTION and ad['category'] == 'interaction'
    assert seg.events(db, entry['id'], 'suggest')[0]['hold_id'] == ad['hold_id']


def test_held_suggestion_is_never_cut_even_after_a_validator_accept():
    ad = {'start': 600.0, 'end': 614.0, 'confidence': 0.99,
          'show_segment': {'entry': 1, 'hold': HOLD_REASON_SHOW_SEGMENT_SUGGESTION},
          'validation': {'decision': 'ACCEPT', 'adjusted_confidence': 0.99}}
    seg.enforce_holds([ad])
    to_remove, _low = detect._gate_validation_by_confidence('s', 'e', [ad], 0.5)
    assert to_remove == [] and ad['was_cut'] is False and ad['validation']['decision'] == 'REVIEW'


def test_validated_entry_suggests_an_unheld_marker(db):
    entry = _bound(db, _entry())
    seg.update_entry(db, entry['id'], state='validated')
    ad = _annotate(db, [])[0]
    assert not ad.get('held_for_review') and ad['reason'] == 'Show segment (community, validated)'


def test_a_kept_marker_blocks_a_suggestion_and_is_untouched(db):
    _bound(db, _entry())
    kept = _llm(600.5, 613.0, 'interaction')
    before = copy.deepcopy(kept)
    out = seg.annotate(db, SLUG, EP, _episode_segments(), [], 1800.0, None, [kept])
    assert out == [] and kept == before


def test_keep_action_is_not_scanned(db):
    _bound(db, _entry())
    actions = db.resolve_segment_actions(SLUG)
    actions['interaction'] = 'keep'
    ads = []
    assert seg.annotate(db, SLUG, EP, _episode_segments(), ads, 1800.0, actions) is ads


def test_at_most_two_suggestions_per_episode(db):
    bodies = [_entry(c) for c in ('interaction', 'self_promo', 'cross_promo')]
    _import(db, *bodies)
    _set_identity(db)
    _store_episodes(db, 3)
    assert seg.bind_feed(db, _podcast(db)) == 3
    assert len(_annotate(db, [])) == seg.SUGGESTIONS_PER_EPISODE


def test_three_unresolved_suggestions_make_the_entry_dormant(db):
    entry = _bound(db, _entry())
    for i in range(3):
        seg.add_event(db, entry['id'], _podcast(db)['id'], f'old{i}', 'suggest', f'hold{i}')
    assert _annotate(db, []) == []
    assert seg.get_entry(db, entry['id'])['state'] == 'dormant'


def test_show_family_entries_need_show_segment_detection(db):
    intro = ('welcome to the copper kettle hour the show about kettles teapots and the people who love '
             'a good cup of tea with your hosts')
    lines = [(0.0, 9.0, intro + '.'), (9.0, 21.0, FILLER + '.')]
    body = _entry('intro', text=intro, durations=(8.0, 9.0, 10.0))
    _import(db, body)
    _set_identity(db)
    for i in range(3):
        eid = f'in{i:04d}abcd'
        db.upsert_episode(slug=SLUG, episode_id=eid, original_url=f'https://kettle.example/i{i}.mp3',
                          title=f'I {i}', original_duration=1800.0)
        db.save_original_transcript(SLUG, eid, _transcript(lines))
    assert seg.bind_feed(db, _podcast(db)) == 1
    ads = []
    assert seg.annotate(db, SLUG, EP, _segments(lines), ads, 1800.0) is ads
    db.set_setting('detect_show_segments', '1')
    out = seg.annotate(db, SLUG, EP, _segments(lines), [], 1800.0)
    assert out[0]['category'] == 'intro' and out[0]['hold_reason'] == HOLD_REASON_SHOW_SEGMENT_SUGGESTION


def test_short_ask_entry_is_inert_without_snip_and_stamps_with_it(db):
    lines = [(0.0, 12.0, FILLER + '.'), (600.0, 604.0, SHORT + '.'), (604.0, 616.0, FILLER + '.')]
    body = _entry(short_ask=True, text=SHORT, durations=(3.5, 4.0, 4.5))
    _import(db, body)
    _set_identity(db)
    for i in range(3):
        eid = f'sa{i:04d}abcd'
        db.upsert_episode(slug=SLUG, episode_id=eid, original_url=f'https://kettle.example/s{i}.mp3',
                          title=f'S {i}', original_duration=1800.0)
        db.save_original_transcript(SLUG, eid, _transcript(lines))
    assert seg.bind_feed(db, _podcast(db)) == 1
    ads = []
    assert seg.annotate(db, SLUG, EP, _segments(lines), ads, 1800.0) is ads
    db.set_setting('snip_short_asks', '1')
    ad = seg.annotate(db, SLUG, EP, _segments(lines), [], 1800.0)[0]
    assert ad['short_ask'] is True and ad['short_ask_origin'] == [600.0, 604.0]
    assert (ad['start'], ad['end']) == (600.0, 604.0)


def test_enforce_holds_acts_only_on_annotated_markers():
    plain = {'start': 1.0, 'end': 2.0}
    held = {'start': 3.0, 'end': 9.0, 'show_segment': {'entry': 1, 'hold': HOLD_REASON_SHOW_SEGMENT_SUGGESTION}}
    confirmed = {'start': 3.0, 'end': 9.0, 'validation': {'user_confirmed': True},
                 'show_segment': {'entry': 1, 'hold': HOLD_REASON_SHOW_SEGMENT_SUGGESTION}}
    seg.enforce_holds([plain, held, confirmed])
    assert plain == {'start': 1.0, 'end': 2.0}
    assert held['held_for_review'] is True and held['hold_id']
    assert 'held_for_review' not in confirmed


def test_feature_off_refine_and_validate_is_unchanged(db):
    """T-OFF-1: with the switch off the stage output deep-equals the run with the hook bypassed."""
    from ad_validator import AdValidator
    _bound(db, _entry())
    db.set_setting('community_show_segments', 'false')
    segments = _episode_segments()

    def run():
        ads = [_llm(601.0, 612.0, 'interaction'), _llm(100.0, 160.0, 'sponsor', sponsor='Acmeteas')]
        with patch.object(detect, 'db', db), patch.object(detect, 'storage'), \
                patch.object(detect, '_build_validator', side_effect=lambda *a, **k: AdValidator(1800.0, a[1], a[2])):
            return detect._refine_and_validate(SLUG, EP, ads, segments, '/x.mp3', '', 1800.0, 0.8, 'Show',
                                               corrections=([], []))
    def stable(result):  # hold ids are random per run
        return json.loads(json.dumps(result).replace('"hold_id"', '"_"'), object_hook=lambda d: {
            k: v for k, v in d.items() if k != '_'})
    off = run()
    with patch.object(seg, 'annotate', side_effect=lambda db_, s, e, g, ads, *a: ads), \
            patch.object(seg, 'enforce_holds', side_effect=lambda ads: ads):
        bypassed = run()
    assert stable(off) == stable(bypassed)


# ---------------------------------------------------------------- states (B8)

def _hold_marker(db, entry, eid=EP):
    """Run annotate for an episode with no detections and store its markers; returns the suggestion."""
    db.upsert_episode(slug=SLUG, episode_id=eid, original_url='https://kettle.example/x.mp3', title='X',
                      original_duration=1800.0)
    ads = seg.annotate(db, SLUG, eid, _episode_segments(), [], 1800.0)
    db.save_episode_details(SLUG, eid, ad_markers=ads)
    return ads[0]


def _correct(db, kind, ad, eid=EP, pattern_id=None):
    return db.create_pattern_correction(kind, pattern_id=pattern_id, episode_id=eid,
                                        original_bounds={'start': ad['start'], 'end': ad['end']},
                                        podcast_id=_podcast(db)['id'], hold_id=ad.get('hold_id'))


def test_three_confirms_on_distinct_episodes_validate(db):
    entry = _bound(db, _entry())
    for i in range(3):
        eid = f'v{i}99abcdef'
        _correct(db, 'confirm', _hold_marker(db, entry, eid), eid)
        seg.sweep(db, _podcast(db))
    assert seg.get_entry(db, entry['id'])['state'] == 'validated'


def test_any_reject_suspends_a_candidate(db):
    entry = _bound(db, _entry())
    _correct(db, 'false_positive', _hold_marker(db, entry))
    seg.sweep(db, _podcast(db))
    assert seg.get_entry(db, entry['id'])['state'] == 'suspended'
    assert seg.set_state(db, entry['id'], 'reactivate')['state'] == 'candidate'


def test_a_confirm_that_creates_a_local_show_row_adopts_the_entry(db):
    entry = _bound(db, _entry())
    ad = _hold_marker(db, entry)
    local = _show_row(db)
    _correct(db, 'confirm', ad, pattern_id=local)
    seg.sweep(db, _podcast(db))
    row = seg.get_entry(db, entry['id'])
    assert row['state'] == 'adopted' and row['adopted_pattern_id'] == local
    assert _annotate(db, []) == []  # an adopted entry stops matching


def test_a_match_over_a_local_show_row_marker_adopts_without_a_confirm(db):
    entry = _bound(db, _entry())
    local = _show_row(db)
    marker = _llm(600.5, 613.0, 'interaction', pattern_id=local)
    before = copy.deepcopy(marker)
    assert _annotate(db, [marker]) == [before]
    assert seg.get_entry(db, entry['id'])['adopted_pattern_id'] == local


def test_corroborated_marker_correction_is_found_by_overlap_after_a_recut(db):
    entry = _bound(db, _entry())
    db.upsert_episode(slug=SLUG, episode_id=EP, original_url='https://kettle.example/x.mp3', title='X',
                      original_duration=1800.0)
    ads = seg.annotate(db, SLUG, EP, _episode_segments(), [_llm(601.0, 613.0, 'interaction')], 1800.0)
    ads[0]['start'] += 1.0  # a recut moved the edge; the stored span still overlaps
    db.save_episode_details(SLUG, EP, ad_markers=ads)
    db.create_pattern_correction('false_positive', episode_id=EP, podcast_id=_podcast(db)['id'],
                                 original_bounds={'start': ads[0]['start'], 'end': ads[0]['end']})
    seg.sweep(db, _podcast(db))
    assert seg.get_entry(db, entry['id'])['state'] == 'suspended'


def test_a_validated_intro_under_mark_or_keep_stays_in_the_audio(db):
    """T-ACT-1: the action map decides; an annotation never makes a marker a defined pattern."""
    from main_app.pipeline import keep_rules
    marker = {'start': 0.0, 'end': 9.0, 'category': 'intro', 'confidence': 0.75, 'sponsor': None,
              'detection_stage': 'show_segment', 'show_segment': {'entry': 1, 'state': 'validated'}}
    for action in ('mark', 'keep'):
        actions = {**db.resolve_segment_actions(SLUG), 'intro': action}
        kept, remove = keep_rules._partition_keep_ads([dict(marker)], actions)
        assert len(kept) == 1 and remove == [] and kept[0]['action_applied'] == action
    actions = {**db.resolve_segment_actions(SLUG), 'intro': 'remove'}
    kept, remove = keep_rules._partition_keep_ads([dict(marker)], actions)
    assert kept == [] and len(remove) == 1 and not remove[0].get('pattern_defined')


# ---------------------------------------------------------------- review fixes

def test_this_installs_own_published_entry_is_skipped_without_error(db):
    pid = _exportable(db)
    own = seg.build_segment_bundle(db, [pid])[0]['patterns'][0]
    out = _import(db, own)
    assert out['errors'] == 0 and out['inserted'] == 0 and out['skipped'] == 1


def test_an_update_keeps_a_suspension_and_a_filtered_category_is_not_applied(db):
    body = _entry()
    entry = _bound(db, body)
    seg.set_state(db, entry['id'], 'suspend')
    newer = copy.deepcopy(body)
    newer['version'] = 2
    newer['duration']['p90'] = 20.0
    _import(db, newer)
    assert seg.get_entry(db, entry['id'])['state'] == 'suspended'
    moved = copy.deepcopy(newer)
    moved['version'], moved['category'] = 3, 'cross_promo'
    db.set_setting('community_sync_categories', json.dumps(['interaction']))
    _import(db, moved)
    row = seg.get_entry(db, entry['id'])
    assert row['is_active'] == 0 and row['disabled_reason'] == 'category_filtered'


def test_a_revoked_entry_that_returns_is_restored(db):
    a, b, c = _entry(), _entry(), _entry()
    _import(db, a, b, c)
    _import(db, b, c)
    assert seg.get_entries(db, community_id=a['community_id'])[0]['state'] == 'revoked'
    _import(db, a, b, c)
    row = seg.get_entries(db, community_id=a['community_id'])[0]
    assert row['state'] == 'unbound' and row['is_active'] == 1


def test_a_head_without_a_guid_keeps_the_last_known_one(db):
    from datetime import timedelta
    from utils.time import utc_now
    _set_identity(db)
    seg.refresh_identity(db, _podcast(db), lambda url: None, now=utc_now() + timedelta(days=8))
    assert seg.show_keys(db, _podcast(db))[0] == (GUID, 'podcast_guid')


def test_a_reactivated_entry_is_not_suspended_again_by_the_old_reject(db):
    entry = _bound(db, _entry())
    _correct(db, 'false_positive', _hold_marker(db, entry))
    seg.sweep(db, _podcast(db))
    seg.set_state(db, entry['id'], 'reactivate')
    seg.sweep(db, _podcast(db))
    assert seg.get_entry(db, entry['id'])['state'] == 'candidate'


def test_reprocessing_an_episode_keeps_one_hold(db):
    entry = _bound(db, _entry())
    ids = {_annotate(db, [])[0]['hold_id'] for _ in range(3)}
    assert len(ids) == 1 and seg._unresolved(db, entry['id']) == 1


def test_a_short_ask_inside_a_wider_marker_does_not_reshape_it(db):
    lines = [(0.0, 12.0, FILLER + '.'), (600.0, 604.0, SHORT + '.'), (604.0, 616.0, FILLER + '.')]
    _import(db, _entry(short_ask=True, text=SHORT, durations=(3.5, 4.0, 4.5)))
    _set_identity(db)
    for i in range(3):
        eid = f'sw{i:04d}abcd'
        db.upsert_episode(slug=SLUG, episode_id=eid, original_url=f'https://kettle.example/w{i}.mp3',
                          title=f'W {i}', original_duration=1800.0)
        db.save_original_transcript(SLUG, eid, _transcript(lines))
    seg.bind_feed(db, _podcast(db))
    db.set_setting('snip_short_asks', '1')
    wide = _llm(590.0, 650.0, 'self_promo')
    ad = seg.annotate(db, SLUG, EP, _segments(lines), [wide], 1800.0)[0]
    assert (ad['start'], ad['end'], ad['category']) == (590.0, 650.0, 'self_promo')
    assert 'short_ask' not in ad and ad['show_segment']['entry']


def test_a_description_chapter_trim_is_never_touched(db):
    _bound(db, _entry())
    chapter = _llm(598.0, 1800.0, 'outro', detection_stage='description_chapter')
    before = copy.deepcopy(chapter)
    assert _annotate(db, [chapter]) == [before]


def test_a_key_failure_is_loud_but_never_stops_the_episode(db, caplog):
    _bound(db, _entry())
    ads = [_llm(601.0, 613.0, 'interaction')]
    with patch.object(core, 'show_secret', side_effect=core.SegmentKeyError('memory limit exceeded')):
        assert _annotate(db, ads) is ads
    assert any(r.levelname == 'ERROR' and 'show key' in r.getMessage() for r in caplog.records)


def test_a_second_reject_on_the_same_hold_does_not_undo_a_reactivate(db):
    entry = _bound(db, _entry())
    held = _hold_marker(db, entry)
    _correct(db, 'false_positive', held)
    seg.sweep(db, _podcast(db))
    seg.set_state(db, entry['id'], 'reactivate')
    _correct(db, 'false_positive', held)
    seg.sweep(db, _podcast(db))
    seg.sweep(db, _podcast(db))
    assert seg.get_entry(db, entry['id'])['state'] == 'candidate'


def test_a_resolved_hold_is_not_reused_on_reprocess(db):
    entry = _bound(db, _entry())
    first = _annotate(db, [])[0]['hold_id']
    seg.add_event(db, entry['id'], _podcast(db)['id'], EP, 'reject', first, 1)
    assert _annotate(db, [])[0]['hold_id'] != first


def test_a_suggestion_that_becomes_a_disagreement_keeps_its_hold(db):
    _bound(db, _entry())
    first = _annotate(db, [])[0]['hold_id']
    again = _annotate(db, [_llm(600.5, 613.0, 'outro')])[0]
    assert again['hold_reason'] == HOLD_REASON_SHOW_SEGMENT_DISAGREEMENT and again['hold_id'] == first


def test_two_held_markers_of_one_entry_keep_distinct_holds(db):
    entry = _bound(db, _entry())
    pid = _podcast(db)['id']
    for hold in ('aaaa1111bbbb', 'cccc2222dddd'):
        seg.add_event(db, entry['id'], pid, EP, 'disagree', hold)
    used = set()
    ads = [{'start': s, 'end': s + 10.0, 'show_segment': {'hold': HOLD_REASON_SHOW_SEGMENT_DISAGREEMENT}}
           for s in (100.0, 200.0)]
    for ad in ads:
        seg._hold(db, ad, entry['id'], EP, used)
    assert {ad['hold_id'] for ad in ads} == {'aaaa1111bbbb', 'cccc2222dddd'}


def test_a_returning_entry_drops_its_old_binding(db):
    a, b, c = _entry(), _entry(), _entry()
    _import(db, a, b, c)
    row = seg.get_entries(db, community_id=a['community_id'])[0]
    _set_identity(db)
    seg.update_entry(db, row['id'], state='candidate', bound_podcast_id=_podcast(db)['id'])
    _import(db, b, c)
    _import(db, a, b, c)
    row = seg.get_entry(db, row['id'])
    assert row['state'] == 'unbound' and row['is_active'] == 1 and row['bound_podcast_id'] is None


def test_a_returning_entry_with_invalid_new_content_stays_revoked(db):
    a, b, c = _entry(), _entry(), _entry()
    _import(db, a, b, c)
    _import(db, b, c)
    newer = copy.deepcopy(a)
    newer['version'] = 2
    manifest, fetch, _ = _manifest(newer, b, c)
    out = seg.apply_segment_manifest(db, manifest, lambda path: (fetch(path)[0], b'tampered'))
    row = seg.get_entries(db, community_id=a['community_id'])[0]
    assert out['errors'] >= 1 and row['state'] == 'revoked' and row['is_active'] == 0


def test_a_filtered_entry_moved_to_an_accepted_category_is_restored(db):
    body = _entry('cross_promo')
    db.set_setting('community_sync_categories', json.dumps(['interaction', 'cross_promo']))
    _import(db, body)
    db.set_setting('community_sync_categories', json.dumps(['interaction']))
    _import(db, body)
    moved = copy.deepcopy(body)
    moved['version'], moved['category'] = 2, 'interaction'
    out = _import(db, moved)
    row = seg.get_entries(db)[0]
    assert out['updated'] == 1 and row['is_active'] == 1 and row['category'] == 'interaction'


def test_delete_entries_refuses_an_unconditional_delete(db):
    with pytest.raises(ValueError):
        seg.delete_entries(db)
