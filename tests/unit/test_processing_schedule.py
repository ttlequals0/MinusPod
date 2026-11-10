"""Processing schedule window maths and failure policy (#992)."""
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

import processing_schedule as ps
from utils.time import parse_iso_utc as at

ALL_DAYS = 'mon,tue,wed,thu,fri,sat,sun'


class FakeDb:
    def __init__(self, **settings):
        self.values = {f'processing_schedule_{k}': v for k, v in settings.items()}
        self.values['notification_timezone'] = self.values.pop('processing_schedule_tz', 'UTC')
        self.reads = 0

    def get_setting(self, key):
        self.reads += 1
        return self.values.get(key)


def sched(start='09:00', end='17:00', days='mon,tue,wed,thu,fri', tz='UTC', enabled='true'):
    return FakeDb(enabled=enabled, start=start, end=end, days=days, tz=tz)


def state(db, now):
    result = ps.schedule_state(db, at(now))
    return result['open'], result['nextChangeAt']


def test_disabled_is_always_open():
    db = sched(enabled='false')
    assert ps.schedule_state(db, at('2026-01-17T03:00:00Z')) == {
        'enabled': False, 'open': True, 'nextChangeAt': None}
    assert ps.is_schedule_closed(db) is False


def test_unset_schedule_is_disabled():
    assert ps.schedule_state(FakeDb())['enabled'] is False


def test_same_day_window():
    db = sched()
    assert state(db, '2026-01-14T10:00:00Z') == (True, '2026-01-14T17:00:00Z')
    assert state(db, '2026-01-14T18:00:00Z') == (False, '2026-01-15T09:00:00Z')
    assert state(db, '2026-01-14T08:59:59Z') == (False, '2026-01-14T09:00:00Z')
    assert state(db, '2026-01-14T17:00:00Z')[0] is False


def test_unselected_days_skip_to_the_next_start():
    assert state(sched(), '2026-01-17T12:00:00Z') == (False, '2026-01-19T09:00:00Z')


def test_overnight_window_spills_into_an_unselected_day():
    db = sched(start='22:00', end='06:00', days='fri')
    assert state(db, '2026-01-17T03:00:00Z') == (True, '2026-01-17T06:00:00Z')


def test_early_hours_of_a_selected_day_are_closed_when_the_previous_day_is_not():
    db = sched(start='22:00', end='06:00', days='fri')
    assert state(db, '2026-01-16T03:00:00Z') == (False, '2026-01-16T22:00:00Z')


def test_equal_start_and_end_is_24_hours_on_selected_days():
    db = sched(start='08:00', end='08:00', days='mon,wed')
    assert state(db, '2026-01-20T07:00:00Z') == (True, '2026-01-20T08:00:00Z')
    assert state(db, '2026-01-20T09:00:00Z') == (False, '2026-01-21T08:00:00Z')


def test_every_day_for_24_hours_never_closes():
    db = sched(start='08:00', end='08:00', days=ALL_DAYS)
    assert ps.schedule_state(db, at('2026-01-20T12:00:00Z')) == {
        'enabled': True, 'open': True, 'nextChangeAt': None}


def test_adjacent_windows_merge():
    db = sched(start='12:00', end='12:00', days='mon,tue')
    assert state(db, '2026-01-20T06:00:00Z') == (True, '2026-01-21T12:00:00Z')


def test_window_end_follows_the_wall_clock_across_dst():
    db = sched(start='22:00', end='06:00', days='sat', tz='America/New_York')
    # 06:00 EDT after the 2026-03-08 spring-forward is 10:00Z, not 11:00Z.
    assert state(db, '2026-03-08T08:00:00Z') == (True, '2026-03-08T10:00:00Z')


def test_time_zone_comes_from_notification_timezone():
    now = '2026-01-14T20:00:00Z'
    assert state(sched(tz='UTC'), now)[0] is False
    assert state(sched(tz='America/New_York'), now) == (True, '2026-01-14T22:00:00Z')


def test_get_processing_schedule_orders_days_and_reports_zone():
    db = sched(days='sun,mon', tz='America/New_York')
    assert ps.get_processing_schedule(db) == {
        'enabled': True, 'start': '09:00', 'end': '17:00',
        'days': ['mon', 'sun'], 'timezone': 'America/New_York'}


def test_schedule_state_read_count():
    off = sched(enabled='false')
    ps.schedule_state(off)
    assert off.reads == 1
    on = sched()
    ps.schedule_state(on)
    assert on.reads <= 5


@pytest.mark.parametrize('stored', ['1', 'true'])
def test_stored_enabled_truthy_is_on_for_view_and_gate(stored):
    db = sched(enabled=stored)
    assert ps.get_processing_schedule(db)['enabled'] is True
    assert ps.schedule_state(db, at('2026-01-14T20:00:00Z'))['enabled'] is True


@pytest.mark.parametrize('field,value,default', [
    ('start', '25:00', '00:00'), ('end', '6:00', '06:00'), ('days', '', ALL_DAYS),
    ('days', 'mon,funday', ALL_DAYS), ('days', 'mon,mon', ALL_DAYS),
])
def test_invalid_value_acts_as_its_default(field, value, default):
    bad = sched(**{field: value})
    explicit = sched(**{field: default})
    now = at('2026-01-17T03:00:00Z')
    got = ps.schedule_state(bad, now)
    assert got['enabled'] is True
    assert got == ps.schedule_state(explicit, now)
    assert ps.get_processing_schedule(bad)[field] == ps.get_processing_schedule(explicit)[field]


def test_database_error_fails_closed(caplog):
    class Broken:
        def get_setting(self, key):
            raise RuntimeError('db locked')

    with caplog.at_level(logging.WARNING, logger='podcast.refresh'):
        assert ps.is_schedule_closed(Broken()) is True
    assert 'Could not read processing schedule' in caplog.text


def test_is_schedule_closed_follows_the_clock(monkeypatch):
    db = sched()
    monkeypatch.setattr(ps, 'utc_now', lambda: at('2026-01-14T10:00:00Z'))
    assert ps.is_schedule_closed(db) is False
    monkeypatch.setattr(ps, 'utc_now', lambda: at('2026-01-14T20:00:00Z'))
    assert ps.is_schedule_closed(db) is True
