"""Tests for the reviewer calibration self-test."""
import json
import threading
import time
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from tests.app_bootstrap import bootstrap, ensure_model_configured

_data_dir = bootstrap('reviewer_calibration_test_')

from database import Database  # noqa: E402
from main_app import app  # noqa: E402
import tools.reviewer_calibration as calib_mod  # noqa: E402
from tools.reviewer_calibration import (  # noqa: E402
    CALIBRATION_AGREEMENT_THRESHOLD,
    CALIBRATION_CORPUS,
    calibration_revision,
    main,
    run_calibration,
    trigger_reviewer_calibration,
)


@dataclass
class _LLMResp:
    """Matches the LLMResponse dataclass shape (content is a string)."""
    content: str
    model: str = "test-model"


def _resp(body: str) -> _LLMResp:
    return _LLMResp(content=body)


def _build_db():
    db = Database()
    ensure_model_configured(db)
    return db


@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _wait_until(predicate, timeout=5.0):
    """Poll until predicate() is true; returns whether it became true."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class _FakeCalibrationRunner:
    """run_calibration stand-in that records routes and can block mid-run."""

    def __init__(self):
        self.routes = []
        self.blocking = False
        self.release = threading.Event()
        self._lock = threading.Lock()

    def __call__(self, llm_client=None, model=None, route=None):
        with self._lock:
            self.routes.append(route)
        if self.blocking:
            self.release.wait(timeout=5)
        return {
            'model': route.model_id if route is not None else model,
            'provider': 'anthropic', 'cases': [], 'agreement': 0.9,
            'structured_fraction': 0.5, 'ran_at': '2026-09-16T00:00:00Z',
        }

    @property
    def runs(self):
        with self._lock:
            return len(self.routes)

    @property
    def models(self):
        with self._lock:
            return [r.model_id for r in self.routes if r is not None]

    def wait_idle(self, timeout=5.0):
        """Wait for the worker to finish and stop holding the running flag."""
        return _wait_until(
            lambda: not calib_mod._CALIBRATION_STATE['running'], timeout)


@pytest.fixture
def calibration_runs(monkeypatch):
    """Fake calibration runner with the module scheduler state reset."""
    runner = _FakeCalibrationRunner()
    monkeypatch.setattr(calib_mod, 'run_calibration', runner)
    calib_mod._CALIBRATION_STATE.update(revision=None, route=None, running=False)
    Database().set_setting('reviewer_calibration_on_change', 'true', is_default=False)
    yield runner
    runner.release.set()
    runner.wait_idle()
    calib_mod._CALIBRATION_STATE.update(revision=None, route=None, running=False)


def _save_settings(client, payload):
    return client.put('/api/v1/settings/ad-detection',
                      data=json.dumps(payload), content_type='application/json')


# Canned responses, one per CALIBRATION_CORPUS case in order. Case 8
# (topic_transition, expected "drop") comes back adjusted instead of
# rejected: the deliberate disagreement, 7/8 agreement = 0.875.
# is_ad appears on cases 1, 3, 6, 7: structured_fraction = 4/8 = 0.5.
_CANNED_RESPONSES = [
    '[{"start": 50.0, "end": 80.0, "confidence": 0.95, "is_ad": true, '
    '"reason": "Clear paid sponsor read for Acme Mattress"}]',
    '[{"start": 50.0, "end": 80.0, "confidence": 0.9, '
    '"reason": "Sponsor read for Brightleaf Coffee"}]',
    '[{"start": 48.0, "end": 80.0, "confidence": 0.9, "is_ad": true, '
    '"reason": "Nimbus VPN ad; earlier start captures the full intro line"}]',
    '[{"start": 50.0, "end": 80.0, "confidence": 0.92, '
    '"reason": "Harborline Insurance sponsor read"}]',
    '[]',
    '[{"is_ad": false, "start": 50.0, "end": 80.0, '
    '"reason": "Editorial discussion about Cascade Bikes, not a paid ad"}]',
    '[{"is_ad": false, "start": 50.0, "end": 80.0, '
    '"reason": "Comedic bit, not a real advertisement"}]',
    '[{"start": 52.0, "end": 78.0, "confidence": 0.7, '
    '"reason": "Looks like a sponsor mention"}]',
]


def _build_calibrated_client():
    client = MagicMock()
    client.messages_create.side_effect = [_resp(b) for b in _CANNED_RESPONSES]
    client.get_provider_name.return_value = 'anthropic'
    return client


def test_corpus_has_8_cases_four_ads_four_non_ads():
    assert len(CALIBRATION_CORPUS) == 8
    keep = [c for c in CALIBRATION_CORPUS if c['expected'] == 'keep']
    drop = [c for c in CALIBRATION_CORPUS if c['expected'] == 'drop']
    assert len(keep) == 4
    assert len(drop) == 4
    ids = [c['id'] for c in CALIBRATION_CORPUS]
    assert len(set(ids)) == 8


def test_run_calibration_agreement_and_structured_fraction():
    _build_db()
    client = _build_calibrated_client()
    result = run_calibration(llm_client=client, model='test-model')
    assert result['agreement'] == 0.875
    assert result['structured_fraction'] == 0.5
    assert len(result['cases']) == 8
    assert result['model'] == 'test-model'
    assert result['provider'] == 'anthropic'
    assert 'ran_at' in result


def test_run_calibration_case_shape_and_disagreement():
    _build_db()
    client = _build_calibrated_client()
    result = run_calibration(llm_client=client, model='test-model')
    last = result['cases'][-1]
    assert last['id'] == 'topic_transition'
    assert last['expected'] == 'drop'
    assert last['verdict'] == 'adjust'
    assert last['agree'] is False
    assert last['structured'] is False
    first = result['cases'][0]
    assert first['id'] == 'acme_mattress_ad'
    assert first['agree'] is True
    assert first['structured'] is True


def test_cli_exits_zero_above_threshold(monkeypatch, capsys):
    canned = {
        'model': 'test-model', 'provider': 'anthropic',
        'cases': [{'id': 'a', 'expected': 'keep', 'verdict': 'confirmed', 'agree': True, 'structured': True}],
        'agreement': 0.875, 'structured_fraction': 1.0, 'ran_at': '2026-08-19T00:00:00Z',
    }
    monkeypatch.setattr('tools.reviewer_calibration.run_calibration', lambda **kw: canned)
    code = main()
    assert code == 0
    out = capsys.readouterr().out
    assert '|' in out
    assert 'a' in out


def test_cli_exits_one_below_threshold(monkeypatch, capsys):
    canned = {
        'model': 'test-model', 'provider': 'anthropic',
        'cases': [{'id': 'a', 'expected': 'keep', 'verdict': 'reject', 'agree': False, 'structured': False}],
        'agreement': 0.5, 'structured_fraction': 0.0, 'ran_at': '2026-08-19T00:00:00Z',
    }
    monkeypatch.setattr('tools.reviewer_calibration.run_calibration', lambda **kw: canned)
    code = main()
    assert code == 1


def test_calibration_agreement_threshold_is_075():
    assert CALIBRATION_AGREEMENT_THRESHOLD == 0.75


# ---------- Settings auto-run hook ----------

def test_trigger_calibration_persists_a_result_for_a_new_revision(calibration_runs):
    db = _build_db()
    db.clear_setting('reviewer_calibration_last')
    # An explicit review slot is what makes review_model the route's model;
    # same_as_pass inherits the pass model instead.
    db.set_setting('review_provider', 'primary', is_default=False)
    db.set_setting('review_model', 'new-model', is_default=False)

    thread = trigger_reviewer_calibration(db, 'a-stale-revision')
    assert thread is not None
    thread.join(timeout=5)

    stored = json.loads(db.get_setting('reviewer_calibration_last'))
    assert stored['model'] == 'new-model'
    assert stored['revision'] == calibration_revision()


def test_trigger_calibration_noop_when_the_route_is_unchanged(calibration_runs):
    db = _build_db()
    db.clear_setting('reviewer_calibration_last')
    assert trigger_reviewer_calibration(db, calibration_revision()) is None
    assert calibration_runs.runs == 0
    assert db.get_setting('reviewer_calibration_last') is None


def test_trigger_calibration_gated_off_by_setting(calibration_runs):
    db = _build_db()
    db.set_setting('reviewer_calibration_on_change', 'false', is_default=False)
    db.clear_setting('reviewer_calibration_last')
    assert trigger_reviewer_calibration(db, 'a-stale-revision') is None
    assert calibration_runs.runs == 0
    assert db.get_setting('reviewer_calibration_last') is None


def test_trigger_calibration_swallows_a_failing_run(calibration_runs, monkeypatch):
    db = _build_db()
    db.clear_setting('reviewer_calibration_last')

    def _boom(**kw):
        raise RuntimeError('llm unreachable')

    monkeypatch.setattr(calib_mod, 'run_calibration', _boom)
    thread = trigger_reviewer_calibration(db, 'a-stale-revision')
    assert thread is not None
    thread.join(timeout=5)

    # Failure never blocks/raises and never writes a stale/partial result.
    assert db.get_setting('reviewer_calibration_last') is None
    assert calib_mod._CALIBRATION_STATE['running'] is False


def test_reviewer_calibration_on_change_defaults_true():
    db = _build_db()
    db.clear_setting('reviewer_calibration_on_change')
    assert db.get_setting_bool('reviewer_calibration_on_change', True) is True


def test_settings_save_calibrates_the_committed_review_model_once(client, calibration_runs):
    db = _build_db()
    db.set_setting('review_provider', 'primary', is_default=False)
    db.set_setting('review_model', 'old-model', is_default=False)

    resp = _save_settings(client, {'reviewModel': 'new-model'})
    assert resp.status_code == 200
    assert calibration_runs.wait_idle()
    assert calibration_runs.models == ['new-model']
    assert db.get_setting('review_model') == 'new-model'


def test_settings_save_calibrates_the_pass_model_when_review_is_same_as_pass(
        client, calibration_runs):
    # review_model 'same_as_pass' means the detection model is the effective
    # reviewer model, so changing it must calibrate.
    db = _build_db()
    db.clear_setting('review_provider')
    db.set_setting('review_model', 'same_as_pass', is_default=False)
    db.set_setting('claude_model', 'old-model', is_default=False)

    resp = _save_settings(client, {'claudeModel': 'new-model'})
    assert resp.status_code == 200
    assert calibration_runs.wait_idle()
    assert calibration_runs.models == ['new-model']


def test_settings_save_skips_calibration_when_the_review_route_is_unchanged(
        client, calibration_runs):
    db = _build_db()
    db.set_setting('review_provider', 'primary', is_default=False)
    db.set_setting('review_model', 'reviewer-model', is_default=False)
    db.set_setting('claude_model', 'old-model', is_default=False)

    resp = _save_settings(client, {'claudeModel': 'new-model'})
    assert resp.status_code == 200
    assert calibration_runs.wait_idle()
    assert calibration_runs.runs == 0


def test_changing_both_models_calibrates_exactly_once(client, calibration_runs):
    """The reviewer inherits the pass model, so both fields are one revision."""
    db = _build_db()
    db.clear_setting('review_provider')
    db.set_setting('review_model', 'same_as_pass', is_default=False)
    db.set_setting('claude_model', 'old-claude', is_default=False)

    resp = _save_settings(client, {'claudeModel': 'new-claude',
                                   'reviewModel': 'same_as_pass'})
    assert resp.status_code == 200
    assert calibration_runs.wait_idle()
    assert calibration_runs.models == ['new-claude']


def test_a_save_rejected_after_the_review_phase_calibrates_nothing(
        client, calibration_runs):
    """The payload rolls back whole, so there is no saved model to self-test."""
    db = _build_db()
    db.set_setting('review_provider', 'primary', is_default=False)
    db.set_setting('review_model', 'old-model', is_default=False)
    db.set_setting('claude_model', 'old-claude', is_default=False)

    # detectionProvider is validated in a phase after both model phases.
    resp = _save_settings(client, {
        'reviewModel': 'new-model',
        'claudeModel': 'new-claude',
        'detectionProvider': 'not-a-slot',
    })

    assert resp.status_code == 400
    assert db.get_setting('review_model') == 'old-model'
    assert db.get_setting('claude_model') == 'old-claude'
    assert calibration_runs.wait_idle()
    assert calibration_runs.runs == 0


def test_a_request_rejected_before_the_review_phase_calibrates_nothing(
        client, calibration_runs):
    """Nothing was saved, so there is nothing to self-test."""
    db = _build_db()
    db.set_setting('review_provider', 'primary', is_default=False)
    db.set_setting('review_model', 'old-model', is_default=False)

    resp = _save_settings(client, {
        'reviewModel': 'new-model',
        'adAddressingMode': 'not-a-mode',
    })

    assert resp.status_code == 400
    assert db.get_setting('review_model') == 'old-model'
    assert calibration_runs.runs == 0


def test_a_save_during_a_run_supersedes_that_run_result(client, calibration_runs):
    """One worker per configuration: the superseded result is discarded."""
    db = _build_db()
    db.set_setting('review_provider', 'primary', is_default=False)
    db.set_setting('review_model', 'old-model', is_default=False)
    db.clear_setting('reviewer_calibration_last')
    calibration_runs.blocking = True

    assert _save_settings(client, {'reviewModel': 'first-model'}).status_code == 200
    assert _wait_until(lambda: calibration_runs.runs == 1)

    # The second save lands while the first run is still inside the barrier.
    assert _save_settings(client, {'reviewModel': 'second-model'}).status_code == 200
    assert calibration_runs.runs == 1

    calibration_runs.release.set()
    assert calibration_runs.wait_idle()
    assert calibration_runs.models == ['first-model', 'second-model']
    stored = json.loads(db.get_setting('reviewer_calibration_last'))
    assert stored['model'] == 'second-model'
    assert stored['revision'] == calibration_revision()


def test_calibration_routes_to_the_review_slot_not_the_global_client():
    """Regression: calibration must build the review slot's client and use
    its model, not send the review model to the global/primary endpoint."""
    from unittest.mock import patch
    from config import OPENROUTER_BASE_URL
    db = _build_db()
    keys = {
        'secondary_provider_enabled': 'true', 'secondary_provider': 'openrouter',
        'review_provider': 'secondary', 'review_model': 'anthropic/claude-opus-5',
    }
    for k, v in keys.items():
        db.set_setting(k, v, is_default=False)
    client = _build_calibrated_client()
    captured = {}

    def fake_get_client(provider_key, base_url=None, credential_slot='primary', **kw):
        captured.update(provider_key=provider_key, base_url=base_url,
                        credential_slot=credential_slot)
        return client

    try:
        with patch('llm_client.get_client_for_provider', side_effect=fake_get_client):
            result = run_calibration()
        assert captured['provider_key'] == 'openrouter'
        assert captured['credential_slot'] == 'secondary'
        assert captured['base_url'] == OPENROUTER_BASE_URL
        assert result['model'] == 'anthropic/claude-opus-5'
    finally:
        for k in keys:
            db.clear_setting(k)
