"""Background LLM features, probes and model routes with the LLM provider set to none."""
import logging
import os
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='llm-optional-features-test-'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import database  # noqa: E402
import failover  # noqa: E402
import llm_client  # noqa: E402
import main_app  # noqa: E402
import pattern_cleanup  # noqa: E402
import pricing_fetcher  # noqa: E402
import llm_route  # noqa: E402
import rate_limit_hold  # noqa: E402
import tools.reviewer_calibration as calibration  # noqa: E402
from api import get_database  # noqa: E402
from api import system as system_routes  # noqa: E402
from api.settings import provider_phases, providers  # noqa: E402
from tests.integration.openapi_conformance import assert_response_conforms  # noqa: E402
from tests.unit.llm_optional_support import restoring_auth  # noqa: E402


@pytest.fixture
def hdr(app_client):
    """Auth headers; every settings row the test touches is restored afterwards."""
    yield from restoring_auth(app_client)


def _put(client, hdr, payload):
    return client.put('/api/v1/settings/ad-detection', json=payload, headers=hdr)


@pytest.fixture
def provider_none(monkeypatch):
    monkeypatch.setattr(llm_client, 'get_effective_provider', lambda: 'none')


@pytest.fixture
def provider_anthropic(monkeypatch):
    monkeypatch.setattr(llm_client, 'get_effective_provider', lambda: 'anthropic')


@pytest.fixture
def started_threads(monkeypatch):
    """Targets of threads the LLM and provider-settings modules start; none actually runs."""
    started = []

    class RecordingThread:
        def __init__(self, *args, target=None, **kwargs):
            self.target = target

        def start(self):
            started.append(self.target)

    fake = SimpleNamespace(**{**vars(threading), 'Thread': RecordingThread})
    for module in (llm_client, provider_phases):
        monkeypatch.setattr(module, 'threading', fake)
    return started


@pytest.fixture
def no_clients(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError('client requested')
    for name in ('get_llm_client', 'get_client_for_provider', 'create_client_for_provider'):
        monkeypatch.setattr(llm_client, name, boom)
        for module in (providers, provider_phases, rate_limit_hold, calibration.provider_clients):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, boom)


# Pattern cleanup

def _cleanup_db(temp_db):
    temp_db.set_setting('pattern_cleanup_enabled', 'true', is_default=False)
    return temp_db


def test_pattern_cleanup_tick_is_a_noop_under_none(temp_db, provider_none):
    _cleanup_db(temp_db)
    with patch.object(pattern_cleanup, 'start_cleanup_run') as start, \
         patch.object(pattern_cleanup, '_busy_slots', return_value=0):
        assert pattern_cleanup.pattern_cleanup_tick(temp_db) is None
    start.assert_not_called()


def test_pattern_cleanup_tick_still_starts_with_a_provider(temp_db, provider_anthropic):
    _cleanup_db(temp_db)
    with patch.object(pattern_cleanup, 'start_cleanup_run', return_value=5) as start, \
         patch.object(pattern_cleanup, '_busy_slots', return_value=0):
        assert pattern_cleanup.pattern_cleanup_tick(temp_db) == 5
    start.assert_called_once()


def test_pattern_cleanup_run_returns_409_under_none(app_client, hdr, provider_none):
    with patch.object(pattern_cleanup, 'start_cleanup_run') as start:
        response = app_client.post('/api/v1/patterns/cleanup/run', json={}, headers=hdr)
    assert response.status_code == 409
    assert response.get_json()['code'] == 'llm_disabled'
    start.assert_not_called()
    assert_response_conforms('post', '/patterns/cleanup/run', response, status=409)


def test_pattern_cleanup_run_validation_runs_before_the_gate(app_client, hdr, provider_none):
    response = app_client.post('/api/v1/patterns/cleanup/run', json={'force': 'yes'}, headers=hdr)
    assert response.status_code == 400


def test_pattern_cleanup_run_still_starts_with_a_provider(app_client, hdr, provider_anthropic):
    with patch.object(pattern_cleanup, 'start_cleanup_run', return_value=9):
        response = app_client.post('/api/v1/patterns/cleanup/run', json={}, headers=hdr)
    assert response.status_code == 202
    assert response.get_json() == {'runId': 9}


# Reviewer calibration

def _calibration_route():
    return SimpleNamespace(provider_key='anthropic', model_id='m', base_url=None,
                           credential_slot='primary')


@pytest.fixture
def calibration_worker(monkeypatch):
    runs = []
    monkeypatch.setattr(calibration, '_calibration_worker', lambda db: runs.append(1))
    monkeypatch.setattr(calibration, 'calibration_route_revision',
                        lambda: ('rev-new', _calibration_route()))
    monkeypatch.setitem(calibration._CALIBRATION_STATE, 'running', False)
    return runs


def test_calibration_is_not_triggered_under_none(temp_db, provider_none, calibration_worker):
    temp_db.set_setting('reviewer_calibration_on_change', 'true', is_default=False)
    assert calibration.trigger_reviewer_calibration(temp_db, 'rev-old') is None
    assert calibration_worker == []
    assert calibration._CALIBRATION_STATE['running'] is False


def test_calibration_is_still_triggered_with_a_provider(temp_db, provider_anthropic, calibration_worker):
    temp_db.set_setting('reviewer_calibration_on_change', 'true', is_default=False)
    thread = calibration.trigger_reviewer_calibration(temp_db, 'rev-old')
    assert thread is not None
    thread.join(timeout=5)
    assert calibration_worker == [1]


@pytest.fixture
def worker_state(monkeypatch):
    """Calibration worker state with one run due; run_calibration calls are recorded."""
    runs = []
    state = calibration._CALIBRATION_STATE
    monkeypatch.setitem(state, 'running', True)
    monkeypatch.setitem(state, 'revision', 'rev-1')
    monkeypatch.setitem(state, 'route', _calibration_route())
    return runs


def _superseding_run(runs, after_first):
    def run(route=None):
        runs.append(route)
        if len(runs) == 1:
            calibration._CALIBRATION_STATE['revision'] = 'rev-2'
            after_first()
        return {'agreement': 1.0, 'model': 'm'}
    return run


def test_calibration_worker_stops_when_the_provider_becomes_none_mid_run(temp_db, worker_state, monkeypatch):
    monkeypatch.setattr(llm_client, 'get_effective_provider', lambda: 'anthropic')
    monkeypatch.setattr(calibration, 'run_calibration', _superseding_run(
        worker_state, lambda: monkeypatch.setattr(llm_client, 'get_effective_provider', lambda: 'none')))
    calibration._calibration_worker(temp_db)
    assert len(worker_state) == 1
    assert calibration._CALIBRATION_STATE['running'] is False


def test_calibration_worker_still_reruns_a_superseded_revision_with_a_provider(temp_db, worker_state, monkeypatch):
    monkeypatch.setattr(llm_client, 'get_effective_provider', lambda: 'anthropic')
    monkeypatch.setattr(calibration, 'run_calibration', _superseding_run(worker_state, lambda: None))
    monkeypatch.setattr(temp_db, 'set_setting', lambda *args, **kwargs: None)
    calibration._calibration_worker(temp_db)
    assert len(worker_state) == 2
    assert calibration._CALIBRATION_STATE['running'] is False


def test_calibration_command_reports_none_instead_of_raising(provider_none, capsys, no_clients):
    assert calibration.main() == 1
    assert 'none' in capsys.readouterr().out.lower()


def test_calibration_revision_never_raises_under_none(provider_none):
    assert isinstance(calibration.calibration_revision(), str)


# Failover probes

def test_probe_targets_skip_llm_targets_under_none(provider_none):
    assert not [t for t in failover.enabled_probe_targets() if t.startswith('llm:')]
    assert not [t for t in failover.run_probe_targets(None, whisper_required=False)
                if t.startswith('llm:')]
    assert failover.run_probe_targets({'detection': {'credential_slot': 'primary'}},
                                      whisper_required=False) == []


def test_probe_targets_keep_llm_targets_with_a_provider(provider_anthropic):
    assert 'llm:primary' in failover.enabled_probe_targets()
    assert 'llm:primary' in failover.run_probe_targets(None, whisper_required=False)


def test_probe_tick_sends_no_llm_probe_under_none(temp_db, provider_none):
    with patch.object(failover, 'probe_target') as probe:
        assert failover.probe_tick(temp_db, ['llm:primary', 'llm:secondary']) == {}
    probe.assert_not_called()


def test_probe_tick_still_probes_a_whisper_target_under_none(temp_db, provider_none):
    result = {'reachable': True, 'status': 200, 'detail': ''}
    with patch.object(failover, 'probe_target', return_value=result) as probe:
        failover.probe_tick(temp_db, ['llm:primary', 'whisper:active'])
    assert [call.args[0] for call in probe.call_args_list] == ['whisper:active']


def test_probe_tick_still_probes_llm_targets_with_a_provider(temp_db, provider_anthropic):
    result = {'reachable': True, 'status': 200, 'detail': ''}
    with patch.object(failover, 'probe_target', return_value=result) as probe:
        failover.probe_tick(temp_db, ['llm:primary'])
    probe.assert_called_once()


def test_ensure_fresh_probes_skips_llm_targets_under_none(provider_none):
    with patch.object(failover, '_probe_requested_target') as run, \
         patch.object(failover, '_capture_probe_context') as capture:
        failover.ensure_fresh_probes(['llm:primary'])
    run.assert_not_called()
    capture.assert_not_called()


def test_probe_target_sends_nothing_for_an_llm_target_under_none(provider_none):
    with patch.object(failover.provider_probe, 'probe_models_endpoint') as models:
        result = failover.probe_target('llm:primary', {'provider': 'none', 'base_url': None,
                                                       'api_key': 'x'})
    models.assert_not_called()
    assert result['reachable'] is None


def test_probe_state_view_reports_llm_targets_disabled_under_none(app_client, hdr, provider_none):
    stale = {'reachable': True, 'status': 200, 'detail': 'ok', 'checked_at': '2026-01-01T00:00:00Z',
             'healthy_streak': 3, 'failed_streak': 0}
    with patch.object(failover, 'probe_state', return_value=stale):
        response = app_client.post('/api/v1/failover/probe', headers=hdr)
    assert response.status_code == 200
    probes = response.get_json()['probes']
    for name in ('llm-a', 'llm-b', 'llm-failover'):
        assert probes[name]['reachable'] is None
        assert probes[name]['detail'] == 'LLM disabled'
        assert probes[name]['checkedAt'] is None
        assert probes[name]['healthyStreak'] == 0
    assert probes['transcriber']['reachable'] is True
    assert_response_conforms('post', '/failover/probe', response)


def test_probe_state_view_keeps_stored_llm_results_with_a_provider(app_client, hdr, provider_anthropic):
    stale = {'reachable': True, 'status': 200, 'detail': 'ok', 'checked_at': '2026-01-01T00:00:00Z',
             'healthy_streak': 3, 'failed_streak': 0}
    with patch.object(failover, 'probe_state', return_value=stale), \
         patch.object(failover, 'probe_tick'):
        response = app_client.post('/api/v1/failover/probe', headers=hdr)
    assert response.get_json()['probes']['llm-a']['detail'] == 'ok'


# Rate-limit probe

def test_rate_limit_probe_is_a_noop_under_none(temp_db, provider_none, no_clients):
    with patch.object(rate_limit_hold, '_probe_rate_limit') as probe:
        assert rate_limit_hold.probe_rate_limit(temp_db) is False
    probe.assert_not_called()


def test_rate_limit_probe_still_runs_with_a_provider(temp_db, provider_anthropic):
    with patch.object(rate_limit_hold, '_probe_rate_limit', return_value=True) as probe:
        assert rate_limit_hold.probe_rate_limit(temp_db) is True
    probe.assert_called_once()


def test_completion_probe_under_none_is_refused_before_any_client(temp_db, provider_none, no_clients):
    with pytest.raises(llm_client.LLMDisabledError):
        rate_limit_hold._probe_via_completion(temp_db, None, 'primary')


# Model routes

def test_models_list_is_empty_under_none(app_client, hdr, provider_none, no_clients):
    assert _put(app_client, hdr, {'llmProvider': 'none'}).status_code == 200
    response = app_client.get('/api/v1/settings/models', headers=hdr)
    assert response.status_code == 200
    assert response.get_json() == {'models': []}


def test_models_refresh_is_empty_under_none(app_client, hdr, provider_none, no_clients):
    response = app_client.post('/api/v1/settings/models/refresh', json={}, headers=hdr)
    assert response.status_code == 200
    assert response.get_json() == {'models': [], 'count': 0}


def test_models_refresh_secondary_slot_still_answers_under_none(app_client, hdr, provider_none, monkeypatch):
    db = get_database()
    db.set_setting('secondary_provider', 'ollama', is_default=False)
    client = MagicMock()
    client.list_models.return_value = [SimpleNamespace(id='m1', name='m1', created=None)]
    built = []
    monkeypatch.setattr(providers, 'get_client_for_provider',
                        lambda provider, **kw: built.append(provider) or client)
    response = app_client.post('/api/v1/settings/models/refresh', json={'slot': 'secondary'},
                               headers=hdr)
    assert response.status_code == 200
    assert built == ['ollama']
    assert response.get_json()['count'] == 1


def test_models_refresh_primary_still_rebuilds_with_a_provider(app_client, hdr, provider_anthropic, monkeypatch):
    forced = []
    monkeypatch.setattr(providers, 'get_llm_client', lambda force_new=False: forced.append(force_new))
    monkeypatch.setattr(providers, '_current_provider_models', lambda: [{'id': 'm'}])
    response = app_client.post('/api/v1/settings/models/refresh', json={}, headers=hdr)
    assert response.status_code == 200
    assert forced == [True]
    assert response.get_json()['count'] == 1


def test_models_preview_for_a_named_provider_still_answers_under_none(app_client, hdr, provider_none, monkeypatch):
    client = MagicMock()
    client.list_models.return_value = [SimpleNamespace(id='m1', name='m1', created=None)]
    monkeypatch.setattr(providers, 'create_client_for_provider', lambda provider, **kw: client)
    response = app_client.get('/api/v1/settings/models?provider=anthropic', headers=hdr)
    assert response.status_code == 200
    assert [m['id'] for m in response.get_json()['models']] == ['m1']


# Health

def test_health_reports_a_model_as_configured_under_none(app_client, hdr, provider_none):
    db = get_database()
    db.set_setting('claude_model', '', is_default=False)
    assert app_client.get('/api/v1/health').get_json()['checks']['llm_model_configured'] is True


def test_health_still_reports_a_missing_model_with_a_provider(app_client, hdr, provider_anthropic):
    db = get_database()
    db.set_setting('claude_model', '', is_default=False)
    assert app_client.get('/api/v1/health').get_json()['checks']['llm_model_configured'] is False


# Settings save and startup

def test_pricing_refresh_is_skipped_under_none(provider_none, started_threads):
    provider_phases._refresh_after_provider_change()
    assert started_threads == []


def test_pricing_refresh_starts_with_a_provider(provider_anthropic, started_threads):
    provider_phases._refresh_after_provider_change()
    assert started_threads == [provider_phases.force_refresh_pricing]


def test_saving_none_starts_no_probe_or_pricing_thread(app_client, hdr, no_clients, started_threads):
    db = get_database()
    db.set_setting('secondary_provider', 'ollama', is_default=False)
    db.set_setting('secondary_provider_enabled', 'true', is_default=False)
    assert _put(app_client, hdr, {'llmProvider': 'none', 'llmJsonSchemaEnabled': True}).status_code == 200
    assert started_threads == []


def test_secondary_probe_helper_is_skipped_under_none(hdr, provider_none, no_clients, started_threads):
    get_database().set_setting('secondary_provider_enabled', 'true', is_default=False)
    llm_client._clear_provider_cache()
    llm_client.probe_secondary_provider_formats()
    assert started_threads == []


def test_secondary_probe_helper_still_probes_with_a_provider(hdr, provider_anthropic, started_threads):
    get_database().set_setting('secondary_provider_enabled', 'true', is_default=False)
    llm_client._clear_provider_cache()
    llm_client.probe_secondary_provider_formats()
    assert started_threads == [llm_client._probe_secondary_provider_formats]


def test_startup_verification_under_none_logs_one_info_line(provider_none, no_clients, started_threads, caplog):
    import logging
    caplog.set_level(logging.DEBUG, logger='llm_client')
    assert llm_client.verify_llm_connection() is True
    assert started_threads == []
    records = [r for r in caplog.records if r.name == 'llm_client']
    assert [r.levelno for r in records] == [logging.INFO]
    assert 'disabled' in records[0].getMessage().lower()
    assert 'key' not in records[0].getMessage().lower()


def test_startup_logs_one_info_line_and_no_model_complaint_under_none(provider_none, no_clients, started_threads, caplog):
    import logging
    db = MagicMock()
    db.get_setting.return_value = ''
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG, logger='llm_client')
    llm_client.verify_llm_connection()
    main_app._log_missing_model_settings(db)
    noisy = [r for r in caplog.records if r.name in ('llm_client', 'podcast.app') and r.levelno >= logging.WARNING]
    assert noisy == []
    disabled = [r for r in caplog.records if r.levelno == logging.INFO and 'disabled' in r.getMessage().lower()]
    assert len(disabled) == 1


def test_startup_still_reports_missing_models_with_a_provider(provider_anthropic, caplog):
    import logging
    db = MagicMock()
    db.get_setting.return_value = ''
    main_app._log_missing_model_settings(db)
    assert any(r.levelno == logging.ERROR and 'No model configured' in r.getMessage() for r in caplog.records)


def test_startup_verification_with_anthropic_and_no_key_still_warns(provider_anthropic, monkeypatch, caplog):
    import logging
    monkeypatch.setattr(llm_client, 'get_api_key', lambda: None)
    assert llm_client._verify_primary_connection() is False
    assert any(r.levelno == logging.WARNING and 'API key' in r.getMessage() for r in caplog.records)


# Pricing refresh

@pytest.fixture
def pricing_fetches(monkeypatch):
    """Calls to the price catalog fetch; the TTL stamp is stale on any host and restored."""
    calls = []
    monkeypatch.setattr(pricing_fetcher, '_last_fetch',
                        time.monotonic() - pricing_fetcher.PRICING_CACHE_TTL - 1)
    monkeypatch.setattr(pricing_fetcher, 'fetch_pricing_chain', lambda sources: calls.append(sources) or [])
    monkeypatch.setattr(pricing_fetcher, 'get_pricing_sources', lambda provider, base_url='': [{'type': 'litellm'}])
    monkeypatch.setattr(database, 'Database', MagicMock())
    return calls


@pytest.mark.parametrize('force', [False, True])
def test_pricing_refresh_makes_no_request_under_none(pricing_fetches, provider_none, force):
    pricing_fetcher.refresh_pricing_if_stale(force=force)
    assert pricing_fetches == []


def test_stale_pricing_still_fetches_with_a_provider_when_not_forced(pricing_fetches, provider_anthropic):
    pricing_fetcher.refresh_pricing_if_stale()
    assert len(pricing_fetches) == 1


def test_pricing_refresh_still_fetches_with_a_provider(pricing_fetches, provider_anthropic):
    pricing_fetcher.refresh_pricing_if_stale(force=True)
    assert len(pricing_fetches) == 1


def test_pricing_refresh_route_returns_409_under_none(app_client, hdr, provider_none, monkeypatch):
    refreshes = []
    monkeypatch.setattr(system_routes, 'force_refresh_pricing', lambda: refreshes.append(1))
    response = app_client.post('/api/v1/system/model-pricing/refresh', headers=hdr)
    assert response.status_code == 409
    assert response.get_json()['code'] == 'llm_disabled'
    assert refreshes == []
    assert_response_conforms('post', '/system/model-pricing/refresh', response, status=409)


def test_pricing_refresh_route_still_refreshes_with_a_provider(app_client, hdr, provider_anthropic, monkeypatch):
    refreshes = []
    monkeypatch.setattr(system_routes, 'force_refresh_pricing', lambda: refreshes.append(1))
    response = app_client.post('/api/v1/system/model-pricing/refresh', headers=hdr)
    assert response.status_code == 200
    assert refreshes == [1]


def test_failover_overview_reads_llm_probes_as_disabled_under_none(app_client, hdr, provider_none):
    response = app_client.get('/api/v1/failover', headers=hdr)
    assert response.status_code == 200
    probes = response.get_json()['probes']
    for name in ('llm-a', 'llm-b', 'llm-failover'):
        assert probes[name]['reachable'] is None
        assert probes[name]['detail'] == 'LLM disabled'
    assert_response_conforms('get', '/failover', response)


def test_viewing_failover_under_none_leaves_stored_probe_rows_unchanged(app_client, hdr, provider_none):
    db = get_database()
    stored = '{"reachable": true, "status": 200, "detail": "ok", "healthy_streak": 3}'
    db.set_setting('failover_probe:llm:primary', stored, is_default=False)
    raw = {t: db.get_setting(f'failover_probe:{t}') for t in failover.PROBE_TARGETS}
    assert app_client.get('/api/v1/failover', headers=hdr).status_code == 200
    assert {t: db.get_setting(f'failover_probe:{t}') for t in failover.PROBE_TARGETS} == raw
    assert db.get_setting('failover_probe:llm:primary') == stored


@pytest.fixture
def empty_pricing_table():
    """A fresh install's empty model_pricing table; the rows are put back afterwards."""
    conn = get_database().get_connection()
    conn.execute('CREATE TEMP TABLE pricing_saved AS SELECT * FROM model_pricing')
    conn.execute('DELETE FROM model_pricing')
    conn.commit()
    yield
    conn.execute('DELETE FROM model_pricing')
    conn.execute('INSERT INTO model_pricing SELECT * FROM pricing_saved')
    conn.execute('DROP TABLE pricing_saved')
    conn.commit()


def test_model_pricing_is_an_empty_list_under_none(app_client, hdr, provider_none, empty_pricing_table):
    response = app_client.get('/api/v1/system/model-pricing', headers=hdr)
    assert response.status_code == 200
    assert response.get_json() == {'models': []}
    assert_response_conforms('get', '/system/model-pricing', response)


def test_switching_from_none_to_a_provider_refreshes_pricing_once(app_client, hdr, no_clients, started_threads):
    assert _put(app_client, hdr, {'llmProvider': 'none'}).status_code == 200
    assert started_threads == []
    assert _put(app_client, hdr, {'llmProvider': 'anthropic'}).status_code == 200
    assert started_threads.count(provider_phases.force_refresh_pricing) == 1


# One handler and one quiet loop boundary

_PRICING_409 = {'error': 'Pricing refresh is unavailable while the LLM provider is none',
                'status': 409, 'code': 'llm_disabled'}
_CLEANUP_409 = {'error': 'Pattern cleanup is unavailable while the LLM provider is set to None',
                'status': 409, 'code': 'llm_disabled'}


def test_409_bodies_are_unchanged(app_client, hdr, provider_none):
    pricing = app_client.post('/api/v1/system/model-pricing/refresh', headers=hdr)
    cleanup = app_client.post('/api/v1/patterns/cleanup/run', json={}, headers=hdr)
    assert (pricing.status_code, pricing.get_json()) == (409, _PRICING_409)
    assert (cleanup.status_code, cleanup.get_json()) == (409, _CLEANUP_409)


def test_any_route_that_reaches_the_client_factory_answers_409(app_client, provider_none):
    """An unguarded route calling get_llm_client gets the same 409 by construction."""
    app = app_client.application
    with app.test_request_context('/api/v1/patterns/cleanup'):
        with pytest.raises(llm_client.LLMDisabledError) as caught:
            llm_client.get_llm_client()
        response = app.handle_user_exception(caught.value)
    assert response.status_code == 409
    assert response.get_json()['code'] == 'llm_disabled'


def test_a_background_loop_tick_that_reaches_the_factory_is_a_quiet_noop(provider_none, caplog):
    def tick():
        with llm_client.llm_off_is_quiet(llm_client.logger, 'Throwaway tick'):
            llm_client.get_llm_client()
            raise AssertionError('the tick went on after the client was refused')

    with caplog.at_level(logging.DEBUG):
        tick()
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_rate_limit_probe_that_loses_the_provider_mid_tick_is_quiet(temp_db, caplog, monkeypatch):
    def refuse(*args, **kwargs):
        raise llm_client.LLMDisabledError('none')
    monkeypatch.setattr(rate_limit_hold, '_probe_rate_limit', refuse)
    with caplog.at_level(logging.DEBUG):
        assert rate_limit_hold.probe_rate_limit(temp_db) is False
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


# Secondary and frozen routes are ignored while the primary is none

@pytest.mark.parametrize('phase', llm_route.PHASES)
def test_a_phase_routed_to_the_secondary_provider_builds_no_client_under_none(provider_none, monkeypatch, phase):
    built = []
    monkeypatch.setattr(llm_client, '_build_client', lambda *args, **kwargs: built.append(args))
    frozen = {'phase': phase, 'provider_key': 'openai-compatible', 'base_url': 'http://example.com/v1',
              'credential_slot': 'secondary'}
    with pytest.raises(llm_client.LLMDisabledError):
        llm_route.client_for_route(frozen)
    route = llm_route.Route(phase=phase, provider_key='openai-compatible', model_id='m',
                            base_url='http://example.com/v1', slot='secondary',
                            credential_slot='secondary', account_id=None)
    with pytest.raises(llm_client.LLMDisabledError):
        llm_route.client_for_route(route)
    assert built == []


def test_a_route_frozen_before_the_switch_is_refused(provider_anthropic, monkeypatch):
    built = []
    monkeypatch.setattr(llm_client, '_build_client', lambda *args, **kwargs: built.append(args))
    frozen = {'phase': 'detection', 'provider_key': 'anthropic', 'base_url': None, 'credential_slot': 'primary'}
    monkeypatch.setattr(llm_client, 'get_effective_provider', lambda: 'none')
    with pytest.raises(llm_client.LLMDisabledError):
        llm_route.client_for_route(frozen)
    assert built == []


def test_calibration_run_is_refused_under_none(provider_none, no_clients):
    with pytest.raises(llm_client.LLMDisabledError):
        calibration.run_calibration()


def test_calibration_worker_that_loses_the_provider_mid_run_saves_nothing_and_stays_quiet(
        temp_db, worker_state, monkeypatch, caplog):
    def refuse(route=None):
        raise llm_client.LLMDisabledError('none')
    monkeypatch.setattr(calibration, 'run_calibration', refuse)
    temp_db.set_setting('reviewer_calibration_last', '', is_default=True)
    with caplog.at_level(logging.DEBUG):
        calibration._calibration_worker(temp_db)
    assert calibration._CALIBRATION_STATE['running'] is False
    assert not temp_db.get_setting('reviewer_calibration_last')
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
