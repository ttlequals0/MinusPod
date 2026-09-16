"""Manual caps as request reservations (F06): the ledger row IS the
reservation, every SDK dispatch consumes a count, and TPM reserves an
estimate at dispatch instead of waiting for recorded usage.
"""
import threading
from unittest.mock import MagicMock, patch

from tests.app_bootstrap import bootstrap

bootstrap('provider_reservation_test_')

import llm_client
import run_context
from database import Database
from llm_client import OpenAICompatibleClient
from rate_limit_hold import manual_rate_limit_caps, reserve_provider_request
from utils.llm_call import _reserved_tokens


def _attempt(label='w1'):
    return dict(run_id='r', podcast_id=1, episode_id='e', phase_key='detection',
                invoking_pass=1, configured_model='m', window_label=label)


def _caps(rpm=0, rpd=0, tpm=0):
    caps = manual_rate_limit_caps('primary')
    caps.update(rpm=rpm, rpd=rpd, tpm=tpm)
    return caps


class TestReservationIsAtomic:
    def test_cap_of_one_admits_one_of_two_reservations(self, temp_db):
        first = temp_db.reserve_llm_attempt(
            provider_key='anthropic', credential_slot='primary',
            caps=_caps(rpm=1), **_attempt('w1'))
        second = temp_db.reserve_llm_attempt(
            provider_key='anthropic', credential_slot='primary',
            caps=_caps(rpm=1), **_attempt('w2'))
        assert first['attempt_id'] is not None
        assert second['attempt_id'] is None
        assert second['blocked'] == 'rpm'

    def test_concurrent_workers_cannot_all_pass_one_cap(self, temp_dir):
        """Threads on separate connections to one SQLite file: only `cap`
        reservations may be granted, no matter how they interleave."""
        cap = 3
        workers = 12
        granted = []
        lock = threading.Lock()
        barrier = threading.Barrier(workers)
        previous = Database._instance
        Database._instance = None
        db = Database(data_dir=temp_dir)
        try:
            def worker(index):
                barrier.wait()
                result = db.reserve_llm_attempt(
                    provider_key='anthropic', credential_slot='primary',
                    caps=_caps(rpm=cap), **_attempt(f'w{index}'))
                if result['attempt_id'] is not None:
                    with lock:
                        granted.append(result['attempt_id'])

            threads = [threading.Thread(target=worker, args=(i,))
                       for i in range(workers)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            rows = db.get_connection().execute(
                "SELECT COUNT(*) AS n FROM llm_call_usage").fetchone()['n']
        finally:
            Database._instance = previous
        assert len(granted) == cap
        assert rows == cap

    def test_daily_cap_blocks_with_its_own_reason(self, temp_db):
        assert temp_db.reserve_llm_attempt(
            provider_key='anthropic', credential_slot='primary',
            caps=_caps(rpd=1), **_attempt('w1'))['attempt_id'] is not None
        blocked = temp_db.reserve_llm_attempt(
            provider_key='anthropic', credential_slot='primary',
            caps=_caps(rpd=1), **_attempt('w2'))
        assert blocked['blocked'] == 'rpd'

    def test_no_cap_configured_still_creates_the_attempt(self, temp_db):
        result = temp_db.reserve_llm_attempt(
            provider_key='anthropic', credential_slot='primary',
            caps=_caps(), **_attempt())
        assert result['attempt_id'] is not None
        assert result['blocked'] is None

    def test_other_account_is_unaffected(self, temp_db):
        temp_db.reserve_llm_attempt(
            provider_key='anthropic', credential_slot='primary',
            caps=_caps(rpm=1), **_attempt('w1'))
        other = temp_db.reserve_llm_attempt(
            provider_key='anthropic', credential_slot='secondary',
            caps=_caps(rpm=1), **_attempt('w2'))
        assert other['attempt_id'] is not None


class TestReserveProviderRequestRecordsTheHold:
    def test_refusal_records_a_manual_hold(self, temp_db):
        with patch('rate_limit_hold.manual_rate_limit_caps',
                   side_effect=lambda slot='primary': _caps(rpm=1)):
            first, _ = reserve_provider_request(
                temp_db, 'anthropic', 'primary', attempt=_attempt('w1'))
            second, hold_until = reserve_provider_request(
                temp_db, 'anthropic', 'primary', attempt=_attempt('w2'))
        assert first is not None
        assert second is None
        assert hold_until
        assert temp_db.get_setting('rate_limit_hold_manual:anthropic:primary') == 'true'


class TestEveryDispatchConsumesACount:
    def test_dispatch_count_backs_the_request_window(self, temp_db):
        attempt_id = temp_db.reserve_llm_attempt(
            provider_key='anthropic', credential_slot='primary',
            caps=_caps(), **_attempt())['attempt_id']
        since = manual_rate_limit_caps('primary')['minute_since']
        assert temp_db.count_recent_llm_attempts('anthropic', 'primary', since) == 1
        temp_db.bump_llm_attempt_dispatches(attempt_id)
        assert temp_db.count_recent_llm_attempts('anthropic', 'primary', since) == 2

    def test_a_compatibility_retry_is_counted(self, temp_db):
        """The token-parameter retry inside one attempt is a second real
        outbound request, so it must consume a second count."""
        from openai import BadRequestError

        client = OpenAICompatibleClient(base_url='https://example.test/v1',
                                        api_key='sk-test')
        response = MagicMock()
        error = BadRequestError.__new__(BadRequestError)
        Exception.__init__(error, "unsupported parameter: 'max_completion_tokens'")
        client._client = MagicMock()
        client._client.chat.completions.create.side_effect = [error, response]

        attempt_id = temp_db.reserve_llm_attempt(
            provider_key='openai-compatible', credential_slot='primary',
            caps=_caps(), **_attempt())['attempt_id']
        run_context.begin_dispatch(attempt_id)
        try:
            client._call_with_token_param_fallback(
                'gpt-x', {'model': 'gpt-x', 'max_completion_tokens': 10},
                'max_completion_tokens')
        finally:
            run_context.end_dispatch()

        since = manual_rate_limit_caps('primary')['minute_since']
        assert temp_db.count_recent_llm_attempts(
            'openai-compatible', 'primary', since) == 2

    def test_extra_dispatch_outside_an_attempt_is_a_no_op(self):
        run_context.end_dispatch()
        llm_client._record_extra_dispatch()


class TestTokensAreReservedAtDispatch:
    def test_estimate_covers_prompt_and_output_budget(self):
        reserved = _reserved_tokens({
            'system': 'a' * 400, 'messages': [{'role': 'user', 'content': 'b' * 400}],
            'max_tokens': 1000})
        assert reserved == 800 // 4 + 1000

    def test_in_flight_reservation_counts_against_tpm(self, temp_db):
        temp_db.reserve_llm_attempt(
            provider_key='anthropic', credential_slot='primary',
            reserved_tokens=5000, caps=_caps(), **_attempt())
        since = manual_rate_limit_caps('primary')['minute_since']
        assert temp_db.sum_recent_llm_tokens('anthropic', 'primary', since) == 5000

    def test_reservation_reconciles_to_actual_usage(self, temp_db):
        attempt_id = temp_db.reserve_llm_attempt(
            provider_key='anthropic', credential_slot='primary',
            reserved_tokens=5000, caps=_caps(), **_attempt())['attempt_id']
        temp_db.finalize_llm_attempt(attempt_id, state='success',
                                     input_tokens=100, output_tokens=20)
        since = manual_rate_limit_caps('primary')['minute_since']
        assert temp_db.sum_recent_llm_tokens('anthropic', 'primary', since) == 120

    def test_a_single_large_call_trips_the_tpm_cap(self, temp_db):
        temp_db.reserve_llm_attempt(
            provider_key='anthropic', credential_slot='primary',
            reserved_tokens=5000, caps=_caps(), **_attempt('w1'))
        blocked = temp_db.reserve_llm_attempt(
            provider_key='anthropic', credential_slot='primary',
            reserved_tokens=5000, caps=_caps(tpm=4000), **_attempt('w2'))
        assert blocked['blocked'] == 'tpm'
