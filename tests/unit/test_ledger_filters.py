"""Unit tests for the Stats ledger date-range filter (F16)."""
from database.stats import _build_ledger_filters, _utc_day_start


def test_to_date_is_half_open_next_day_start():
    sql, params = _build_ledger_filters(to_date='2026-09-14')
    assert 'created_at < ?' in sql
    assert params == ['2026-09-15T00:00:00Z']


def test_fractional_second_to_date_still_includes_final_second():
    # The UI's end-of-day value must not drop 'T23:59:59Z' rows.
    sql, params = _build_ledger_filters(to_date='2026-09-14T23:59:59.999Z')
    assert 'created_at < ?' in sql
    assert params == ['2026-09-15T00:00:00Z']


def test_from_date_canonicalized_to_utc_day_start():
    sql, params = _build_ledger_filters(from_date='2026-09-14')
    assert 'created_at >= ?' in sql
    assert params == ['2026-09-14T00:00:00Z']


def test_offset_timestamp_normalized_to_utc():
    # 2026-09-14T22:00:00-05:00 is 2026-09-15T03:00Z -> day 2026-09-15.
    assert _utc_day_start('2026-09-14T22:00:00-05:00') == '2026-09-15T00:00:00Z'


def test_unparseable_date_falls_back_to_raw_comparison():
    sql, params = _build_ledger_filters(to_date='not-a-date')
    assert 'created_at <= ?' in sql
    assert params == ['not-a-date']
