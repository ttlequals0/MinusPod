"""Processing schedule gates: start refusal, dispatcher hold, admission explanation (#992)."""
import threading
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('schedule_gates_test_')
from main_app import background, db
from main_app.pipeline.admission import admission_explanation
from main_app.processing import start_background_processing
from processing_queue import ProcessingQueue, set_processing_paused
import processing_schedule as ps
from tests.unit.test_queue_dispatcher import _pool, _queue, _run_dispatcher, SLUG

CLOSED = datetime(2026, 1, 14, 20, 0, tzinfo=timezone.utc)
OPEN = datetime(2026, 1, 14, 12, 0, tzinfo=timezone.utc)
EP = 'a1b2c3d4e5f6'


@pytest.fixture
def clock(monkeypatch):
    """Weekday 09:00-17:00 UTC schedule with a settable clock, closed at first."""
    now = {'value': CLOSED}
    monkeypatch.setattr(ps, 'utc_now', lambda: now['value'])
    ps.save_processing_schedule(
        db, enabled=True, start='09:00', end='17:00',
        days=['mon', 'tue', 'wed', 'thu', 'fri'])
    db.create_podcast(SLUG, 'https://example.com/feed.xml', title='Dispatch')
    set_processing_paused(False, db)
    ProcessingQueue().clear_all()
    yield now
    ps.save_processing_schedule(db, enabled=False)
    ProcessingQueue().clear_all()
    db.delete_podcast(SLUG)
    db.get_connection().execute("DELETE FROM auto_process_queue")
    db.get_connection().commit()


@pytest.mark.parametrize('when,expected', [
    (CLOSED, (False, 'outside_schedule')),
    (OPEN, (True, 'started')),
])
def test_start_is_gated_by_the_window(clock, monkeypatch, when, expected):
    clock['value'] = when
    monkeypatch.setattr('main_app.processing.get_pool', lambda: _pool(False, 1))
    with patch('main_app.processing.threading.Thread') as thread:
        result = start_background_processing(
            SLUG, 'ep-play', 'https://example.com/e.mp3', 'E', 'P', None, None)
    assert result == expected
    if not expected[0]:
        thread.assert_not_called()
        assert ProcessingQueue().is_processing(SLUG, 'ep-play') is False


def test_dispatcher_does_not_claim_outside_the_window(clock, monkeypatch):
    _queue(2)
    peak = _run_dispatcher(monkeypatch, lambda: _pool(False, 1), timeout=1)
    assert peak == 0
    assert db.count_pending_queued_episodes() == 2


def test_dispatcher_resumes_when_the_window_opens(clock, monkeypatch):
    _queue(1)
    opener = threading.Timer(0.2, lambda: clock.update(value=OPEN))
    opener.start()
    try:
        _run_dispatcher(monkeypatch, lambda: _pool(False, 1), timeout=3)
        assert db.count_pending_queued_episodes() == 0
    finally:
        opener.cancel()


def test_dispatcher_unloads_the_idle_model_while_held(clock, monkeypatch, caplog):
    _queue(1)
    unload = MagicMock(return_value=True)
    monkeypatch.setattr(background, 'unload_whisper_if_idle', unload)
    with caplog.at_level('INFO'):
        _run_dispatcher(monkeypatch, lambda: _pool(False, 1), timeout=0.5)
    unload.assert_called()
    assert 'Outside the processing schedule' in caplog.text
    assert 'unloaded the local transcription model' in caplog.text
    assert db.count_pending_queued_episodes() == 1


def test_dispatcher_keeps_the_model_while_a_run_is_active(clock, monkeypatch):
    _queue(1)
    unload = MagicMock(return_value=True)
    monkeypatch.setattr(background, 'unload_whisper_if_idle', unload)
    run_id = ProcessingQueue().acquire(SLUG, 'busy', limit=99)
    try:
        _run_dispatcher(monkeypatch, lambda: _pool(False, 1), timeout=0.5)
    finally:
        ProcessingQueue().release(run_id)
    unload.assert_not_called()


def test_operator_pause_also_unloads_the_idle_model(clock, monkeypatch):
    ps.save_processing_schedule(db, enabled=False)
    set_processing_paused(True, db)
    _queue(1)
    unload = MagicMock(return_value=True)
    monkeypatch.setattr(background, 'unload_whisper_if_idle', unload)
    try:
        _run_dispatcher(monkeypatch, lambda: _pool(False, 1), timeout=0.5)
    finally:
        set_processing_paused(False, db)
    unload.assert_called()


def test_admission_explanation_reports_the_closed_window(clock):
    result = admission_explanation(db, SLUG, EP, paused=False)
    assert result == {'blocked': True, 'phase': None, 'slot': None,
                      'reason': 'outside_schedule', 'resumesAt': '2026-01-15T09:00:00Z'}


def test_admission_explanation_uses_a_passed_schedule_state(clock):
    state = {'enabled': True, 'open': False, 'nextChangeAt': '2026-02-01T09:00:00Z'}
    clock['value'] = OPEN
    result = admission_explanation(db, SLUG, EP, paused=False, schedule=state)
    assert result['reason'] == 'outside_schedule'
    assert result['resumesAt'] == '2026-02-01T09:00:00Z'


def test_pause_outranks_the_schedule(clock):
    assert admission_explanation(db, SLUG, EP, paused=True)['reason'] == 'processing_paused'
