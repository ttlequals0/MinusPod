"""Tests for the reviewer calibration self-test."""
import json
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from tests.app_bootstrap import bootstrap, ensure_model_configured

_data_dir = bootstrap('reviewer_calibration_test_')

from database import Database  # noqa: E402
from main_app import app  # noqa: E402
from tools.reviewer_calibration import (  # noqa: E402
    CALIBRATION_AGREEMENT_THRESHOLD,
    CALIBRATION_CORPUS,
    main,
    maybe_trigger_reviewer_calibration,
    run_calibration,
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


@pytest.fixture
def calibration_calls(monkeypatch):
    """(old, new) pairs the settings save handed to the calibration hook."""
    calls = []
    monkeypatch.setattr(
        'api.settings.maybe_trigger_reviewer_calibration',
        lambda db_arg, old, new: calls.append((old, new)),
    )
    return calls


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

def test_maybe_trigger_calibration_persists_result_on_model_change():
    db = _build_db()
    db.set_setting('reviewer_calibration_on_change', 'true', is_default=False)
    canned = {
        'model': 'new-model', 'provider': 'anthropic', 'cases': [],
        'agreement': 0.9, 'structured_fraction': 0.5, 'ran_at': '2026-08-19T00:00:00Z',
    }
    import tools.reviewer_calibration as calib_mod
    original = calib_mod.run_calibration
    calib_mod.run_calibration = lambda **kw: canned
    try:
        thread = maybe_trigger_reviewer_calibration(db, 'old-model', 'new-model')
        assert thread is not None
        thread.join(timeout=5)
        assert not thread.is_alive()
    finally:
        calib_mod.run_calibration = original

    stored = db.get_setting('reviewer_calibration_last')
    assert stored is not None
    assert json.loads(stored) == canned


def test_maybe_trigger_calibration_noop_when_model_unchanged():
    db = _build_db()
    db.set_setting('reviewer_calibration_on_change', 'true', is_default=False)
    db.clear_setting('reviewer_calibration_last')
    thread = maybe_trigger_reviewer_calibration(db, 'same-model', 'same-model')
    assert thread is None
    assert db.get_setting('reviewer_calibration_last') is None


def test_maybe_trigger_calibration_gated_off_by_setting():
    db = _build_db()
    db.set_setting('reviewer_calibration_on_change', 'false', is_default=False)
    db.clear_setting('reviewer_calibration_last')
    thread = maybe_trigger_reviewer_calibration(db, 'old-model', 'new-model')
    assert thread is None
    assert db.get_setting('reviewer_calibration_last') is None


def test_maybe_trigger_calibration_swallows_failure():
    db = _build_db()
    db.set_setting('reviewer_calibration_on_change', 'true', is_default=False)
    db.clear_setting('reviewer_calibration_last')

    import tools.reviewer_calibration as calib_mod
    original = calib_mod.run_calibration

    def _boom(**kw):
        raise RuntimeError('llm unreachable')

    calib_mod.run_calibration = _boom
    try:
        thread = maybe_trigger_reviewer_calibration(db, 'old-model', 'new-model')
        assert thread is not None
        thread.join(timeout=5)
        assert not thread.is_alive()
    finally:
        calib_mod.run_calibration = original

    # Failure never blocks/raises and never writes a stale/partial result.
    assert db.get_setting('reviewer_calibration_last') is None


def test_reviewer_calibration_on_change_defaults_true():
    db = _build_db()
    db.clear_setting('reviewer_calibration_on_change')
    assert db.get_setting_bool('reviewer_calibration_on_change', True) is True


def test_settings_save_calibrates_the_review_model_once(client, calibration_calls):
    db = _build_db()
    db.set_setting('review_model', 'old-model', is_default=False)

    resp = _save_settings(client, {'reviewModel': 'new-model'})
    assert resp.status_code == 200
    assert calibration_calls == [('old-model', 'new-model')]
    assert db.get_setting('review_model') == 'new-model'


def test_settings_save_calibrates_claude_model_when_review_is_same_as_pass(client, calibration_calls):
    # review_model 'same_as_pass' means the detection model is the effective
    # reviewer model, so changing it must calibrate.
    db = _build_db()
    db.set_setting('review_model', 'same_as_pass', is_default=False)
    db.set_setting('claude_model', 'old-model', is_default=False)

    resp = _save_settings(client, {'claudeModel': 'new-model'})
    assert resp.status_code == 200
    assert calibration_calls == [('old-model', 'new-model')]


def test_settings_save_skips_claude_calibration_with_explicit_reviewer(client, calibration_calls):
    db = _build_db()
    db.set_setting('review_model', 'reviewer-model', is_default=False)
    db.set_setting('claude_model', 'old-model', is_default=False)

    resp = _save_settings(client, {'claudeModel': 'new-model'})
    assert resp.status_code == 200
    assert calibration_calls == []


def test_a_saved_review_model_is_calibrated_even_when_a_later_phase_fails(
        client, calibration_calls):
    """Each phase commits as it runs, so a later field's 400 still leaves the
    new review model persisted. Calibration follows its own phase to keep the
    saved model and its self-test in step."""
    db = _build_db()
    db.set_setting('review_model', 'old-model', is_default=False)
    db.set_setting('claude_model', 'old-claude', is_default=False)

    # detectionProvider is validated in a phase after both model phases.
    resp = _save_settings(client, {
        'reviewModel': 'new-model',
        'claudeModel': 'new-claude',
        'detectionProvider': 'not-a-slot',
    })

    assert resp.status_code == 400
    assert db.get_setting('review_model') == 'new-model'
    assert calibration_calls == [('old-model', 'new-model')]


def test_a_request_rejected_before_the_review_phase_calibrates_nothing(
        client, calibration_calls):
    """Nothing was saved, so there is nothing to self-test."""
    db = _build_db()
    db.set_setting('review_model', 'old-model', is_default=False)

    resp = _save_settings(client, {
        'reviewModel': 'new-model',
        'adAddressingMode': 'not-a-mode',
    })

    assert resp.status_code == 400
    assert db.get_setting('review_model') == 'old-model'
    assert calibration_calls == []


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
