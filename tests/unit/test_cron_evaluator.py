"""Tests for the minimal cron evaluator (src/utils/cron.py)."""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from utils.cron import is_due, is_valid_expression, next_fire, parse_expression  # noqa: E402


def test_validation_accepts_common_forms():
    assert is_valid_expression('0 3 * * 0')        # Sunday 3am
    assert is_valid_expression('*/5 * * * *')      # every 5 minutes
    assert is_valid_expression('30 */2 * * *')     # every 2 hours at :30
    assert is_valid_expression('0 0 1,15 * *')     # 1st and 15th midnight
    assert is_valid_expression('0 9-17 * * 1-5')   # weekday business hours
    assert is_valid_expression('* * * * *')        # every minute


def test_validation_rejects_garbage():
    assert not is_valid_expression('bad')
    assert not is_valid_expression('* * * *')        # too few fields
    assert not is_valid_expression('60 * * * *')     # out of range minute
    assert not is_valid_expression('* * * * 7')      # out of range dow
    assert not is_valid_expression('*/0 * * * *')    # zero step
    assert not is_valid_expression('5-3 * * * *')    # backwards range


def test_parse_field_handles_lists_and_steps():
    minute, hour, dom, month, dow = parse_expression('0,15,30 */2 * * *')
    assert minute == {0, 15, 30}
    assert hour == {0, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22}
    assert dom == set(range(1, 32))
    assert month == set(range(1, 13))
    assert dow == set(range(0, 7))


def test_next_fire_sunday_3am():
    # 2026-05-14 is a Thursday. Next Sunday 3am should be 2026-05-17 03:00.
    after = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)
    nxt = next_fire('0 3 * * 0', after)
    assert nxt == datetime(2026, 5, 17, 3, 0, tzinfo=timezone.utc)


def test_next_fire_every_5_minutes():
    after = datetime(2026, 1, 1, 12, 7, tzinfo=timezone.utc)
    nxt = next_fire('*/5 * * * *', after)
    assert nxt == datetime(2026, 1, 1, 12, 10, tzinfo=timezone.utc)


def test_is_due_basic():
    cron = '0 3 * * 0'
    last_run = datetime(2026, 5, 10, 3, 0, tzinfo=timezone.utc)  # Sunday 3am
    not_yet = datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)  # Thursday
    sunday = datetime(2026, 5, 17, 3, 0, tzinfo=timezone.utc)
    assert is_due(cron, last_run, not_yet) is False
    assert is_due(cron, last_run, sunday) is True
