"""Pattern cleanup service: selection, stats, context, review gate, run, tick, apply/undo."""
import fcntl
import json
import os
import sys
import threading
import time
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import pattern_cleanup  # noqa: E402
from llm_client import ProviderRateLimitedError  # noqa: E402
from llm_route import LiveRoute  # noqa: E402
from pattern_cleanup import (  # noqa: E402
    LOCK_FILENAME,
    CleanupInProgressError,
    SuggestionStateError,
    apply_suggestion,
    pattern_cleanup_tick,
    reject_suggestion,
    review_hash,
    review_pattern,
    run_cleanup,
    select_candidates,
    source_context,
    stats_suggestions,
    undo_suggestion,
)
from utils.time import ISO_FORMAT, utc_now  # noqa: E402

LEAD = ("thanks for listening everyone we have a great show today so stick "
        "around for the interview right after this short break. ")
AD = ("This episode is brought to you by Acme. Acme makes the best widgets "
      "around, visit acme dot com slash show for twenty percent off your "
      "first order today.")
TAIL = " okay and we are back with our guest who just flew in from the coast"
AD2 = ("This episode is also sponsored by Widgetco. Widgetco has amazing deals "
       "this week, check out widgetco dot com right now for big savings.")

LIVE = LiveRoute(route=None, provider='anthropic', credential_slot='primary',
                 model='test-model', timeout=30.0, max_retries=0)


def _iso(dt):
    return dt.strftime(ISO_FORMAT)


def _pattern(db, text=LEAD + AD + TAIL, sponsor='Acme', created_by='auto',
             source='local', podcast_id='show-a', episode_id='ep1', **fields):
    sponsor_id = None
    if sponsor:
        row = db.get_known_sponsor_by_name(sponsor)
        sponsor_id = row['id'] if row else db.create_known_sponsor(name=sponsor)
    pid = db.create_ad_pattern(scope='podcast', text_template=text, sponsor_id=sponsor_id,
                               podcast_id=podcast_id, created_by=created_by, source=source,
                               created_from_episode_id=episode_id,
                               intro_variants=['old intro'], outro_variants=['old outro'])
    if fields:
        db.update_ad_pattern(pid, **fields)
    return db.get_ad_pattern_by_id(pid)


def _reply(**body):
    base = {'action': 'keep', 'text': None, 'sponsor': None, 'pieces': [],
            'contaminated': False, 'contamination_reason': None, 'confidence': 0.9,
            'reasons': ['looks clean']}
    base.update(body)
    return SimpleNamespace(content=json.dumps(base))


def _fake_llm(*replies):
    calls = []
    queue = list(replies)

    def fake(**kwargs):
        calls.append(kwargs)
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            return None, item
        return item, None
    return fake, calls


# Candidate selection

def test_select_candidates_scope_and_reviewed_hash_skip(temp_db):
    learned = _pattern(temp_db)
    stamped = _pattern(temp_db, text=AD2, sponsor='Widgetco')
    _pattern(temp_db, text='manual ' + AD, created_by='user')
    _pattern(temp_db, text='community ' + AD, source='community')
    off = _pattern(temp_db, text='off ' + AD)
    temp_db.update_ad_pattern(off['id'], is_active=0)
    temp_db.stamp_pattern_cleanup_reviewed(stamped['id'], review_hash(AD2, 'Widgetco'))

    ids = [p['id'] for p in select_candidates(temp_db, force=False, batch_size=10)]
    assert ids == [learned['id']]


def test_changed_text_is_selected_again(temp_db):
    p = _pattern(temp_db, text=AD2, sponsor='Widgetco')
    temp_db.stamp_pattern_cleanup_reviewed(p['id'], review_hash(AD2, 'Widgetco'))
    temp_db.update_ad_pattern(p['id'], text_template=AD2 + ' extra words here')
    assert [c['id'] for c in select_candidates(temp_db, force=False, batch_size=10)] == [p['id']]


def test_review_hash_normalizes_whitespace_and_case():
    assert review_hash('A  b\nC', 'Acme') == review_hash('a b c', 'acme')
    assert review_hash('a b c', 'Acme') != review_hash('a b c', 'Other')


def test_force_clears_stamps_and_selects_all(temp_db):
    a = _pattern(temp_db, text=AD2, sponsor='Widgetco')
    temp_db.stamp_pattern_cleanup_reviewed(a['id'], review_hash(AD2, 'Widgetco'))
    assert select_candidates(temp_db, force=False, batch_size=10) == []
    ids = [p['id'] for p in select_candidates(temp_db, force=True, batch_size=10)]
    assert ids == [a['id']]
    assert temp_db.get_ad_pattern_by_id(a['id'])['cleanup_reviewed_hash'] is None


def test_batch_size_and_never_reviewed_first(temp_db):
    old = _pattern(temp_db, text='old ' + AD)
    fresh = _pattern(temp_db, text='fresh ' + AD)
    temp_db.get_connection().execute(
        "UPDATE ad_patterns SET cleanup_reviewed_at = '2020-01-01T00:00:00Z', "
        "cleanup_reviewed_hash = 'stale' WHERE id = ?", (old['id'],))
    temp_db.get_connection().commit()
    assert [p['id'] for p in select_candidates(temp_db, force=False, batch_size=1)] == [fresh['id']]


def test_pattern_with_pending_suggestion_is_not_reselected(temp_db):
    p = _pattern(temp_db)
    temp_db.upsert_cleanup_suggestion(None, p['id'], 'trim', 0.9, [], {'text': AD}, {})
    assert select_candidates(temp_db, force=False, batch_size=10) == []
    assert [c['id'] for c in select_candidates(temp_db, force=True, batch_size=10)] == [p['id']]


# Stats suggestions

def test_retire_when_unused_past_threshold(temp_db):
    long_ago = _iso(utc_now() - timedelta(days=200))
    p = _pattern(temp_db, last_matched_at=_iso(utc_now() - timedelta(days=120)))
    temp_db.get_connection().execute(
        "UPDATE ad_patterns SET created_at = ? WHERE id = ?", (long_ago, p['id']))
    temp_db.get_connection().commit()
    out = stats_suggestions(temp_db, temp_db.get_ad_pattern_by_id(p['id']), 90)
    assert [s['kind'] for s in out] == ['retire']
    assert out[0]['payload']['unused_days'] == 90
    assert out[0]['payload']['last_matched_at'] is not None


def test_no_retire_when_recent_match_or_young_pattern(temp_db):
    long_ago = _iso(utc_now() - timedelta(days=200))
    recent = _pattern(temp_db, last_matched_at=_iso(utc_now() - timedelta(days=10)))
    temp_db.get_connection().execute(
        "UPDATE ad_patterns SET created_at = ? WHERE id = ?", (long_ago, recent['id']))
    temp_db.get_connection().commit()
    young = _pattern(temp_db, text='young ' + AD)
    assert stats_suggestions(temp_db, temp_db.get_ad_pattern_by_id(recent['id']), 90) == []
    assert stats_suggestions(temp_db, young, 90) == []


def test_never_matched_old_pattern_retires(temp_db):
    p = _pattern(temp_db)
    temp_db.get_connection().execute(
        "UPDATE ad_patterns SET created_at = ? WHERE id = ?",
        (_iso(utc_now() - timedelta(days=100)), p['id']))
    temp_db.get_connection().commit()
    out = stats_suggestions(temp_db, temp_db.get_ad_pattern_by_id(p['id']), 90)
    assert out[0]['kind'] == 'retire' and out[0]['payload']['last_matched_at'] is None


@pytest.mark.parametrize('fp,conf,flagged', [(2, 2, True), (3, 1, True), (1, 0, False), (2, 3, False)])
def test_high_false_positive_flag(temp_db, fp, conf, flagged):
    p = _pattern(temp_db, false_positive_count=fp, confirmation_count=conf)
    kinds = [s['kind'] for s in stats_suggestions(temp_db, p, 90)]
    assert (kinds == ['flag']) is flagged
    if flagged:
        payload = stats_suggestions(temp_db, p, 90)[0]['payload']
        assert payload['recommended'] == 'disable'
        assert payload['false_positive_count'] == fp


# Source context

def _seed_episode(db, segments, slug='show-a', episode_id='ep1'):
    if not db.get_podcast_by_slug(slug):
        db.create_podcast(slug, 'http://example.com/feed', title='The Daily Tech Show')
    db.upsert_episode(slug, episode_id, title='Episode', original_url='http://example.com/a.mp3')
    db.save_original_segments(slug, episode_id, segments)


def _segments():
    lines = [f"filler sentence number {i} about the weather and other things" for i in range(30)]
    lines[15] = AD
    return [{'start': i * 10.0, 'end': i * 10.0 + 10.0, 'text': t} for i, t in enumerate(lines)]


def test_source_context_marks_located_span_with_45s_window(temp_db):
    _seed_episode(temp_db, _segments())
    p = _pattern(temp_db, text=AD)
    ctx = source_context(temp_db, p)
    assert ctx is not None
    assert '[[' in ctx and ']]' in ctx
    marked = ctx[ctx.index('[[') + 2:ctx.index(']]')]
    assert 'brought to you by Acme' in marked
    assert 'number 11 ' in ctx and 'number 19 ' in ctx
    assert 'number 9 ' not in ctx and 'number 21 ' not in ctx


def test_source_context_not_found(temp_db):
    _seed_episode(temp_db, _segments())
    p = _pattern(temp_db, text='completely unrelated sponsor copy for a mattress brand nobody mentioned')
    assert source_context(temp_db, p) is None


def test_source_context_without_retained_segments(temp_db):
    p = _pattern(temp_db, text=AD)
    assert source_context(temp_db, p) is None
    assert source_context(temp_db, {**p, 'podcast_id': None}) is None


# Review validation (fake call_llm)

def _review(pattern, reply):
    fake, calls = _fake_llm(reply)
    with patch.object(pattern_cleanup, 'call_llm', fake):
        result = review_pattern(pattern, None, live=LIVE, system_prompt='sys')
    return result, calls


def test_review_call_uses_pattern_cleanup_phase(temp_db):
    p = _pattern(temp_db)
    _, calls = _review(p, _reply())
    kw = calls[0]
    assert kw['phase_key'] == 'pattern_cleanup' and kw['route_phase'] == 'pattern_cleanup'
    assert kw['slug'] is None and kw['episode_id'] is None
    assert kw['provider'] == 'anthropic' and kw['credential_slot'] == 'primary'
    assert kw['model'] == 'test-model' and kw['system_prompt'] == 'sys'
    assert kw['response_format']['type'] in ('json_schema', 'json_object')
    assert AD in kw['prompt']


def test_valid_trim_returns_exact_original_substring(temp_db):
    p = _pattern(temp_db)
    result, _ = _review(p, _reply(action='trim', text=AD, confidence=0.8))
    assert result['action'] == 'trim'
    assert result['text'] == AD
    assert result['text'] in p['text_template']


def test_trim_with_invented_words_is_rejected(temp_db):
    p = _pattern(temp_db)
    invented = AD.replace('the best widgets around', 'award winning premium gadgets for everyone')
    result, _ = _review(p, _reply(action='trim', text=invented))
    assert result is None


def test_small_trim_becomes_keep(temp_db):
    p = _pattern(temp_db, text=AD + ' okay')
    result, _ = _review(p, _reply(action='trim', text=AD))
    assert result['action'] == 'keep'


def test_low_confidence_is_dropped(temp_db):
    p = _pattern(temp_db)
    result, _ = _review(p, _reply(action='trim', text=AD, confidence=0.4))
    assert result is None


def test_valid_split(temp_db):
    p = _pattern(temp_db, text=AD + ' ' + AD2)
    result, _ = _review(p, _reply(action='split', pieces=[
        {'text': AD, 'sponsor': 'Acme'}, {'text': AD2, 'sponsor': 'Widgetco'}]))
    assert result['action'] == 'split'
    assert [x['text'] for x in result['pieces']] == [AD, AD2]
    assert [x['sponsor'] for x in result['pieces']] == ['Acme', 'Widgetco']


def test_split_overlapping_pieces_rejected(temp_db):
    p = _pattern(temp_db, text=AD + ' ' + AD2)
    result, _ = _review(p, _reply(action='split', pieces=[
        {'text': AD, 'sponsor': 'Acme'}, {'text': AD, 'sponsor': 'Acme'}]))
    assert result is None


def test_split_piece_missing_its_sponsor_rejected(temp_db):
    p = _pattern(temp_db, text=AD + ' ' + AD2)
    result, _ = _review(p, _reply(action='split', pieces=[
        {'text': AD, 'sponsor': 'Acme'}, {'text': AD2, 'sponsor': 'Zebra'}]))
    assert result is None


def test_split_piece_not_in_original_rejected(temp_db):
    p = _pattern(temp_db, text=AD + ' ' + AD2)
    result, _ = _review(p, _reply(action='split', pieces=[
        {'text': AD, 'sponsor': 'Acme'},
        {'text': 'Zebra sells shoes, visit zebra dot com for a discount on shoes', 'sponsor': 'Zebra'}]))
    assert result is None


def test_rename_requires_sponsor_in_text(temp_db):
    p = _pattern(temp_db, text=AD, sponsor='Acme Inc')
    ok, _ = _review(p, _reply(action='rename', sponsor='Acme'))
    assert ok['action'] == 'rename' and ok['sponsor'] == 'Acme'
    bad, _ = _review(p, _reply(action='rename', sponsor='Globex'))
    assert bad is None


def test_rename_to_invalid_sponsor_name_rejected(temp_db):
    p = _pattern(temp_db, text=AD + ' this show is hosted on Megaphone')
    bad, _ = _review(p, _reply(action='rename', sponsor='Megaphone'))
    assert bad is None


def test_unparseable_output_yields_none(temp_db):
    p = _pattern(temp_db)
    result, _ = _review(p, SimpleNamespace(content='not json at all'))
    assert result is None


def test_failed_call_raises(temp_db):
    p = _pattern(temp_db)
    fake, _ = _fake_llm(RuntimeError('boom'))
    with patch.object(pattern_cleanup, 'call_llm', fake):
        with pytest.raises(pattern_cleanup.PatternCleanupCallError):
            review_pattern(p, None, live=LIVE, system_prompt='sys')


# run_cleanup

@pytest.fixture
def live_route():
    with patch.object(pattern_cleanup, '_live_route', return_value=LIVE):
        yield


def test_run_cleanup_counts_and_summary(temp_db, live_route):
    trim_p = _pattern(temp_db)
    keep_p = _pattern(temp_db, text=AD2, sponsor='Widgetco')
    replies = {trim_p['id']: _reply(action='trim', text=AD), keep_p['id']: _reply()}

    def fake(**kw):
        pid = trim_p['id'] if LEAD.strip()[:20] in kw['prompt'] else keep_p['id']
        return replies[pid], None
    with patch.object(pattern_cleanup, 'call_llm', fake):
        summary = run_cleanup(temp_db, trigger='manual')

    assert summary['status'] == 'completed'
    assert (summary['reviewed'], summary['suggested'], summary['skipped']) == (2, 1, 0)
    sugg = temp_db.get_cleanup_suggestions(status='pending')
    assert [(s['pattern_id'], s['kind']) for s in sugg] == [(trim_p['id'], 'trim')]
    assert sugg[0]['before']['text_template'] == trim_p['text_template']
    assert temp_db.get_ad_pattern_by_id(keep_p['id'])['cleanup_reviewed_hash'] == review_hash(AD2, 'Widgetco')
    assert temp_db.get_ad_pattern_by_id(trim_p['id'])['cleanup_reviewed_hash'] is None
    run = temp_db.get_cleanup_runs(limit=1)[0]
    assert run['id'] == summary['runId'] and run['started_at'] == summary['startedAt']
    assert run['status'] == 'completed' and run['trigger'] == 'manual' and run['model'] == 'test-model'
    assert run['error'] is None and run['finished_at']


def test_run_skips_llm_when_retire_suggested(temp_db, live_route):
    p = _pattern(temp_db)
    temp_db.get_connection().execute(
        "UPDATE ad_patterns SET created_at = ? WHERE id = ?",
        (_iso(utc_now() - timedelta(days=400)), p['id']))
    temp_db.get_connection().commit()
    fake, calls = _fake_llm(_reply())
    with patch.object(pattern_cleanup, 'call_llm', fake):
        summary = run_cleanup(temp_db)
    assert calls == []
    assert summary['skipped'] == 1 and summary['suggested'] == 1
    assert temp_db.get_cleanup_suggestions()[0]['kind'] == 'retire'


def test_high_fp_flag_carries_trim_text(temp_db, live_route):
    _pattern(temp_db, false_positive_count=3, confirmation_count=1)
    fake, _ = _fake_llm(_reply(action='trim', text=AD))
    with patch.object(pattern_cleanup, 'call_llm', fake):
        run_cleanup(temp_db)
    sugg = temp_db.get_cleanup_suggestions()
    assert [s['kind'] for s in sugg] == ['flag']
    assert sugg[0]['payload']['recommended'] == 'trim'
    assert sugg[0]['payload']['trim_text'] == AD


def test_contaminated_keep_becomes_flag(temp_db, live_route):
    p = _pattern(temp_db, text=AD2, sponsor='Widgetco')
    fake, _ = _fake_llm(_reply(contaminated=True, contamination_reason='mixes show content'))
    with patch.object(pattern_cleanup, 'call_llm', fake):
        run_cleanup(temp_db)
    sugg = temp_db.get_cleanup_suggestions()
    assert sugg[0]['kind'] == 'flag' and sugg[0]['payload']['contaminated'] is True
    assert sugg[0]['payload']['recommended'] == 'disable'
    assert temp_db.get_ad_pattern_by_id(p['id'])['cleanup_reviewed_hash'] is None


def test_per_pattern_error_continues(temp_db, live_route):
    _pattern(temp_db)
    _pattern(temp_db, text=AD2, sponsor='Widgetco')
    fake, calls = _fake_llm(RuntimeError('boom'), _reply())
    with patch.object(pattern_cleanup, 'call_llm', fake):
        summary = run_cleanup(temp_db)
    assert len(calls) == 2
    assert summary['status'] == 'completed'
    assert summary['errors'] == 1 and summary['reviewed'] == 1
    run = temp_db.get_cleanup_runs(limit=1)[0]
    assert run['error_count'] == 1 and run['reviewed_count'] == 1


def test_fatal_error_marks_run_failed(temp_db):
    _pattern(temp_db)
    with patch.object(pattern_cleanup, '_live_route', side_effect=RuntimeError('no model')):
        summary = run_cleanup(temp_db)
    assert summary['status'] == 'failed'
    run = temp_db.get_cleanup_runs(limit=1)[0]
    assert run['status'] == 'failed' and 'no model' in run['error']


def test_rate_limit_aborts_run(temp_db, live_route):
    _pattern(temp_db)
    _pattern(temp_db, text=AD2, sponsor='Widgetco')
    fake, calls = _fake_llm(ProviderRateLimitedError('held', 60.0, provider_key='anthropic'))
    with patch.object(pattern_cleanup, 'call_llm', fake):
        summary = run_cleanup(temp_db)
    assert len(calls) == 1
    assert summary['status'] == 'failed'


def test_failing_call_rotates_the_pattern_back_and_parks_it(temp_db, live_route):
    bad = _pattern(temp_db)
    good = _pattern(temp_db, text=AD2, sponsor='Widgetco')
    calls = []

    def fake(**kw):
        calls.append(kw['prompt'])
        if LEAD.strip()[:20] in kw['prompt']:
            return None, RuntimeError('context too long')
        return _reply(), None
    temp_db.set_setting('pattern_cleanup_batch_size', '1')
    with patch.object(pattern_cleanup, 'call_llm', fake):
        run_cleanup(temp_db)
        assert temp_db.get_ad_pattern_by_id(bad['id'])['cleanup_reviewed_hash'] == 'invalid:1'
        run_cleanup(temp_db)
        assert AD2 in calls[-1]
        assert temp_db.get_ad_pattern_by_id(good['id'])['cleanup_reviewed_hash'] == review_hash(AD2, 'Widgetco')
        run_cleanup(temp_db)
        run_cleanup(temp_db)
    assert temp_db.get_ad_pattern_by_id(bad['id'])['cleanup_reviewed_hash'] == 'invalid'
    assert select_candidates(temp_db, force=False, batch_size=10) == []


def test_three_consecutive_call_errors_abort_the_run(temp_db, live_route):
    for i in range(5):
        _pattern(temp_db, text=f'variant {i} ' + AD)
    fake, calls = _fake_llm(RuntimeError('bad api key'))
    with patch.object(pattern_cleanup, 'call_llm', fake):
        summary = run_cleanup(temp_db)
    assert len(calls) == 3
    assert summary['status'] == 'failed' and 'in a row' in summary['error']
    assert temp_db.get_cleanup_runs(limit=1)[0]['status'] == 'failed'


def test_begin_run_marks_interrupted_rows_failed(temp_db, live_route):
    stale = temp_db.create_cleanup_run(forced=False, trigger='schedule')
    with patch.object(pattern_cleanup, 'select_candidates', return_value=[]):
        summary = run_cleanup(temp_db)
    row = next(r for r in temp_db.get_cleanup_runs() if r['id'] == stale)
    assert row['status'] == 'failed' and row['error'] == 'interrupted' and row['finished_at']
    assert temp_db.get_cleanup_runs(limit=1)[0]['id'] == summary['runId']


def test_status_probe_during_begin_does_not_refuse_the_start(temp_db):
    temp_db.create_cleanup_run(forced=False, trigger='manual')
    real = pattern_cleanup._try_run_lock
    holding = threading.Event()

    def slow(db):
        fd = real(db)
        if threading.current_thread().name == 'probe':
            holding.set()
            time.sleep(0.3)
        return fd
    probe = threading.Thread(target=pattern_cleanup.is_cleanup_running, args=(temp_db,), name='probe')
    with patch.object(pattern_cleanup, '_try_run_lock', side_effect=slow), \
            patch.object(pattern_cleanup, '_execute_run', return_value={}):
        probe.start()
        assert holding.wait(5)
        run_id = pattern_cleanup.start_cleanup_run(temp_db)
        probe.join(5)
    assert isinstance(run_id, int)


def test_segments_decoded_once_per_episode_per_run(temp_db, live_route):
    _seed_episode(temp_db, _segments())
    _pattern(temp_db, text=AD)
    _pattern(temp_db, text='filler sentence number 15 ' + AD)
    fake, calls = _fake_llm(_reply())
    with patch.object(pattern_cleanup, 'call_llm', fake), \
            patch.object(temp_db, 'get_original_segments', wraps=temp_db.get_original_segments) as seg:
        run_cleanup(temp_db)
    assert len(calls) == 2 and seg.call_count == 1
    assert all('[[' in kw['prompt'] for kw in calls)


def test_run_refuses_when_lock_held(temp_db):
    temp_db.create_cleanup_run(forced=False, trigger='schedule')
    fd = open(os.path.join(str(temp_db.data_dir), LOCK_FILENAME), 'w')
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert pattern_cleanup.is_cleanup_running(temp_db) is True
        with pytest.raises(CleanupInProgressError):
            run_cleanup(temp_db)
    finally:
        fd.close()
    assert pattern_cleanup.is_cleanup_running(temp_db) is False


# Tick gating

def test_tick_disabled(temp_db):
    with patch.object(pattern_cleanup, 'run_cleanup') as run:
        assert pattern_cleanup_tick(temp_db) is None
    run.assert_not_called()


def test_tick_not_due(temp_db):
    temp_db.set_setting('pattern_cleanup_enabled', 'true')
    temp_db.set_setting('pattern_cleanup_cron', '0 4 * * 0')
    temp_db.create_cleanup_run(forced=False, trigger='manual',
                               started_at=_iso(utc_now() - timedelta(minutes=1)))
    with patch.object(pattern_cleanup, 'run_cleanup') as run, \
            patch.object(pattern_cleanup, '_busy_slots', return_value=0):
        assert pattern_cleanup_tick(temp_db) is None
    run.assert_not_called()


def test_tick_defers_while_processing(temp_db):
    temp_db.set_setting('pattern_cleanup_enabled', 'true')
    with patch.object(pattern_cleanup, 'run_cleanup') as run, \
            patch.object(pattern_cleanup, '_busy_slots', return_value=1):
        assert pattern_cleanup_tick(temp_db) is None
    run.assert_not_called()


def test_tick_runs_when_due_and_idle(temp_db):
    temp_db.set_setting('pattern_cleanup_enabled', 'true')
    temp_db.create_cleanup_run(forced=False, trigger='manual',
                               started_at=_iso(utc_now() - timedelta(days=8)))
    temp_db.set_setting('pattern_cleanup_schedule_anchor', _iso(utc_now() - timedelta(days=9)))
    with patch.object(pattern_cleanup, 'start_cleanup_run', return_value=7) as start, \
            patch.object(pattern_cleanup, '_busy_slots', return_value=0):
        assert pattern_cleanup_tick(temp_db) == 7
    start.assert_called_once_with(temp_db, trigger='schedule')


def test_tick_returns_none_when_lock_held(temp_db):
    temp_db.set_setting('pattern_cleanup_enabled', 'true')
    fd = open(os.path.join(str(temp_db.data_dir), LOCK_FILENAME), 'w')
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with patch.object(pattern_cleanup, '_busy_slots', return_value=0):
            assert pattern_cleanup_tick(temp_db) is None
    finally:
        fd.close()


def test_tick_returns_promptly_while_run_works_in_background(temp_db):
    temp_db.set_setting('pattern_cleanup_enabled', 'true')
    release = threading.Event()
    entered = threading.Event()

    def slow_run(db, run_id, started_at, *, force, trigger):
        entered.set()
        release.wait(10)
        return {}
    with patch.object(pattern_cleanup, '_execute_run', side_effect=slow_run), \
            patch.object(pattern_cleanup, '_busy_slots', return_value=0):
        t0 = time.monotonic()
        run_id = pattern_cleanup_tick(temp_db)
        assert time.monotonic() - t0 < 2.0
        assert isinstance(run_id, int)
        assert entered.wait(5)
        assert pattern_cleanup.is_cleanup_running(temp_db) is True
        assert pattern_cleanup.start_cleanup_run(temp_db) is None
        release.set()
        deadline = time.monotonic() + 5
        while pattern_cleanup.is_cleanup_running(temp_db) and time.monotonic() < deadline:
            time.sleep(0.05)
    assert pattern_cleanup.is_cleanup_running(temp_db) is False
    assert temp_db.get_cleanup_runs(limit=1)[0]['id'] == run_id


# apply / reject / undo

def _suggest(db, pattern, kind, payload, confidence=0.9):
    before = pattern_cleanup._before_snapshot(db.get_ad_pattern_by_id(pattern['id']))
    return db.upsert_cleanup_suggestion(None, pattern['id'], kind, confidence, ['r'], payload, before)


def test_apply_trim_in_place_and_undo(temp_db):
    p = _pattern(temp_db)
    sid = _suggest(temp_db, p, 'trim', {'text': AD})
    result = apply_suggestion(temp_db, sid)
    after = temp_db.get_ad_pattern_by_id(p['id'])
    assert result['status'] == 'approved'
    assert after['text_template'] == AD
    assert json.loads(after['intro_variants']) != ['old intro']
    assert after['cleanup_reviewed_hash'] == review_hash(AD, 'Acme')
    assert result['applied']['applied_at']

    undone = undo_suggestion(temp_db, sid)
    restored = temp_db.get_ad_pattern_by_id(p['id'])
    assert undone['status'] == 'undone'
    assert restored['text_template'] == p['text_template']
    assert json.loads(restored['intro_variants']) == ['old intro']


def test_apply_refuses_when_pattern_changed(temp_db):
    p = _pattern(temp_db)
    sid = _suggest(temp_db, p, 'trim', {'text': AD})
    temp_db.update_ad_pattern(p['id'], text_template='edited by hand ' + AD)
    with pytest.raises(SuggestionStateError):
        apply_suggestion(temp_db, sid)
    assert temp_db.get_cleanup_suggestion(sid)['status'] == 'pending'


def test_apply_refuses_when_sponsor_changed(temp_db):
    p = _pattern(temp_db)
    sid = _suggest(temp_db, p, 'trim', {'text': AD})
    temp_db.update_ad_pattern(p['id'], sponsor_id=temp_db.create_known_sponsor(name='Globex'))
    with pytest.raises(SuggestionStateError):
        apply_suggestion(temp_db, sid)
    assert temp_db.get_cleanup_suggestion(sid)['status'] == 'pending'


def test_apply_twice_refused(temp_db):
    p = _pattern(temp_db)
    sid = _suggest(temp_db, p, 'trim', {'text': AD})
    apply_suggestion(temp_db, sid)
    with pytest.raises(SuggestionStateError):
        apply_suggestion(temp_db, sid)


def test_apply_split_disables_original_and_undo(temp_db):
    p = _pattern(temp_db, text=AD + ' ' + AD2)
    sid = _suggest(temp_db, p, 'split', {'pieces': [
        {'text': AD, 'sponsor': 'Acme'}, {'text': AD2, 'sponsor': 'Widgetco'}]})
    result = apply_suggestion(temp_db, sid)
    new_ids = result['applied']['new_pattern_ids']
    assert len(new_ids) == 2 and result['applied']['disabled_pattern_id'] == p['id']
    original = temp_db.get_ad_pattern_by_id(p['id'])
    assert original['is_active'] == 0
    assert original['disabled_reason'] == f'Cleanup split into patterns: {new_ids}'
    pieces = [temp_db.get_ad_pattern_by_id(i) for i in new_ids]
    assert [x['sponsor'] for x in pieces] == ['Acme', 'Widgetco']
    for piece in pieces:
        assert piece['scope'] == 'podcast' and piece['podcast_id'] == 'show-a'
        assert piece['created_from_episode_id'] == 'ep1'
        assert piece['created_by'] == 'auto' and piece['is_active'] == 1
        assert piece['cleanup_reviewed_hash']

    undo_suggestion(temp_db, sid)
    assert temp_db.get_ad_pattern_by_id(p['id'])['is_active'] == 1
    for i in new_ids:
        row = temp_db.get_ad_pattern_by_id(i)
        assert row['is_active'] == 0 and row['disabled_reason'] == 'Cleanup undo'


def test_apply_rename_and_undo(temp_db):
    p = _pattern(temp_db, text=AD, sponsor='Acme Inc')
    sid = _suggest(temp_db, p, 'rename', {'sponsor': 'Acme'})
    apply_suggestion(temp_db, sid)
    assert temp_db.get_ad_pattern_by_id(p['id'])['sponsor'] == 'Acme'
    undo_suggestion(temp_db, sid)
    assert temp_db.get_ad_pattern_by_id(p['id'])['sponsor'] == 'Acme Inc'


def test_apply_retire_and_undo(temp_db):
    p = _pattern(temp_db)
    sid = _suggest(temp_db, p, 'retire', {'unused_days': 90, 'last_matched_at': None,
                                          'confirmation_count': 0})
    apply_suggestion(temp_db, sid)
    row = temp_db.get_ad_pattern_by_id(p['id'])
    assert row['is_active'] == 0 and row['disabled_at']
    assert row['disabled_reason'] == 'Cleanup: no matches in 90 days'
    undo_suggestion(temp_db, sid)
    row = temp_db.get_ad_pattern_by_id(p['id'])
    assert row['is_active'] == 1 and row['disabled_reason'] is None and row['disabled_at'] is None


def test_apply_flag_disable_and_flag_trim(temp_db):
    p = _pattern(temp_db, false_positive_count=3)
    sid = _suggest(temp_db, p, 'flag', {'false_positive_count': 3, 'confirmation_count': 0,
                                        'contaminated': False, 'contamination_reason': None,
                                        'recommended': 'disable'})
    apply_suggestion(temp_db, sid)
    row = temp_db.get_ad_pattern_by_id(p['id'])
    assert row['is_active'] == 0 and row['disabled_reason'] == 'Cleanup: false positives'
    undo_suggestion(temp_db, sid)
    assert temp_db.get_ad_pattern_by_id(p['id'])['is_active'] == 1

    q = _pattern(temp_db, text='second ' + LEAD + AD)
    sid2 = _suggest(temp_db, q, 'flag', {'false_positive_count': 3, 'confirmation_count': 0,
                                         'contaminated': True, 'contamination_reason': 'x',
                                         'recommended': 'trim', 'trim_text': AD})
    apply_suggestion(temp_db, sid2)
    row = temp_db.get_ad_pattern_by_id(q['id'])
    assert row['text_template'] == AD and row['is_active'] == 1


def test_apply_retire_refuses_when_matched_again_since_the_suggestion(temp_db):
    p = _pattern(temp_db)
    sid = _suggest(temp_db, p, 'retire', {'unused_days': 90, 'last_matched_at': None,
                                          'confirmation_count': 0})
    temp_db.update_ad_pattern(p['id'], last_matched_at=_iso(utc_now()))
    with pytest.raises(SuggestionStateError):
        apply_suggestion(temp_db, sid)
    assert temp_db.get_cleanup_suggestion(sid)['status'] == 'pending'


def test_apply_flag_refuses_when_confirmation_count_changed(temp_db):
    p = _pattern(temp_db, false_positive_count=3, confirmation_count=0)
    sid = _suggest(temp_db, p, 'flag', {'false_positive_count': 3, 'confirmation_count': 0,
                                        'contaminated': False, 'contamination_reason': None,
                                        'recommended': 'disable'})
    temp_db.update_ad_pattern(p['id'], confirmation_count=1)
    with pytest.raises(SuggestionStateError):
        apply_suggestion(temp_db, sid)
    assert temp_db.get_cleanup_suggestion(sid)['status'] == 'pending'


def test_apply_split_supersedes_other_pending_suggestions(temp_db):
    p = _pattern(temp_db, text=AD + ' ' + AD2)
    split = _suggest(temp_db, p, 'split', {'pieces': [
        {'text': AD, 'sponsor': 'Acme'}, {'text': AD2, 'sponsor': 'Widgetco'}]})
    rename = temp_db.upsert_cleanup_suggestion(
        None, p['id'], 'rename', 0.9, [], {'sponsor': 'Acme'},
        pattern_cleanup._before_snapshot(p))
    apply_suggestion(temp_db, split)
    assert temp_db.get_cleanup_suggestion(rename) is None


def test_apply_retire_supersedes_other_pending_suggestions(temp_db):
    p = _pattern(temp_db)
    retire = _suggest(temp_db, p, 'retire', {'unused_days': 90, 'last_matched_at': None,
                                             'confirmation_count': 0})
    rename = temp_db.upsert_cleanup_suggestion(
        None, p['id'], 'rename', 0.9, [], {'sponsor': 'Acme'},
        pattern_cleanup._before_snapshot(p))
    apply_suggestion(temp_db, retire)
    assert temp_db.get_cleanup_suggestion(rename) is None


def test_apply_flag_trim_does_not_supersede_other_pending_suggestions(temp_db):
    p = _pattern(temp_db, false_positive_count=3)
    flag = _suggest(temp_db, p, 'flag', {'false_positive_count': 3, 'confirmation_count': 0,
                                         'contaminated': False, 'contamination_reason': None,
                                         'recommended': 'trim', 'trim_text': AD})
    rename = temp_db.upsert_cleanup_suggestion(
        None, p['id'], 'rename', 0.9, [], {'sponsor': 'Acme'},
        pattern_cleanup._before_snapshot(p))
    apply_suggestion(temp_db, flag)
    assert temp_db.get_cleanup_suggestion(rename)['status'] == 'pending'


def test_contaminated_trim_on_flagged_pattern_keeps_the_flag_disabled(temp_db, live_route):
    _pattern(temp_db, false_positive_count=3, confirmation_count=1)
    fake, _ = _fake_llm(_reply(action='trim', text=AD, contaminated=True,
                               contamination_reason='mixes show content'))
    with patch.object(pattern_cleanup, 'call_llm', fake):
        run_cleanup(temp_db)
    sugg = temp_db.get_cleanup_suggestions()
    assert [s['kind'] for s in sugg] == ['flag']
    assert sugg[0]['payload']['recommended'] == 'disable'
    assert sugg[0]['payload']['contaminated'] is True
    assert 'trim_text' not in sugg[0]['payload']


def test_is_cleanup_running_skips_the_lock_probe_when_no_running_row(temp_db):
    with patch.object(pattern_cleanup, '_try_run_lock') as probe:
        assert pattern_cleanup.is_cleanup_running(temp_db) is False
    probe.assert_not_called()


def test_is_cleanup_running_ignores_a_stale_running_row(temp_db):
    temp_db.create_cleanup_run(forced=False, trigger='schedule',
                               started_at=_iso(utc_now() - timedelta(hours=7)))
    with patch.object(pattern_cleanup, '_try_run_lock') as probe:
        assert pattern_cleanup.is_cleanup_running(temp_db) is False
    probe.assert_not_called()


def test_is_cleanup_running_probes_the_lock_when_a_running_row_is_fresh(temp_db):
    temp_db.create_cleanup_run(forced=False, trigger='schedule')
    fd = open(os.path.join(str(temp_db.data_dir), LOCK_FILENAME), 'w')
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert pattern_cleanup.is_cleanup_running(temp_db) is True
    finally:
        fd.close()
    assert pattern_cleanup.is_cleanup_running(temp_db) is False


def test_reject_stamps_pattern(temp_db):
    p = _pattern(temp_db)
    sid = _suggest(temp_db, p, 'trim', {'text': AD})
    result = reject_suggestion(temp_db, sid)
    assert result['status'] == 'rejected' and result['reviewed_at']
    assert temp_db.get_ad_pattern_by_id(p['id'])['text_template'] == p['text_template']
    assert temp_db.get_ad_pattern_by_id(p['id'])['cleanup_reviewed_hash'] == review_hash(
        p['text_template'], 'Acme')
    with pytest.raises(SuggestionStateError):
        reject_suggestion(temp_db, sid)


def test_undo_requires_approved(temp_db):
    p = _pattern(temp_db)
    sid = _suggest(temp_db, p, 'trim', {'text': AD})
    with pytest.raises(SuggestionStateError):
        undo_suggestion(temp_db, sid)


def test_missing_suggestion(temp_db):
    with pytest.raises(pattern_cleanup.SuggestionNotFoundError):
        apply_suggestion(temp_db, 9999)


# Review fixes: gate, forced supersede, invalid parking, undo ordering

def test_forced_keep_supersedes_every_pending_kind(temp_db, live_route):
    p = _pattern(temp_db)
    temp_db.upsert_cleanup_suggestion(None, p['id'], 'trim', 0.9, [], {'text': AD}, {})
    temp_db.upsert_cleanup_suggestion(None, p['id'], 'rename', 0.9, [], {'sponsor': 'Acme'}, {})
    fake, _ = _fake_llm(_reply())
    with patch.object(pattern_cleanup, 'call_llm', fake):
        run_cleanup(temp_db, force=True)
    assert temp_db.get_cleanup_suggestions(status='pending') == []


def test_supersede_pending_keeps_decided_rows(temp_db):
    p = _pattern(temp_db)
    done = temp_db.upsert_cleanup_suggestion(None, p['id'], 'trim', 0.9, [], {'text': AD}, {})
    temp_db.set_cleanup_suggestion_status(done, 'rejected')
    temp_db.upsert_cleanup_suggestion(None, p['id'], 'flag', 0.9, [], {}, {})
    assert temp_db.supersede_pending(p['id']) == 1
    assert temp_db.get_cleanup_suggestion(done)['status'] == 'rejected'


def test_trim_to_sponsor_name_only_rejected(temp_db):
    p = _pattern(temp_db)
    result, _ = _review(p, _reply(action='trim', text='Acme.'))
    assert result is None


def test_trim_that_keeps_show_content_and_drops_the_ad_rejected(temp_db):
    p = _pattern(temp_db)
    result, _ = _review(p, _reply(action='trim', text=LEAD.strip()))
    assert result is None


def test_rename_to_word_fragment_rejected(temp_db):
    p = _pattern(temp_db, text=AD + ' pick a category and concatenate your savings today')
    result, _ = _review(p, _reply(action='rename', sponsor='cat'))
    assert result is None


def test_rename_to_substring_of_brand_rejected(temp_db):
    p = _pattern(temp_db, text=AD, sponsor='Widgetco')
    result, _ = _review(p, _reply(action='rename', sponsor='Acm'))
    assert result is None


def test_split_piece_sponsor_inside_longer_word_rejected(temp_db):
    p = _pattern(temp_db, text=AD + ' ' + AD2)
    result, _ = _review(p, _reply(action='split', pieces=[
        {'text': AD, 'sponsor': 'Acm'}, {'text': AD2, 'sponsor': 'Widgetco'}]))
    assert result is None


def test_split_that_drops_the_current_sponsor_rejected(temp_db):
    p = _pattern(temp_db, text=AD2 + ' ' + LEAD + 'Acme is great.', sponsor='Acme')
    result, _ = _review(p, _reply(action='split', pieces=[
        {'text': AD2[:70], 'sponsor': 'Widgetco'}, {'text': AD2[70:], 'sponsor': 'Widgetco'}]))
    assert result is None


def test_trim_without_named_sponsor_must_keep_a_known_sponsor(temp_db):
    p = _pattern(temp_db, sponsor=None)
    temp_db.create_known_sponsor(name='Acme')
    ok, _ = _review(p, _reply(action='trim', text=AD))
    assert ok['action'] == 'trim' and ok['text'] == AD
    bad, _ = _review(p, _reply(action='trim', text=LEAD.strip()))
    assert bad is None


def test_trim_when_sponsor_not_in_text_must_keep_a_known_sponsor(temp_db):
    p = _pattern(temp_db, text=LEAD + TAIL.strip() + ' and more chat about the coast', sponsor='Globex')
    bad, _ = _review(p, _reply(action='trim', text=LEAD.strip()))
    assert bad is None


def test_rename_rejects_the_podcast_title(temp_db):
    _seed_episode(temp_db, _segments())
    _pattern(temp_db, text=AD + ' you are listening to The Daily Tech Show', sponsor='Acme Inc')
    p = select_candidates(temp_db, force=False, batch_size=1)[0]
    assert p['podcast_title'] == 'The Daily Tech Show'
    result, _ = _review(p, _reply(action='rename', sponsor='The Daily Tech Show'))
    assert result is None


def test_rename_sponsor_rejects_show_name(temp_db):
    p = _pattern(temp_db, text=AD + ' thanks to show-a listeners', podcast_id='show-a')
    result, _ = _review(p, _reply(action='rename', sponsor='show-a'))
    assert result is None


def test_trim_with_one_inserted_word_does_not_pull_show_words_back(temp_db):
    p = _pattern(temp_db)
    padded = AD.replace('the best widgets', 'the very best widgets')
    result, _ = _review(p, _reply(action='trim', text=padded))
    assert result['action'] == 'trim'
    assert result['text'] == AD


def test_three_invalid_reviews_park_the_pattern_until_force(temp_db, live_route):
    p = _pattern(temp_db)
    fake, _ = _fake_llm(SimpleNamespace(content='not json'))
    with patch.object(pattern_cleanup, 'call_llm', fake):
        for expected in ('invalid:1', 'invalid:2', 'invalid'):
            run_cleanup(temp_db)
            assert temp_db.get_ad_pattern_by_id(p['id'])['cleanup_reviewed_hash'] == expected
    assert select_candidates(temp_db, force=False, batch_size=10) == []
    assert [c['id'] for c in select_candidates(temp_db, force=True, batch_size=10)] == [p['id']]


def test_rename_then_flag_then_undo_rename_refused_until_flag_undone(temp_db):
    p = _pattern(temp_db, text=AD, sponsor='Acme Inc')
    rename = _suggest(temp_db, p, 'rename', {'sponsor': 'Acme'})
    apply_suggestion(temp_db, rename)
    flag = _suggest(temp_db, p, 'flag', {'false_positive_count': 3, 'confirmation_count': 0,
                                         'contaminated': False, 'contamination_reason': None,
                                         'recommended': 'disable'})
    apply_suggestion(temp_db, flag)
    with pytest.raises(SuggestionStateError):
        undo_suggestion(temp_db, rename)
    undo_suggestion(temp_db, flag)
    row = temp_db.get_ad_pattern_by_id(p['id'])
    assert row['is_active'] == 1 and row['sponsor'] == 'Acme'
    undo_suggestion(temp_db, rename)
    row = temp_db.get_ad_pattern_by_id(p['id'])
    assert row['is_active'] == 1 and row['sponsor'] == 'Acme Inc'


def test_trim_then_retire_then_undo_retire_keeps_the_trim(temp_db):
    p = _pattern(temp_db)
    trim = _suggest(temp_db, p, 'trim', {'text': AD})
    apply_suggestion(temp_db, trim)
    retire = _suggest(temp_db, p, 'retire', {'unused_days': 90, 'last_matched_at': None,
                                             'confirmation_count': 0})
    apply_suggestion(temp_db, retire)
    undo_suggestion(temp_db, retire)
    row = temp_db.get_ad_pattern_by_id(p['id'])
    assert row['is_active'] == 1 and row['text_template'] == AD


def test_flag_then_split_refused_on_disabled_pattern(temp_db):
    p = _pattern(temp_db, text=AD + ' ' + AD2, false_positive_count=3)
    flag = _suggest(temp_db, p, 'flag', {'false_positive_count': 3, 'confirmation_count': 0,
                                         'contaminated': False, 'contamination_reason': None,
                                         'recommended': 'disable'})
    split = _suggest(temp_db, p, 'split', {'pieces': [
        {'text': AD, 'sponsor': 'Acme'}, {'text': AD2, 'sponsor': 'Widgetco'}]})
    apply_suggestion(temp_db, flag)
    # The disable superseded (deleted) the still-pending split suggestion.
    with pytest.raises(pattern_cleanup.SuggestionNotFoundError):
        apply_suggestion(temp_db, split)


def test_split_undo_refused_when_a_piece_changed(temp_db):
    p = _pattern(temp_db, text=AD + ' ' + AD2)
    sid = _suggest(temp_db, p, 'split', {'pieces': [
        {'text': AD, 'sponsor': 'Acme'}, {'text': AD2, 'sponsor': 'Widgetco'}]})
    new_ids = apply_suggestion(temp_db, sid)['applied']['new_pattern_ids']
    temp_db.update_ad_pattern(new_ids[0], is_active=0)
    with pytest.raises(SuggestionStateError):
        undo_suggestion(temp_db, sid)
    temp_db.update_ad_pattern(new_ids[0], is_active=1)
    piece = temp_db.get_ad_pattern_by_id(new_ids[1])
    rename = _suggest(temp_db, piece, 'rename', {'sponsor': 'Widgetco'})
    apply_suggestion(temp_db, rename)
    with pytest.raises(SuggestionStateError):
        undo_suggestion(temp_db, sid)


def test_split_with_invalid_piece_sponsor_refused(temp_db):
    p = _pattern(temp_db, text=AD + ' ' + AD2)
    sid = _suggest(temp_db, p, 'split', {'pieces': [
        {'text': AD, 'sponsor': 'Acme'}, {'text': AD2, 'sponsor': 'Megaphone'}]})
    with pytest.raises(SuggestionStateError):
        apply_suggestion(temp_db, sid)
    assert temp_db.get_ad_pattern_by_id(p['id'])['is_active'] == 1


def test_status_write_failure_marks_run_failed(temp_db, live_route):
    _pattern(temp_db, text=AD2, sponsor='Widgetco')
    real_finish = temp_db.finish_cleanup_run
    attempts = []

    def flaky(run_id, **kw):
        attempts.append(kw['status'])
        if len(attempts) == 1:
            raise RuntimeError('disk full')
        return real_finish(run_id, **kw)
    fake, _ = _fake_llm(_reply())
    with patch.object(pattern_cleanup, 'call_llm', fake), \
            patch.object(temp_db, 'finish_cleanup_run', side_effect=flaky):
        summary = run_cleanup(temp_db)
    assert summary['status'] == 'failed'
    run = temp_db.get_cleanup_runs(limit=1)[0]
    assert run['status'] == 'failed' and 'disk full' in run['error']
