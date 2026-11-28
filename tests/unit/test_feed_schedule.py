"""Table-driven tests for the pure adaptive refresh interval."""
import random
from datetime import datetime, timedelta, timezone

import pytest

import feed_schedule as fs
from feed_schedule import compute_refresh_interval, parse_retry_after

NOW = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)
FLOOR = 15 * 60
MAX = 60 * 60
H = 3600


def dates(*hours_ago):
    return [NOW - timedelta(hours=h) for h in hours_ago]


def decide(**kw):
    kw.setdefault('now', NOW)
    kw.setdefault('floor_seconds', FLOOR)
    kw.setdefault('max_seconds', MAX)
    kw.setdefault('rng', random.Random(1))
    return compute_refresh_interval(**kw)


@pytest.mark.parametrize('episode_dates', [[], dates(5), dates(5, 29)])
def test_fewer_than_three_dates_fall_back_to_floor(episode_dates):
    d = decide(episode_dates=episode_dates)
    assert (d.interval_seconds, d.reason) == (FLOOR, 'floor')


def test_daily_show_between_episodes_is_checked_about_every_two_hours():
    d = decide(episode_dates=dates(9, 33, 57, 81), max_seconds=24 * H)
    assert (d.interval_seconds, d.reason) == (2 * H, 'cadence')


def test_cadence_interval_is_clamped_to_max():
    d = decide(episode_dates=dates(9, 33, 57, 81))
    assert (d.interval_seconds, d.reason) == (MAX, 'max')


def test_hourly_show_hits_the_floor():
    d = decide(episode_dates=dates(0.5, 1.5, 2.5, 3.5), max_seconds=24 * H)
    assert d.interval_seconds == FLOOR and d.reason in ('floor', 'due')


def test_due_episode_uses_floor():
    d = decide(episode_dates=dates(23, 47, 71, 95), max_seconds=24 * H)
    assert (d.interval_seconds, d.reason) == (FLOOR, 'due')


def test_late_episode_beyond_window_is_not_due():
    d = decide(episode_dates=dates(35, 59, 83, 107), max_seconds=24 * H)
    assert d.reason == 'cadence'


def test_irregular_cadence_uses_median_not_mean():
    # gaps 24h, 24h, 24h, 400h: median 24h
    d = decide(episode_dates=dates(9, 33, 57, 457), max_seconds=24 * H)
    assert (d.interval_seconds, d.reason) == (2 * H, 'cadence')


def test_only_the_newest_ten_dates_count():
    recent = dates(*[9 + 24 * i for i in range(10)])
    old = dates(*[2000 + 1000 * i for i in range(5)])
    d = decide(episode_dates=recent + old, max_seconds=24 * H)
    assert d.interval_seconds == 2 * H


def test_future_dated_episodes_are_ignored():
    future = [NOW + timedelta(days=d) for d in (1, 2, 3)]
    d = decide(episode_dates=future)
    assert (d.interval_seconds, d.reason) == (FLOOR, 'floor')


def test_future_date_does_not_hide_the_real_cadence():
    d = decide(episode_dates=dates(9, 33, 57, 81) + [NOW + timedelta(days=5)], max_seconds=24 * H)
    assert d.interval_seconds == 2 * H


def test_identical_timestamps_give_no_cadence():
    d = decide(episode_dates=dates(5, 5, 5, 5))
    assert d.reason == 'floor'


def test_naive_datetimes_are_treated_as_utc():
    naive = [(NOW - timedelta(hours=h)).replace(tzinfo=None) for h in (9, 33, 57, 81)]
    assert decide(episode_dates=naive, max_seconds=24 * H).interval_seconds == 2 * H


@pytest.mark.parametrize('period,expected_seconds,reason', [
    (86400, 2 * H, 'hint'),
    (7 * 86400, 14 * H, 'hint'),
    (600, FLOOR, 'floor'),
])
def test_update_period_hint(period, expected_seconds, reason):
    d = decide(update_period_seconds=period, max_seconds=24 * H)
    assert (d.interval_seconds, d.reason) == (expected_seconds, reason)


def test_hint_is_clamped_to_max():
    d = decide(update_period_seconds=30 * 86400)
    assert (d.interval_seconds, d.reason) == (MAX, 'max')


def test_cadence_wins_over_a_longer_hint():
    d = decide(episode_dates=dates(9, 33, 57, 81), update_period_seconds=7 * 86400, max_seconds=24 * H)
    assert (d.interval_seconds, d.reason) == (2 * H, 'cadence')


def test_complete_feed_drops_to_max():
    d = decide(episode_dates=dates(0.5, 1.5, 2.5, 3.5), feed_complete=True)
    assert (d.interval_seconds, d.reason) == (MAX, 'hint')


def test_dormant_feed_uses_max():
    d = decide(episode_dates=dates(0.5, 1.5, 2.5, 3.5), dormant=True)
    assert (d.interval_seconds, d.reason) == (MAX, 'dormant')


def test_http_max_age_lengthens_but_never_shortens():
    longer = decide(http_max_age_seconds=45 * 60)
    assert (longer.interval_seconds, longer.reason) == (45 * 60, 'hint')
    shorter = decide(http_max_age_seconds=60)
    assert (shorter.interval_seconds, shorter.reason) == (FLOOR, 'floor')
    capped = decide(http_max_age_seconds=10 * H)
    assert (capped.interval_seconds, capped.reason) == (MAX, 'max')


def test_http_max_age_does_not_delay_a_due_episode():
    d = decide(episode_dates=dates(23, 47, 71, 95), http_max_age_seconds=3 * H, max_seconds=24 * H)
    assert d.reason == 'due'


def test_disabled_mode_is_the_fixed_floor_and_ignores_signals():
    d = decide(adaptive=False, episode_dates=dates(9, 33, 57, 81), update_period_seconds=86400,
               feed_complete=True, dormant=True, http_max_age_seconds=3 * H)
    assert (d.interval_seconds, d.reason) == (FLOOR, 'fixed')


def test_disabled_mode_still_backs_off_and_honours_retry_after():
    assert decide(adaptive=False, failures=3).reason == 'backoff'
    d = decide(adaptive=False, retry_after_until=NOW + timedelta(hours=2))
    assert (d.interval_seconds, d.reason) == (2 * H, 'retry_after')


@pytest.mark.parametrize('failures,low', [(1, FLOOR), (2, 2 * FLOOR), (3, 4 * FLOOR), (4, 8 * FLOOR)])
def test_backoff_grows_from_the_floor(failures, low):
    d = decide(failures=failures)
    assert d.reason == 'backoff'
    assert low <= d.interval_seconds <= low * 1.1


@pytest.mark.parametrize('failures', [10, 100, 10_000])
def test_backoff_caps_at_six_hours_plus_jitter(failures):
    d = decide(failures=failures)
    assert 6 * H <= d.interval_seconds <= 6 * H * 1.1


def test_backoff_cap_never_below_a_large_floor():
    d = decide(failures=1, floor_seconds=24 * H, max_seconds=24 * H)
    assert d.interval_seconds >= 24 * H


def test_jitter_is_bounded_and_varies():
    values = {decide(failures=2, rng=random.Random(seed)).interval_seconds for seed in range(50)}
    assert len(values) > 10
    assert all(2 * FLOOR <= v <= 2 * FLOOR * 1.1 for v in values)


def test_jitter_is_deterministic_for_a_seed():
    a = decide(failures=2, rng=random.Random(7))
    b = decide(failures=2, rng=random.Random(7))
    assert a == b


def test_failures_override_the_cadence_signals():
    d = decide(failures=1, episode_dates=dates(23, 47, 71, 95))
    assert d.reason == 'backoff'


def test_retry_after_lengthens_but_never_shortens():
    long = decide(retry_after_until=NOW + timedelta(hours=3))
    assert (long.interval_seconds, long.reason) == (3 * H, 'retry_after')
    short = decide(retry_after_until=NOW + timedelta(seconds=30))
    assert short.reason == 'floor'


def test_retry_after_is_capped_at_24_hours():
    d = decide(retry_after_until=NOW + timedelta(days=90))
    assert d.interval_seconds == 24 * H


def test_retry_after_in_the_past_is_ignored():
    assert decide(retry_after_until=NOW - timedelta(hours=1)).reason == 'floor'


def test_max_below_floor_is_raised_to_floor():
    d = decide(max_seconds=60)
    assert d.interval_seconds == FLOOR


@pytest.mark.parametrize('value,expected', [
    ('120', 120), (' 45 ', 45), ('0', None), ('-5', None), ('', None), (None, None),
    ('soon', None), ('1e3', None), ('99999999999', 24 * H), ('12.5', None),
])
def test_parse_retry_after_seconds(value, expected):
    assert parse_retry_after(value, NOW) == expected


def test_parse_retry_after_http_date():
    future = 'Mon, 01 Jun 2026 14:00:00 GMT'
    assert parse_retry_after(future, NOW) == 2 * H
    assert parse_retry_after('Mon, 01 Jun 2026 11:00:00 GMT', NOW) is None
    assert parse_retry_after('Mon, 01 Jun 2030 11:00:00 GMT', NOW) == 24 * H
    assert parse_retry_after('Mon, 99 Foo 2026 99:00:00 GMT', NOW) is None


@pytest.mark.parametrize('value,expected', [
    ('max-age=3600', 3600), ('public, max-age=600, s-maxage=9', 600), ('MAX-AGE=60', 60),
    ('max-age="120"', 120), ('max-age=0', None), ('no-cache', None), ('', None), (None, None),
    ('max-age=abc', None), ('s-maxage=300', None),
])
def test_parse_cache_control(value, expected):
    assert fs.parse_cache_control_max_age(value) == expected


@pytest.mark.parametrize('rrule,expected', [
    ('FREQ=DAILY', 86400), ('FREQ=WEEKLY', 7 * 86400), ('FREQ=WEEKLY;INTERVAL=2', 14 * 86400),
    ('FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR', int(7 * 86400 / 5)), ('FREQ=MONTHLY', 30 * 86400),
    ('freq=hourly', 3600), ('FREQ=SECONDLY', None), ('FREQ=DAILY;INTERVAL=0', None),
    ('FREQ=DAILY;INTERVAL=x', None), ('nonsense', None), ('', None), (None, None),
])
def test_parse_rrule(rrule, expected):
    assert fs.parse_rrule_period_seconds(rrule) == expected


@pytest.mark.parametrize('period,freq,expected', [
    ('daily', '1', 86400), ('daily', '2', 43200), ('hourly', None, 3600), ('weekly', '', 7 * 86400),
    ('fortnightly', '1', None), ('daily', '0', None), ('daily', 'x', None), (None, '1', None),
])
def test_parse_sy(period, freq, expected):
    assert fs.parse_sy_period_seconds(period, freq) == expected


def test_next_refresh_at_adds_the_interval():
    d = fs.RefreshDecision(900, 'floor')
    assert fs.next_refresh_at(NOW, d) == NOW + timedelta(seconds=900)


def test_fetch_meta_is_per_thread_and_resettable():
    fs.reset_fetch_meta()
    fs.note_fetch_meta(status=429)
    assert fs.get_fetch_meta() == {'status': 429}
    fs.reset_fetch_meta()
    assert fs.get_fetch_meta() == {}
