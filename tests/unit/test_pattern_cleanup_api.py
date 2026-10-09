"""Pattern cleanup HTTP surface: status, run-now, runs, suggestions, bulk, settings."""
import fcntl
import os
import sys
import tempfile
from datetime import timedelta
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

import pytest

from tests.app_bootstrap import authenticate_test_client

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='pattern-cleanup-api-test-'))

import config_transfer  # noqa: E402
import api  # noqa: E402
import api.settings as api_settings  # noqa: E402
import pattern_cleanup  # noqa: E402
from llm_client import clear_settings_cache  # noqa: E402
from utils.time import ISO_FORMAT, utc_now  # noqa: E402


@pytest.fixture
def hdr(app_client):
    return authenticate_test_client(app_client)


@pytest.fixture
def csrf_required(app_client, hdr):
    """CSRF is only enforced once a password is set (project convention)."""
    db = api.get_database()
    db.set_setting('app_password', 'pbkdf2:sha256:fake-for-tests', is_default=False)
    yield db
    db.clear_setting('app_password')


def _pattern(db, text='This episode is brought to you by Acme.', sponsor='Acme', **fields):
    sponsor_id = None
    if sponsor:
        row = db.get_known_sponsor_by_name(sponsor)
        sponsor_id = row['id'] if row else db.create_known_sponsor(name=sponsor)
    pid = db.create_ad_pattern(scope='podcast', text_template=text, sponsor_id=sponsor_id,
                               podcast_id='show-a', created_by='auto', source='local',
                               created_from_episode_id='ep1')
    if fields:
        db.update_ad_pattern(pid, **fields)
    return db.get_ad_pattern_by_id(pid)


def _suggest(db, pattern, kind='trim', payload=None, confidence=0.9):
    before = pattern_cleanup._before_snapshot(pattern)
    return db.upsert_cleanup_suggestion(None, pattern['id'], kind, confidence, ['looks like show content'],
                                        payload or {'text': 'Acme'}, before)


@pytest.fixture
def podcast(app_client):
    db = api.get_database()
    if not db.get_podcast_by_slug('show-a'):
        db.create_podcast('show-a', 'http://example.com/feed', title='The Daily Tech Show')
    return db


# Status

def test_status_shape_with_defaults(app_client, podcast):
    body = app_client.get('/api/v1/patterns/cleanup').get_json()
    assert body['enabled'] is False
    assert body['cron'] == '0 4 * * 0'
    assert body['batchSize'] == 25
    assert body['unusedDays'] == 90
    assert body['provider'] == '' and body['model'] == ''
    assert body['modelMissing'] is False
    assert body['inProgress'] is False
    assert body['lastRun'] is None and body['lastError'] is None and body['lastSummary'] is None
    assert body['pending'] == {'total': 0, 'byKind': {}}


def test_status_marks_explicitly_cleared_cleanup_model_as_missing(app_client, podcast):
    db = api.get_database()
    db.set_setting('pattern_cleanup_model', '', is_default=False)

    try:
        body = app_client.get('/api/v1/patterns/cleanup').get_json()
    finally:
        db.clear_setting('pattern_cleanup_model')

    assert body['model'] == ''
    assert body['modelMissing'] is True


def test_status_reflects_lock_and_pending(app_client, podcast):
    p = _pattern(podcast)
    _suggest(podcast, p, kind='trim')
    # A running row is required: is_cleanup_running only probes the lock when one exists.
    run_id = podcast.create_cleanup_run(forced=False, trigger='schedule')
    try:
        fd = open(os.path.join(str(podcast.data_dir), pattern_cleanup.LOCK_FILENAME), 'w')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            body = app_client.get('/api/v1/patterns/cleanup').get_json()
            assert body['inProgress'] is True
        finally:
            fd.close()
        assert app_client.get('/api/v1/patterns/cleanup').get_json()['inProgress'] is False
        body = app_client.get('/api/v1/patterns/cleanup').get_json()
        assert body['pending'] == {'total': 1, 'byKind': {'trim': 1}}
    finally:
        podcast.get_connection().execute("DELETE FROM pattern_cleanup_runs WHERE id = ?", (run_id,))
        podcast.get_connection().commit()


def test_status_derives_last_run_from_the_run_table(app_client, podcast):
    done = podcast.create_cleanup_run(forced=False, trigger='manual', started_at='2026-01-01T00:00:00Z')
    podcast.finish_cleanup_run(done, status='failed', reviewed=3, suggested=1, skipped=0,
                               error='provider down', error_count=2)
    running = podcast.create_cleanup_run(forced=False, trigger='schedule',
                                         started_at='2026-01-02T00:00:00Z')
    try:
        body = app_client.get('/api/v1/patterns/cleanup').get_json()
        assert body['lastRun'] == '2026-01-02T00:00:00Z'
        assert body['lastError'] == 'provider down'
        summary = body['lastSummary']
        assert summary['id'] == done and summary['status'] == 'failed'
        assert summary['startedAt'] == '2026-01-01T00:00:00Z' and summary['finishedAt']
        assert (summary['reviewedCount'], summary['suggestedCount'], summary['errorCount']) == (3, 1, 2)
    finally:
        podcast.get_connection().execute(
            "DELETE FROM pattern_cleanup_runs WHERE id IN (?, ?)", (done, running))
        podcast.get_connection().commit()


# Run now (daemon thread + 409/202)

def test_run_accepted_returns_run_id_and_passes_force(app_client, podcast):
    def fake(db, *, force=False, trigger='manual'):
        return db.create_cleanup_run(forced=force, trigger=trigger)
    with patch.object(pattern_cleanup, 'start_cleanup_run', side_effect=fake) as start:
        r = app_client.post('/api/v1/patterns/cleanup/run', json={'force': True})
    assert r.status_code == 202
    run_id = r.get_json()['runId']
    assert run_id is not None
    start.assert_called_once_with(podcast, force=True, trigger='manual')
    row = podcast.get_cleanup_runs(limit=1)[0]
    assert row['id'] == run_id and row['forced'] == 1 and row['trigger'] == 'manual'


def test_run_without_body_defaults_force_false(app_client, podcast):
    def fake(db, *, force=False, trigger='manual'):
        return db.create_cleanup_run(forced=force, trigger=trigger)
    with patch.object(pattern_cleanup, 'start_cleanup_run', side_effect=fake):
        r = app_client.post('/api/v1/patterns/cleanup/run')
    assert r.status_code == 202
    row = podcast.get_cleanup_runs(limit=1)[0]
    assert row['forced'] == 0


def test_run_rejects_non_object_body(app_client, podcast):
    with patch.object(pattern_cleanup, 'start_cleanup_run') as start:
        r = app_client.post('/api/v1/patterns/cleanup/run', json=[1, 2])
    assert r.status_code == 400
    start.assert_not_called()


@pytest.mark.parametrize('body', ['{', 'null', 'false', '1', '"false"'])
def test_run_rejects_invalid_json_without_starting(app_client, podcast, body):
    with patch.object(pattern_cleanup, 'start_cleanup_run') as start:
        response = app_client.post('/api/v1/patterns/cleanup/run',
                                   data=body, content_type='application/json')
    assert response.status_code == 400
    start.assert_not_called()


@pytest.mark.parametrize('force', ['false', 'true', 0, 1, None, [], {}])
def test_run_rejects_non_boolean_force(app_client, podcast, force):
    with patch.object(pattern_cleanup, 'start_cleanup_run') as start:
        response = app_client.post('/api/v1/patterns/cleanup/run', json={'force': force})
    assert response.status_code == 400
    start.assert_not_called()


def test_run_conflict_when_lock_held(app_client, podcast):
    """No mocking: the endpoint's own start_cleanup_run hits the real fcntl lock."""
    fd = open(os.path.join(str(podcast.data_dir), pattern_cleanup.LOCK_FILENAME), 'w')
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        r = app_client.post('/api/v1/patterns/cleanup/run')
    finally:
        fd.close()
    assert r.status_code == 409
    assert r.get_json()['error'] == 'cleanup_in_progress'


def test_run_requires_csrf(app_client, csrf_required):
    r = app_client.post('/api/v1/patterns/cleanup/run', json={})
    assert r.status_code == 403


def test_run_400_does_not_consume_the_rate_limit(app_client, podcast):
    for _ in range(6):
        r = app_client.post('/api/v1/patterns/cleanup/run', json=[1, 2])
        assert r.status_code == 400
    with patch.object(pattern_cleanup, 'start_cleanup_run', return_value=99):
        r = app_client.post('/api/v1/patterns/cleanup/run', json={})
    assert r.status_code == 202


# Runs list

def test_runs_list_shape(app_client, podcast):
    podcast.create_cleanup_run(forced=True, trigger='manual')
    podcast.finish_cleanup_run(podcast.get_cleanup_runs(limit=1)[0]['id'], status='completed',
                               reviewed=2, suggested=1, skipped=0, error_count=1, model='m',
                               provider='anthropic', credential_slot='primary')
    body = app_client.get('/api/v1/patterns/cleanup/runs').get_json()
    run = body['runs'][0]
    assert run['forced'] is True and run['trigger'] == 'manual' and run['status'] == 'completed'
    assert run['reviewedCount'] == 2 and run['suggestedCount'] == 1 and run['skippedCount'] == 0
    assert run['errorCount'] == 1
    assert run['model'] == 'm' and run['provider'] == 'anthropic' and run['credentialSlot'] == 'primary'


def test_runs_list_limit_is_clamped(app_client, podcast):
    for _ in range(3):
        podcast.create_cleanup_run(forced=False, trigger='schedule')
    body = app_client.get('/api/v1/patterns/cleanup/runs?limit=1').get_json()
    assert len(body['runs']) == 1


# Suggestions list

def test_suggestions_list_shape_and_pattern_join(app_client, podcast):
    p = _pattern(podcast, confirmation_count=5, false_positive_count=1)
    _suggest(podcast, p, kind='trim', payload={'text': 'Acme'})
    body = app_client.get('/api/v1/patterns/cleanup/suggestions?status=pending').get_json()
    s = body['suggestions'][0]
    assert s['kind'] == 'trim' and s['status'] == 'pending'
    assert s['payload'] == {'text': 'Acme'}
    assert s['before']['textTemplate'] == p['text_template']
    assert s['before']['isActive'] is True
    assert s['reasons'] == ['looks like show content']
    pat = s['pattern']
    assert pat['id'] == p['id'] and pat['sponsor'] == 'Acme' and pat['scope'] == 'podcast'
    assert pat['podcastTitle'] == 'The Daily Tech Show'
    assert pat['networkId'] is None and pat['isActive'] is True
    assert pat['confirmationCount'] == 5 and pat['falsePositiveCount'] == 1


def test_suggestions_list_filters_by_kind(app_client, podcast):
    p = _pattern(podcast)
    _suggest(podcast, p, kind='trim')
    q = _pattern(podcast, text='Widgetco ad copy', sponsor='Widgetco')
    _suggest(podcast, q, kind='rename', payload={'sponsor': 'Widgetco'})
    body = app_client.get('/api/v1/patterns/cleanup/suggestions?kind=rename').get_json()
    assert [s['kind'] for s in body['suggestions']] == ['rename']


def test_suggestions_list_before_id_pages_past_the_cursor(app_client, podcast):
    # The suggestions table is shared across this module's tests, so assert the cursor's
    # effect on these two ids rather than the exact (polluted) list it returns.
    p = _pattern(podcast)
    older = _suggest(podcast, p, kind='trim', payload={'text': 'Acme'})
    newer = _suggest(podcast, p, kind='rename', payload={'sponsor': 'Acme'})
    body = app_client.get(f'/api/v1/patterns/cleanup/suggestions?before_id={newer}').get_json()
    ids = [s['id'] for s in body['suggestions']]
    assert older in ids
    assert newer not in ids


def test_suggestions_list_rejects_unknown_status_and_kind(app_client, podcast):
    assert app_client.get('/api/v1/patterns/cleanup/suggestions?status=bogus').status_code == 400
    assert app_client.get('/api/v1/patterns/cleanup/suggestions?kind=bogus').status_code == 400


def test_retire_and_flag_payloads_are_camelcase(app_client, podcast):
    r_pattern = _pattern(podcast, text='retire me ' + 'Acme ad copy', sponsor='Acme')
    _suggest(podcast, r_pattern, kind='retire', payload={
        'unused_days': 90, 'last_matched_at': None, 'confirmation_count': 0})
    f_pattern = _pattern(podcast, text='flag me ' + 'Widgetco ad copy', sponsor='Widgetco')
    _suggest(podcast, f_pattern, kind='flag', payload={
        'false_positive_count': 3, 'confirmation_count': 1, 'contaminated': True,
        'contamination_reason': 'mixes show content', 'recommended': 'trim', 'trim_text': 'Widgetco ad copy'})

    body = app_client.get('/api/v1/patterns/cleanup/suggestions').get_json()
    by_kind = {s['kind']: s['payload'] for s in body['suggestions']}

    assert by_kind['retire'] == {'unusedDays': 90, 'lastMatchedAt': None, 'confirmationCount': 0}
    assert by_kind['flag'] == {
        'falsePositiveCount': 3, 'confirmationCount': 1, 'contaminated': True,
        'contaminationReason': 'mixes show content', 'recommended': 'trim',
        'trimText': 'Widgetco ad copy',
    }
    for payload in by_kind.values():
        assert not any('_' in key for key in payload)


# Approve / reject / undo

def test_approve_returns_updated_suggestion(app_client, podcast):
    p = _pattern(podcast)
    sid = _suggest(podcast, p, kind='trim', payload={'text': 'Acme'})
    r = app_client.post(f'/api/v1/patterns/cleanup/suggestions/{sid}/approve')
    assert r.status_code == 200
    body = r.get_json()
    assert body['status'] == 'approved' and body['applied']['appliedAt']
    assert body['applied']['newPatternIds'] == [] and 'applied_at' not in body['applied']


def test_retire_response_uses_boolean_snapshot_states(app_client, podcast):
    p = _pattern(podcast)
    sid = _suggest(podcast, p, kind='retire', payload={'unused_days': 90})
    response = app_client.post(f'/api/v1/patterns/cleanup/suggestions/{sid}/approve')
    assert response.status_code == 200
    body = response.get_json()
    assert body['before']['isActive'] is True
    assert body['applied']['after']['isActive'] is False


def test_approve_missing_suggestion_is_404(app_client, podcast):
    r = app_client.post('/api/v1/patterns/cleanup/suggestions/999999/approve')
    assert r.status_code == 404


def test_approve_twice_is_409_invalid_transition(app_client, podcast):
    p = _pattern(podcast)
    sid = _suggest(podcast, p, kind='trim', payload={'text': 'Acme'})
    app_client.post(f'/api/v1/patterns/cleanup/suggestions/{sid}/approve')
    r = app_client.post(f'/api/v1/patterns/cleanup/suggestions/{sid}/approve')
    assert r.status_code == 409
    assert r.get_json()['error'] == 'invalid_transition'


def test_reject_returns_updated_suggestion(app_client, podcast):
    p = _pattern(podcast)
    sid = _suggest(podcast, p, kind='trim', payload={'text': 'Acme'})
    r = app_client.post(f'/api/v1/patterns/cleanup/suggestions/{sid}/reject')
    assert r.status_code == 200 and r.get_json()['status'] == 'rejected'


def test_undo_requires_approved(app_client, podcast):
    p = _pattern(podcast)
    sid = _suggest(podcast, p, kind='trim', payload={'text': 'Acme'})
    r = app_client.post(f'/api/v1/patterns/cleanup/suggestions/{sid}/undo')
    assert r.status_code == 409 and r.get_json()['error'] == 'invalid_transition'
    app_client.post(f'/api/v1/patterns/cleanup/suggestions/{sid}/approve')
    r = app_client.post(f'/api/v1/patterns/cleanup/suggestions/{sid}/undo')
    assert r.status_code == 200 and r.get_json()['status'] == 'undone'


def test_approve_requires_csrf(app_client, csrf_required):
    p = _pattern(csrf_required)
    sid = _suggest(csrf_required, p, kind='trim', payload={'text': 'Acme'})
    r = app_client.post(f'/api/v1/patterns/cleanup/suggestions/{sid}/approve')
    assert r.status_code == 403


def test_reject_requires_csrf(app_client, csrf_required):
    p = _pattern(csrf_required)
    sid = _suggest(csrf_required, p, kind='trim', payload={'text': 'Acme'})
    r = app_client.post(f'/api/v1/patterns/cleanup/suggestions/{sid}/reject')
    assert r.status_code == 403


def test_undo_requires_csrf(app_client, csrf_required):
    p = _pattern(csrf_required)
    sid = _suggest(csrf_required, p, kind='trim', payload={'text': 'Acme'})
    r = app_client.post(f'/api/v1/patterns/cleanup/suggestions/{sid}/undo')
    assert r.status_code == 403


# Bulk

def test_bulk_approve_returns_per_id_results(app_client, podcast):
    p1 = _pattern(podcast)
    p2 = _pattern(podcast, text='Widgetco ad copy', sponsor='Widgetco')
    sid1 = _suggest(podcast, p1, kind='trim', payload={'text': 'Acme'})
    sid2 = _suggest(podcast, p2, kind='rename', payload={'sponsor': 'Widgetco'})
    r = app_client.post('/api/v1/patterns/cleanup/suggestions/bulk',
                        json={'ids': [sid1, sid2, 999999], 'action': 'approve'})
    assert r.status_code == 200
    results = {res['id']: res for res in r.get_json()['results']}
    assert results[sid1]['status'] == 'approved'
    assert results[sid2]['status'] == 'approved'
    assert results[999999]['error'] == 'not_found'


def test_bulk_reject_then_approve_is_invalid_transition(app_client, podcast):
    p = _pattern(podcast)
    sid = _suggest(podcast, p, kind='trim', payload={'text': 'Acme'})
    app_client.post('/api/v1/patterns/cleanup/suggestions/bulk', json={'ids': [sid], 'action': 'reject'})
    r = app_client.post('/api/v1/patterns/cleanup/suggestions/bulk', json={'ids': [sid], 'action': 'approve'})
    assert r.get_json()['results'][0]['error'] == 'invalid_transition'


@pytest.mark.parametrize('body', [{'ids': [], 'action': 'approve'}, {'ids': 'x', 'action': 'approve'},
                                  {'ids': [1], 'action': 'delete'}, {'action': 'approve'}, {'ids': [1]}])
def test_bulk_rejects_malformed_body(app_client, podcast, body):
    assert app_client.post('/api/v1/patterns/cleanup/suggestions/bulk', json=body).status_code == 400


def test_bulk_requires_csrf(app_client, csrf_required):
    r = app_client.post('/api/v1/patterns/cleanup/suggestions/bulk', json={'ids': [1], 'action': 'approve'})
    assert r.status_code == 403


# Settings PUT

@pytest.fixture
def reset_cleanup_settings():
    yield
    db = api.get_database()
    for key in ('pattern_cleanup_enabled', 'pattern_cleanup_cron', 'pattern_cleanup_batch_size',
               'pattern_cleanup_unused_days', 'pattern_cleanup_provider', 'pattern_cleanup_model',
               'pattern_cleanup_schedule_anchor'):
        db.clear_setting(key)


def test_put_settings_updates_and_echoes(app_client, podcast, reset_cleanup_settings):
    r = app_client.put('/api/v1/settings/pattern-cleanup', json={
        'enabled': True, 'cron': '0 5 * * 1', 'batchSize': 40, 'unusedDays': 120,
        'provider': 'secondary', 'model': 'claude-opus',
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body == {'enabled': True, 'cron': '0 5 * * 1', 'batchSize': 40, 'unusedDays': 120,
                    'provider': 'secondary', 'model': 'claude-opus', 'modelMissing': False}


def test_put_blank_model_selects_inheritance_by_clearing_the_row(
        app_client, podcast, reset_cleanup_settings):
    db = api.get_database()
    db.set_setting('pattern_cleanup_model', 'custom-model', is_default=False)

    response = app_client.put('/api/v1/settings/pattern-cleanup', json={'model': ''})

    assert response.status_code == 200
    assert db.get_setting('pattern_cleanup_model') is None
    assert response.get_json()['model'] == ''
    assert response.get_json()['modelMissing'] is False


def test_put_settings_accepts_slot_aliases(app_client, podcast, reset_cleanup_settings):
    r = app_client.put('/api/v1/settings/pattern-cleanup', json={'provider': 'a'})
    assert r.status_code == 200 and r.get_json()['provider'] == 'primary'


def test_put_settings_blank_provider_clears_override(app_client, podcast, reset_cleanup_settings):
    app_client.put('/api/v1/settings/pattern-cleanup', json={'provider': 'secondary'})
    r = app_client.put('/api/v1/settings/pattern-cleanup', json={'provider': ''})
    assert r.status_code == 200 and r.get_json()['provider'] == ''


def test_put_settings_invalid_cron_is_400(app_client, podcast, reset_cleanup_settings):
    r = app_client.put('/api/v1/settings/pattern-cleanup', json={'cron': 'not a cron'})
    assert r.status_code == 400


@pytest.mark.parametrize('body', ['{', 'null', 'false', '1', '"enabled"', '["enabled"]'])
def test_put_settings_requires_json_object(app_client, podcast, reset_cleanup_settings, body):
    response = app_client.put('/api/v1/settings/pattern-cleanup',
                              data=body, content_type='application/json')
    assert response.status_code == 400
    assert podcast.get_setting('pattern_cleanup_schedule_anchor') is None


@pytest.mark.parametrize('field,value', [
    ('enabled', 'false'), ('enabled', 'true'), ('enabled', 0), ('enabled', 1),
    ('enabled', None), ('enabled', []), ('enabled', {}),
    ('cron', None), ('cron', 1), ('cron', True), ('cron', []), ('cron', {}),
])
def test_put_settings_invalid_types_do_not_save_partial_changes(
        app_client, podcast, reset_cleanup_settings, field, value):
    podcast.set_setting('pattern_cleanup_model', 'original-model')
    response = app_client.put('/api/v1/settings/pattern-cleanup', json={
        'enabled': True, 'model': 'replacement-model', field: value,
    })
    assert response.status_code == 400
    assert podcast.get_setting('pattern_cleanup_model') == 'original-model'
    assert podcast.get_setting_bool('pattern_cleanup_enabled', False) is False
    assert podcast.get_setting('pattern_cleanup_schedule_anchor') is None


@pytest.mark.parametrize('value', [0, 201, 'nope', True])
def test_put_settings_batch_size_out_of_range_is_400(app_client, podcast, reset_cleanup_settings, value):
    r = app_client.put('/api/v1/settings/pattern-cleanup', json={'batchSize': value})
    assert r.status_code == 400


@pytest.mark.parametrize('value', [6, 3651, 'nope'])
def test_put_settings_unused_days_out_of_range_is_400(app_client, podcast, reset_cleanup_settings, value):
    r = app_client.put('/api/v1/settings/pattern-cleanup', json={'unusedDays': value})
    assert r.status_code == 400


def test_put_settings_invalid_provider_is_400(app_client, podcast, reset_cleanup_settings):
    r = app_client.put('/api/v1/settings/pattern-cleanup', json={'provider': 'tertiary'})
    assert r.status_code == 400


def test_put_settings_model_too_long_is_400(app_client, podcast, reset_cleanup_settings):
    r = app_client.put('/api/v1/settings/pattern-cleanup', json={'model': 'x' * 201})
    assert r.status_code == 400


def test_put_settings_requires_csrf(app_client, csrf_required):
    r = app_client.put('/api/v1/settings/pattern-cleanup', json={'enabled': True})
    assert r.status_code == 403


def _clock(dt):
    """Pin 'now' for the settings handler, the service and the run table."""
    def iso():
        return dt.strftime(ISO_FORMAT)
    return (patch.object(pattern_cleanup, 'utc_now', return_value=dt),
            patch.object(pattern_cleanup, 'utc_now_iso', iso),
            patch.object(api_settings, 'utc_now_iso', iso),
            patch('database.pattern_cleanup.utc_now_iso', iso))


def _tick_fires(db) -> bool:
    with patch.object(pattern_cleanup, 'start_cleanup_run', return_value=99) as start, \
            patch.object(pattern_cleanup, '_busy_slots', return_value=0):
        pattern_cleanup.pattern_cleanup_tick(db)
    return start.called


def test_enabling_with_no_prior_run_waits_for_the_next_slot(app_client, podcast, reset_cleanup_settings):
    db = api.get_database()
    r = app_client.put('/api/v1/settings/pattern-cleanup', json={'enabled': True})
    assert r.status_code == 200
    assert db.get_setting('pattern_cleanup_schedule_anchor')
    assert _tick_fires(db) is False


def test_enabling_days_after_a_manual_run_waits_for_the_next_slot(app_client, podcast,
                                                                  reset_cleanup_settings):
    db = api.get_database()
    with patch.object(pattern_cleanup, '_live_route', return_value=None), \
            patch.object(pattern_cleanup, 'select_candidates', return_value=[]):
        summary = pattern_cleanup.run_cleanup(db, trigger='manual')
    later = utc_now() + timedelta(days=10)
    a, b, c, d = _clock(later)
    with a, b, c, d:
        app_client.put('/api/v1/settings/pattern-cleanup', json={'enabled': True})
        assert _tick_fires(db) is False
        assert app_client.get('/api/v1/patterns/cleanup').get_json()['lastRun'] == summary['startedAt']


def test_disable_then_reenable_waits_for_the_next_slot(app_client, podcast, reset_cleanup_settings):
    db = api.get_database()
    app_client.put('/api/v1/settings/pattern-cleanup', json={'enabled': True})
    a, b, c, d = _clock(utc_now() + timedelta(days=10))
    with a, b, c, d:
        app_client.put('/api/v1/settings/pattern-cleanup', json={'enabled': False})
        app_client.put('/api/v1/settings/pattern-cleanup', json={'enabled': True})
        assert _tick_fires(db) is False


@pytest.mark.parametrize('provider,model', [('typesafe', 'configured-model'),
                                          ('systemone-compatible', 'configured-model'),
                                          ('openai-compatible', 'typesafe/jev')])
def test_manual_native_cleanup_rejects_before_force_history_run_or_thread(
        app_client, podcast, hdr, provider, model):
    db = podcast
    original = {key: db.get_setting(key) for key in ('llm_provider', 'pattern_cleanup_model',
                                                     'pattern_cleanup_enabled', 'pattern_cleanup_provider')}
    pattern = _pattern(db)
    db.stamp_pattern_cleanup_reviewed(pattern['id'], pattern_cleanup.review_hash(pattern['text_template'], pattern['sponsor']))
    suggestion_id = _suggest(db, pattern)
    stamp = db.get_ad_pattern_by_id(pattern['id'])['cleanup_reviewed_hash']
    before_runs = db.get_cleanup_runs()
    try:
        db.set_setting('llm_provider', provider)
        db.set_setting('pattern_cleanup_model', model)
        db.set_setting('pattern_cleanup_enabled', 'false')
        db.set_setting('pattern_cleanup_provider', 'primary')
        clear_settings_cache()
        thread = MagicMock()
        with patch.object(pattern_cleanup, 'threading', SimpleNamespace(Thread=thread)), \
                patch.object(db, 'reset_cleanup_force_state') as reset, \
                patch.object(pattern_cleanup, '_run_stats_sweep') as sweep:
            response = app_client.post('/api/v1/patterns/cleanup/run', json={'force': True}, headers=hdr)
            assert response.status_code == 400
            assert 'supported chat provider and model' in response.get_json()['error']
            for invoke in [pattern_cleanup.run_cleanup, pattern_cleanup.start_cleanup_run]:
                with pytest.raises(pattern_cleanup.UnsupportedCleanupRouteError):
                    invoke(db, force=True, trigger='schedule')
            thread.assert_not_called()
            reset.assert_not_called()
            sweep.assert_not_called()
        assert db.get_cleanup_runs() == before_runs
        assert db.get_cleanup_suggestion(suggestion_id)['status'] == 'pending'
        assert db.get_ad_pattern_by_id(pattern['id'])['cleanup_reviewed_hash'] == stamp
    finally:
        for key, value in original.items():
            if value is None:
                db.clear_setting(key)
            else:
                db.set_setting(key, value)
        clear_settings_cache()


@pytest.mark.parametrize('write_path', ['settings-api', 'config-import'])
def test_real_provider_change_invalidates_cache_before_manual_cleanup(app_client, podcast, hdr, write_path):
    db = podcast
    keys = ('llm_provider', 'claude_model', 'pattern_cleanup_model', 'pattern_cleanup_provider',
            'chapters_enabled', 'pattern_cleanup_enabled', 'reviewer_calibration_on_change')
    original = {key: db.get_setting(key) for key in keys}
    try:
        db.set_setting('llm_provider', 'anthropic')
        db.set_setting('claude_model', 'configured-model')
        db.set_setting('pattern_cleanup_model', 'configured-model')
        db.set_setting('pattern_cleanup_provider', 'primary')
        db.set_setting('chapters_enabled', 'false')
        db.set_setting('pattern_cleanup_enabled', 'false')
        db.set_setting('reviewer_calibration_on_change', 'false')
        clear_settings_cache()
        assert pattern_cleanup.resolve_route('pattern_cleanup').provider_key == 'anthropic'
        if write_path == 'settings-api':
            result = app_client.put('/api/v1/settings/ad-detection', json={'llmProvider': 'typesafe'}, headers=hdr)
            assert result.status_code == 200
        else:
            document = config_transfer.export_config(db, '2.98.7')
            document['settings'] = {'llm_provider': 'typesafe'}
            preview = app_client.post('/api/v1/system/config-import/preview',
                                      json={'document': document, 'scope': 'global'}, headers=hdr)
            assert preview.status_code == 200, preview.get_json()
            result = app_client.post('/api/v1/system/config-import',
                                     json={'document': document, 'scope': 'global',
                                           'previewToken': preview.get_json()['previewToken']}, headers=hdr)
            assert result.status_code == 200
        before_runs = db.get_cleanup_runs()
        thread = MagicMock()
        with patch.object(pattern_cleanup, 'threading', SimpleNamespace(Thread=thread)), \
                patch.object(db, 'reset_cleanup_force_state') as reset, \
                patch.object(pattern_cleanup, '_run_stats_sweep') as sweep:
            response = app_client.post('/api/v1/patterns/cleanup/run', json={'force': True}, headers=hdr)
        assert response.status_code == 400
        thread.assert_not_called()
        reset.assert_not_called()
        sweep.assert_not_called()
        assert db.get_cleanup_runs() == before_runs
    finally:
        for key, value in original.items():
            if value is None:
                db.clear_setting(key)
            else:
                db.set_setting(key, value)
        clear_settings_cache()
