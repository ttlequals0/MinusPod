"""Manual per-provider request-rate limits (RPM/RPD), issue #747."""
from datetime import timedelta

from tests.app_bootstrap import bootstrap

bootstrap('provider_rate_limit_test_')

from config import _validate_non_negative_int
from rate_limit_hold import (
    enforce_provider_rate_limit, evaluate_provider_rate_limit, is_queue_paused,
    probe_rate_limit, rate_limit_hold_tick, record_hold_until,
)
from utils.time import ISO_FORMAT, parse_iso_utc, utc_now


def _insert(db, provider_key, slot, created_at, attempt_id):
    db.get_connection().execute(
        "INSERT INTO llm_call_usage (attempt_id, provider_key, credential_slot, "
        "configured_model, phase_key, created_at, state) "
        "VALUES (?, ?, ?, 'm', 'detect', ?, 'success')",
        (attempt_id, provider_key, slot, created_at))
    db.get_connection().commit()


def _insert_tokens(db, provider_key, slot, created_at, attempt_id, input_t, output_t):
    db.get_connection().execute(
        "INSERT INTO llm_call_usage (attempt_id, provider_key, credential_slot, "
        "configured_model, phase_key, created_at, finalized_at, state, "
        "input_tokens, output_tokens) "
        "VALUES (?, ?, ?, 'm', 'detect', ?, ?, 'success', ?, ?)",
        (attempt_id, provider_key, slot, created_at, created_at, input_t, output_t))
    db.get_connection().commit()


class TestBeginPersistsSlot:
    def test_writes_credential_slot(self, temp_db):
        attempt_id = temp_db.begin_llm_attempt(
            run_id='r', podcast_id=1, episode_id='e', phase_key='detect',
            invoking_pass=1, provider_key='anthropic', configured_model='m',
            credential_slot='secondary')
        row = temp_db.get_connection().execute(
            "SELECT credential_slot FROM llm_call_usage WHERE attempt_id = ?",
            (attempt_id,)).fetchone()
        assert row['credential_slot'] == 'secondary'

    def test_defaults_to_primary(self, temp_db):
        attempt_id = temp_db.begin_llm_attempt(
            run_id='r', podcast_id=1, episode_id='e', phase_key='detect',
            invoking_pass=1, provider_key='anthropic', configured_model='m')
        row = temp_db.get_connection().execute(
            "SELECT credential_slot FROM llm_call_usage WHERE attempt_id = ?",
            (attempt_id,)).fetchone()
        assert row['credential_slot'] == 'primary'


class TestValidator:
    def test_zero_accepted(self):
        assert _validate_non_negative_int('0')

    def test_positive_accepted(self):
        assert _validate_non_negative_int('5')

    def test_negative_rejected(self):
        assert not _validate_non_negative_int('-1')

    def test_non_numeric_rejected(self):
        assert not _validate_non_negative_int('abc')


class TestCountHelpers:
    def test_counts_per_provider_and_slot_within_window(self, temp_db):
        now = utc_now()
        recent = (now - timedelta(seconds=10)).strftime(ISO_FORMAT)
        old = (now - timedelta(seconds=120)).strftime(ISO_FORMAT)
        since = (now - timedelta(seconds=60)).strftime(ISO_FORMAT)
        _insert(temp_db, 'anthropic', 'primary', recent, 'a1')
        _insert(temp_db, 'anthropic', 'primary', old, 'a2')
        _insert(temp_db, 'anthropic', 'secondary', recent, 'a3')
        _insert(temp_db, 'openai_compatible', 'primary', recent, 'a4')
        assert temp_db.count_recent_llm_attempts('anthropic', 'primary', since) == 1
        assert temp_db.count_recent_llm_attempts('anthropic', 'secondary', since) == 1

    def test_null_slot_counts_as_primary(self, temp_db):
        now = utc_now()
        recent = (now - timedelta(seconds=10)).strftime(ISO_FORMAT)
        since = (now - timedelta(seconds=60)).strftime(ISO_FORMAT)
        _insert(temp_db, 'anthropic', None, recent, 'a1')
        assert temp_db.count_recent_llm_attempts('anthropic', 'primary', since) == 1
        assert temp_db.count_recent_llm_attempts('anthropic', 'secondary', since) == 0

    def test_oldest_in_window(self, temp_db):
        now = utc_now()
        oldest = (now - timedelta(seconds=40)).strftime(ISO_FORMAT)
        newer = (now - timedelta(seconds=5)).strftime(ISO_FORMAT)
        since = (now - timedelta(seconds=60)).strftime(ISO_FORMAT)
        _insert(temp_db, 'anthropic', 'primary', newer, 'a1')
        _insert(temp_db, 'anthropic', 'primary', oldest, 'a2')
        assert temp_db.oldest_recent_llm_attempt('anthropic', 'primary', since) == oldest


class TestEvaluate:
    def test_none_when_unconfigured(self, temp_db):
        now = utc_now()
        _insert(temp_db, 'anthropic', 'primary',
                (now - timedelta(seconds=5)).strftime(ISO_FORMAT), 'a1')
        assert evaluate_provider_rate_limit(temp_db, 'anthropic', 'primary') is None

    def test_rpm_trip_resets_60s_after_oldest(self, temp_db):
        temp_db.set_setting('provider_requests_per_min', '3', is_default=False)
        now = utc_now()
        oldest = (now - timedelta(seconds=30)).strftime(ISO_FORMAT)
        _insert(temp_db, 'anthropic', 'primary', oldest, 'a1')
        _insert(temp_db, 'anthropic', 'primary',
                (now - timedelta(seconds=20)).strftime(ISO_FORMAT), 'a2')
        _insert(temp_db, 'anthropic', 'primary',
                (now - timedelta(seconds=10)).strftime(ISO_FORMAT), 'a3')
        reset = evaluate_provider_rate_limit(temp_db, 'anthropic', 'primary')
        expected = (parse_iso_utc(oldest) + timedelta(seconds=60)).strftime(ISO_FORMAT)
        assert reset == expected

    def test_rpm_under_cap_is_none(self, temp_db):
        temp_db.set_setting('provider_requests_per_min', '3', is_default=False)
        now = utc_now()
        _insert(temp_db, 'anthropic', 'primary',
                (now - timedelta(seconds=10)).strftime(ISO_FORMAT), 'a1')
        assert evaluate_provider_rate_limit(temp_db, 'anthropic', 'primary') is None

    def test_rpd_trip_resets_next_utc_midnight(self, temp_db):
        temp_db.set_setting('provider_requests_per_day', '2', is_default=False)
        now = utc_now()
        recent = (now - timedelta(seconds=10)).strftime(ISO_FORMAT)
        _insert(temp_db, 'anthropic', 'primary', recent, 'a1')
        _insert(temp_db, 'anthropic', 'primary', recent, 'a2')
        reset = evaluate_provider_rate_limit(temp_db, 'anthropic', 'primary')
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        expected = (midnight + timedelta(days=1)).strftime(ISO_FORMAT)
        assert reset == expected

    def test_both_trip_returns_later_reset(self, temp_db):
        temp_db.set_setting('provider_requests_per_min', '1', is_default=False)
        temp_db.set_setting('provider_requests_per_day', '1', is_default=False)
        now = utc_now()
        _insert(temp_db, 'anthropic', 'primary',
                (now - timedelta(seconds=10)).strftime(ISO_FORMAT), 'a1')
        reset = evaluate_provider_rate_limit(temp_db, 'anthropic', 'primary')
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        next_midnight = (midnight + timedelta(days=1)).strftime(ISO_FORMAT)
        assert reset == next_midnight

    def test_secondary_reads_secondary_settings(self, temp_db):
        temp_db.set_setting('provider_requests_per_min', '1', is_default=False)
        now = utc_now()
        _insert(temp_db, 'anthropic', 'secondary',
                (now - timedelta(seconds=10)).strftime(ISO_FORMAT), 'a1')
        assert evaluate_provider_rate_limit(temp_db, 'anthropic', 'secondary') is None
        temp_db.set_setting('secondary_provider_requests_per_min', '1', is_default=False)
        assert evaluate_provider_rate_limit(temp_db, 'anthropic', 'secondary') is not None


class TestEnforce:
    def test_records_hold_pausing_the_slot(self, temp_db):
        temp_db.set_setting('provider_requests_per_min', '1', is_default=False)
        now = utc_now()
        _insert(temp_db, 'anthropic', 'primary',
                (now - timedelta(seconds=10)).strftime(ISO_FORMAT), 'a1')
        assert not is_queue_paused(temp_db, 'anthropic', 'primary')
        hold_until = enforce_provider_rate_limit(temp_db, 'anthropic', 'primary')
        assert hold_until is not None
        assert is_queue_paused(temp_db, 'anthropic', 'primary')
        # A different account on the same provider is not paused.
        assert not is_queue_paused(temp_db, 'anthropic', 'secondary')

    def test_under_cap_records_no_hold(self, temp_db):
        temp_db.set_setting('provider_requests_per_min', '5', is_default=False)
        now = utc_now()
        _insert(temp_db, 'anthropic', 'primary',
                (now - timedelta(seconds=10)).strftime(ISO_FORMAT), 'a1')
        assert enforce_provider_rate_limit(temp_db, 'anthropic', 'primary') is None
        assert not is_queue_paused(temp_db, 'anthropic', 'primary')


class TestTokenHelpers:
    def test_sums_finalized_tokens_in_window(self, temp_db):
        now = utc_now()
        recent = (now - timedelta(seconds=10)).strftime(ISO_FORMAT)
        old = (now - timedelta(seconds=120)).strftime(ISO_FORMAT)
        since = (now - timedelta(seconds=60)).strftime(ISO_FORMAT)
        _insert_tokens(temp_db, 'anthropic', 'primary', recent, 'a1', 100, 40)
        _insert_tokens(temp_db, 'anthropic', 'primary', old, 'a2', 999, 999)
        _insert(temp_db, 'anthropic', 'primary', recent, 'a3')  # in_flight, no tokens
        assert temp_db.sum_recent_llm_tokens('anthropic', 'primary', since) == 140

    def test_null_slot_tokens_count_as_primary(self, temp_db):
        now = utc_now()
        recent = (now - timedelta(seconds=10)).strftime(ISO_FORMAT)
        since = (now - timedelta(seconds=60)).strftime(ISO_FORMAT)
        _insert_tokens(temp_db, 'anthropic', None, recent, 'a1', 50, 50)
        assert temp_db.sum_recent_llm_tokens('anthropic', 'primary', since) == 100
        assert temp_db.sum_recent_llm_tokens('anthropic', 'secondary', since) == 0


class TestEvaluateTpm:
    def test_tpm_trip_resets_60s_after_oldest_token_row(self, temp_db):
        temp_db.set_setting('provider_tokens_per_min', '100', is_default=False)
        now = utc_now()
        oldest = (now - timedelta(seconds=30)).strftime(ISO_FORMAT)
        _insert_tokens(temp_db, 'anthropic', 'primary', oldest, 'a1', 60, 0)
        _insert_tokens(temp_db, 'anthropic', 'primary',
                       (now - timedelta(seconds=10)).strftime(ISO_FORMAT), 'a2', 50, 0)
        reset = evaluate_provider_rate_limit(temp_db, 'anthropic', 'primary')
        expected = (parse_iso_utc(oldest) + timedelta(seconds=60)).strftime(ISO_FORMAT)
        assert reset == expected

    def test_tpm_under_cap_is_none(self, temp_db):
        temp_db.set_setting('provider_tokens_per_min', '1000', is_default=False)
        now = utc_now()
        _insert_tokens(temp_db, 'anthropic', 'primary',
                       (now - timedelta(seconds=10)).strftime(ISO_FORMAT), 'a1', 100, 100)
        assert evaluate_provider_rate_limit(temp_db, 'anthropic', 'primary') is None

    def test_tpm_zero_no_hold(self, temp_db):
        now = utc_now()
        _insert_tokens(temp_db, 'anthropic', 'primary',
                       (now - timedelta(seconds=10)).strftime(ISO_FORMAT), 'a1', 999, 999)
        assert evaluate_provider_rate_limit(temp_db, 'anthropic', 'primary') is None

    def test_secondary_reads_secondary_tpm(self, temp_db):
        temp_db.set_setting('provider_tokens_per_min', '100', is_default=False)
        now = utc_now()
        _insert_tokens(temp_db, 'anthropic', 'secondary',
                       (now - timedelta(seconds=10)).strftime(ISO_FORMAT), 'a1', 200, 0)
        assert evaluate_provider_rate_limit(temp_db, 'anthropic', 'secondary') is None
        temp_db.set_setting('secondary_provider_tokens_per_min', '100', is_default=False)
        assert evaluate_provider_rate_limit(temp_db, 'anthropic', 'secondary') is not None


class TestManualHoldProbeSkip:
    def test_manual_hold_is_not_probed(self, temp_db):
        record_hold_until(temp_db, None, '2099-01-01T00:00:00Z', manual=True)
        assert probe_rate_limit(temp_db) is False
        # Hold remains: only time (the tick) may clear a manual hold.
        assert temp_db.get_setting('rate_limit_hold_until') == '2099-01-01T00:00:00Z'

    def test_manual_hold_clears_by_time_via_tick(self, temp_db):
        record_hold_until(temp_db, None, '2000-01-01T00:00:00Z', manual=True)
        rate_limit_hold_tick(temp_db)
        assert not temp_db.get_setting('rate_limit_hold_until')
        assert not temp_db.get_setting('rate_limit_hold_manual')

    def test_real_429_marker_clears_manual_flag(self, temp_db):
        record_hold_until(temp_db, 'anthropic', '2099-01-01T00:00:00Z',
                          credential_slot='primary', manual=True)
        assert temp_db.get_setting('rate_limit_hold_manual:anthropic:primary')
        record_hold_until(temp_db, 'anthropic', '2099-06-01T00:00:00Z',
                          credential_slot='primary')
        assert not temp_db.get_setting('rate_limit_hold_manual:anthropic:primary')
