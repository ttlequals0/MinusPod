"""Pattern cleanup HTTP surface: status, run-now, runs, suggestions, bulk, settings."""
import fcntl
import json
import os
import sys
import tempfile
from unittest.mock import patch

import pytest

from tests.app_bootstrap import authenticate_test_client

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))
os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='pattern-cleanup-api-test-'))

import api  # noqa: E402
import pattern_cleanup  # noqa: E402


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
    assert body['inProgress'] is False
    assert body['lastRun'] is None and body['lastError'] is None and body['lastSummary'] is None
    assert body['pending'] == {'total': 0, 'byKind': {}}


def test_status_reflects_lock_and_pending(app_client, podcast):
    p = _pattern(podcast)
    _suggest(podcast, p, kind='trim')
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


def test_status_surfaces_last_summary(app_client, podcast):
    summary = {'runId': 7, 'status': 'completed'}
    podcast.set_setting('pattern_cleanup_last_run', '2026-01-01T00:00:00Z')
    podcast.set_setting('pattern_cleanup_last_error', '')
    podcast.set_setting('pattern_cleanup_last_summary', json.dumps(summary))
    try:
        body = app_client.get('/api/v1/patterns/cleanup').get_json()
        assert body['lastRun'] == '2026-01-01T00:00:00Z'
        assert body['lastError'] is None
        assert body['lastSummary'] == summary
    finally:
        podcast.clear_setting('pattern_cleanup_last_run')
        podcast.clear_setting('pattern_cleanup_last_error')
        podcast.clear_setting('pattern_cleanup_last_summary')


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
    assert s['reasons'] == ['looks like show content']
    pat = s['pattern']
    assert pat['id'] == p['id'] and pat['sponsor'] == 'Acme' and pat['scope'] == 'podcast'
    assert pat['podcastTitle'] == 'The Daily Tech Show'
    assert pat['confirmationCount'] == 5 and pat['falsePositiveCount'] == 1


def test_suggestions_list_filters_by_kind(app_client, podcast):
    p = _pattern(podcast)
    _suggest(podcast, p, kind='trim')
    q = _pattern(podcast, text='Widgetco ad copy', sponsor='Widgetco')
    _suggest(podcast, q, kind='rename', payload={'sponsor': 'Widgetco'})
    body = app_client.get('/api/v1/patterns/cleanup/suggestions?kind=rename').get_json()
    assert [s['kind'] for s in body['suggestions']] == ['rename']


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
    assert body['status'] == 'approved' and body['applied']['applied_at']


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
               'pattern_cleanup_unused_days', 'pattern_cleanup_provider', 'pattern_cleanup_model'):
        db.clear_setting(key)


def test_put_settings_updates_and_echoes(app_client, podcast, reset_cleanup_settings):
    r = app_client.put('/api/v1/settings/pattern-cleanup', json={
        'enabled': True, 'cron': '0 5 * * 1', 'batchSize': 40, 'unusedDays': 120,
        'provider': 'secondary', 'model': 'claude-opus',
    })
    assert r.status_code == 200
    body = r.get_json()
    assert body == {'enabled': True, 'cron': '0 5 * * 1', 'batchSize': 40, 'unusedDays': 120,
                    'provider': 'secondary', 'model': 'claude-opus'}


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
