"""Tests for the Whisper backend health probe (#734)."""
import pytest
from unittest.mock import patch, MagicMock

import requests as requests_lib

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('whisper_health_test_', passphrase='whisper-health-test-pass')

import transcriber  # noqa: E402
from transcriber import probe_whisper_health  # noqa: E402

BASE = 'http://transcriber:8001/v1'


@pytest.fixture(autouse=True)
def _clear_health_cache():
    """The probe caches per base URL; tests share BASE, so start each clean."""
    transcriber._health_cache.clear()
    transcriber._health_last_good.clear()
    transcriber._health_inflight.clear()
    yield


def _health(instance, model='large-v3', device='cuda', compute_type='float16',
           max_concurrent=1, batch_size=16, vad_filter=True):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {
        'status': 'ok', 'instance': instance, 'model': model, 'device': device,
        'compute_type': compute_type, 'beam_size': 5, 'batch_size': batch_size,
        'max_concurrent': max_concurrent, 'vad_filter': vad_filter,
        'hotwords_loaded': True,
    }
    return r


def _health_no_max_concurrent(instance, model='large-v3'):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {
        'status': 'ok', 'instance': instance, 'model': model,
        'device': 'cuda', 'compute_type': 'float16',
    }
    return r


def _bad(status=404):
    r = MagicMock()
    r.status_code = status
    return r


class TestDistinctInstances:
    def test_three_distinct_instances_sum_max_concurrent(self):
        replicas = [_health('whisper-1'), _health('whisper-2'), _health('whisper-3')]
        with patch('transcriber.safe_get', side_effect=replicas) as sg:
            result = probe_whisper_health(base_url=BASE, samples=3, refresh=True)
        assert result['available'] is True
        assert sg.call_args[0][0] == f'{BASE}/health'
        assert [i['instance'] for i in result['instances']] == [
            'whisper-1', 'whisper-2', 'whisper-3']
        assert result['suggested_max_requests'] == 3
        assert result['mismatch'] == []


class TestEarlyStop:
    def test_repeated_instance_stops_after_three_consecutive_repeats(self):
        replicas = [_health('whisper-1', max_concurrent=2)] * 5
        with patch('transcriber.safe_get', side_effect=replicas) as sg:
            result = probe_whisper_health(base_url=BASE, samples=5, refresh=True)
        assert sg.call_count == 4
        assert len(result['instances']) == 1
        assert result['suggested_max_requests'] == 2
        assert result['sampled_floor'] is False


class TestMismatch:
    def test_differing_model_is_reported(self):
        replicas = [_health('whisper-1', model='large-v3'),
                   _health('whisper-2', model='medium')]
        with patch('transcriber.safe_get', side_effect=replicas):
            result = probe_whisper_health(base_url=BASE, samples=2, refresh=True)
        assert result['mismatch'] == ['model']


class TestUnavailable:
    def test_404_returns_unavailable_only(self):
        with patch('transcriber.safe_get', return_value=_bad(404)):
            result = probe_whisper_health(base_url=BASE, samples=3, refresh=True)
        assert result == {'available': False}

    def test_404_backend_costs_the_full_sample_count(self):
        with patch('transcriber.safe_get', return_value=_bad(404)) as sg:
            probe_whisper_health(base_url=BASE, samples=5, refresh=True)
        assert sg.call_count == 5

    def test_transport_error_returns_unavailable_only(self):
        with patch('transcriber.safe_get',
                   side_effect=requests_lib.ConnectionError('refused')):
            result = probe_whisper_health(base_url=BASE, samples=3, refresh=True)
        assert result == {'available': False}

    def test_no_base_url_returns_unavailable(self):
        assert probe_whisper_health(base_url='') == {'available': False}

    def test_non_string_instance_is_skipped(self):
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = {'status': 'ok', 'instance': ['not', 'hashable'], 'model': 'large-v3'}
        with patch('transcriber.safe_get', return_value=r):
            result = probe_whisper_health(base_url=BASE, samples=2, refresh=True)
        assert result == {'available': False}


class TestMaxConcurrentFallback:
    def test_missing_max_concurrent_falls_back_to_instance_count(self):
        with patch('transcriber.safe_get', return_value=_health_no_max_concurrent('whisper-1')):
            result = probe_whisper_health(base_url=BASE, samples=1, refresh=True)
        assert result['suggested_max_requests'] == 1
        assert len(result['instances']) == 1

    def test_zero_max_concurrent_clamps_to_one(self):
        with patch('transcriber.safe_get', return_value=_health('whisper-1', max_concurrent=0)):
            result = probe_whisper_health(base_url=BASE, samples=1, refresh=True)
        assert result['suggested_max_requests'] == 1

    def test_negative_max_concurrent_clamps_to_one(self):
        with patch('transcriber.safe_get', return_value=_health('whisper-1', max_concurrent=-5)):
            result = probe_whisper_health(base_url=BASE, samples=1, refresh=True)
        assert result['suggested_max_requests'] == 1

    def test_bool_max_concurrent_is_ignored(self):
        with patch('transcriber.safe_get', return_value=_health('whisper-1', max_concurrent=True)):
            result = probe_whisper_health(base_url=BASE, samples=1, refresh=True)
        assert result['suggested_max_requests'] == 1

    def test_mixed_reporting_sums_fallback_and_reported_values(self):
        replicas = [_health('whisper-1', max_concurrent=5), _health_no_max_concurrent('whisper-2')]
        with patch('transcriber.safe_get', side_effect=replicas):
            result = probe_whisper_health(base_url=BASE, samples=2, refresh=True)
        assert len(result['instances']) == 2
        # Genuinely summed (5 + 1), not just the instance count (2).
        assert result['suggested_max_requests'] == 6

    def test_all_missing_max_concurrent_sums_one_per_instance(self):
        replicas = [_health_no_max_concurrent('whisper-1'), _health_no_max_concurrent('whisper-2')]
        with patch('transcriber.safe_get', side_effect=replicas):
            result = probe_whisper_health(base_url=BASE, samples=2, refresh=True)
        assert len(result['instances']) == 2
        assert result['suggested_max_requests'] == 2


class TestSampledFloor:
    def test_all_samples_distinct_reports_floor_true(self):
        replicas = [_health('whisper-1'), _health('whisper-2')]
        with patch('transcriber.safe_get', side_effect=replicas):
            result = probe_whisper_health(base_url=BASE, samples=2, refresh=True)
        assert result['sampled_floor'] is True

    def test_failed_samples_do_not_clear_the_floor_flag(self):
        # A wasted sample is not evidence the replica set was covered.
        replicas = [_health('whisper-1'), _bad(503), _health('whisper-2')]
        with patch('transcriber.safe_get', side_effect=replicas):
            result = probe_whisper_health(base_url=BASE, samples=3, refresh=True)
        assert len(result['instances']) == 2
        assert result['sampled_floor'] is True

    def test_a_lone_repeat_does_not_clear_the_floor_flag(self):
        # One repeat is not the sampling wrapping the replica set; a balancer
        # that is not round robin repeats without having shown every replica.
        replicas = [_health('whisper-1'), _health('whisper-2'), _health('whisper-1'),
                    _health('whisper-3')]
        with patch('transcriber.safe_get', side_effect=replicas):
            result = probe_whisper_health(base_url=BASE, samples=4, refresh=True)
        assert len(result['instances']) == 3
        assert result['sampled_floor'] is True

    def test_three_consecutive_repeats_clear_the_floor_flag(self):
        replicas = [_health('whisper-1'), _health('whisper-2')] + [_health('whisper-2')] * 3
        with patch('transcriber.safe_get', side_effect=replicas):
            result = probe_whisper_health(base_url=BASE, samples=5, refresh=True)
        assert result['sampled_floor'] is False


class TestHealthUrl:
    def test_trailing_slash_derives_same_health_url(self):
        with patch('transcriber.safe_get', return_value=_bad(404)) as sg:
            probe_whisper_health(base_url='http://transcriber:8001/v1/', samples=1, refresh=True)
        assert sg.call_args[0][0] == 'http://transcriber:8001/v1/health'

    def test_no_path_derives_health_at_root(self):
        with patch('transcriber.safe_get', return_value=_bad(404)) as sg:
            probe_whisper_health(base_url='http://transcriber:8001', samples=1, refresh=True)
        assert sg.call_args[0][0] == 'http://transcriber:8001/health'


class TestAuthHeader:
    def test_sends_bearer_header_when_api_key_given(self):
        with patch('transcriber.safe_get', return_value=_health('whisper-1')) as sg:
            probe_whisper_health(base_url=BASE, samples=1, api_key='sk-test', refresh=True)
        assert sg.call_args.kwargs['headers'] == {'Authorization': 'Bearer sk-test'}

    def test_no_api_key_sends_no_auth_header(self):
        with patch('transcriber.safe_get', return_value=_health('whisper-1')) as sg:
            probe_whisper_health(base_url=BASE, samples=1, refresh=True)
        assert sg.call_args.kwargs['headers'] == {}


class TestCaching:
    def test_second_call_within_ttl_reuses_cached_result(self):
        with patch('transcriber.safe_get', return_value=_health('whisper-1')) as sg:
            probe_whisper_health(base_url=BASE, samples=1)
            probe_whisper_health(base_url=BASE, samples=1)
        assert sg.call_count == 1

    def test_refresh_always_reprobes(self):
        with patch('transcriber.safe_get', return_value=_health('whisper-1')) as sg:
            probe_whisper_health(base_url=BASE, samples=1, refresh=True)
            probe_whisper_health(base_url=BASE, samples=1, refresh=True)
        assert sg.call_count == 2

    def test_a_failed_refresh_keeps_a_good_cached_probe(self):
        with patch('transcriber.safe_get', return_value=_health('whisper-1')):
            probe_whisper_health(base_url=BASE, samples=1)
        with patch('transcriber.safe_get', return_value=_bad(429)):
            assert probe_whisper_health(base_url=BASE, samples=1, refresh=True) == {
                'available': False}
        with patch('transcriber.safe_get', side_effect=AssertionError('should be cached')):
            assert probe_whisper_health(base_url=BASE, samples=1)['available'] is True

    def test_refresh_replaces_a_cached_negative(self):
        with patch('transcriber.safe_get', return_value=_bad(404)):
            assert probe_whisper_health(base_url=BASE, samples=1)['available'] is False
        with patch('transcriber.safe_get', return_value=_health('whisper-1')):
            probe_whisper_health(base_url=BASE, samples=1, refresh=True)
        with patch('transcriber.safe_get', side_effect=AssertionError('should be cached')):
            assert probe_whisper_health(base_url=BASE, samples=1)['available'] is True


class TestProbeBudget:
    def test_an_exhausted_budget_stops_after_the_first_sample(self):
        # A spent budget must not let the 8-sample default keep a worker for
        # samples * timeout against a hanging backend.
        with patch('transcriber.safe_get', return_value=_bad(503)) as sg:
            probe_whisper_health(base_url=BASE, samples=8, refresh=True, budget=0.0)
        assert sg.call_count == 1

    def test_a_generous_budget_takes_every_sample(self):
        with patch('transcriber.safe_get', return_value=_bad(503)) as sg:
            probe_whisper_health(base_url=BASE, samples=4, refresh=True, budget=600.0)
        assert sg.call_count == 4


class TestConcurrentProbes:
    def test_a_probe_in_flight_serves_the_last_good_result(self):
        # The caller's own cache read already missed, so an expired entry must
        # not blank the panel for as long as the running probe takes.
        with patch('transcriber.safe_get', return_value=_health('whisper-1')):
            probe_whisper_health(base_url=BASE, samples=1)
        transcriber._health_cache.clear()
        transcriber._health_inflight.add(BASE)
        try:
            with patch('transcriber.safe_get',
                       side_effect=AssertionError('must not probe')) as sg:
                result = probe_whisper_health(base_url=BASE, samples=1)
            assert sg.call_count == 0
        finally:
            transcriber._health_inflight.discard(BASE)
        assert result['available'] is True

    def test_a_probe_in_flight_on_another_backend_does_not_block(self):
        # The in-flight set is per backend: testing a new URL must not wait on
        # a probe hanging against the old one.
        transcriber._health_inflight.add(BASE)
        try:
            with patch('transcriber.safe_get', return_value=_health('whisper-9')) as sg:
                result = probe_whisper_health(
                    base_url='http://other-transcriber:8001/v1', samples=1, refresh=True)
        finally:
            transcriber._health_inflight.discard(BASE)
        assert sg.call_count == 1
        assert result['available'] is True

    def test_a_probe_in_flight_with_nothing_known_reports_unavailable(self):
        transcriber._health_last_good.pop(BASE, None)
        transcriber._health_inflight.add(BASE)
        try:
            with patch('transcriber.safe_get',
                       side_effect=AssertionError('must not probe')) as sg:
                result = probe_whisper_health(base_url=BASE, samples=1)
        finally:
            transcriber._health_inflight.discard(BASE)
        # Without the count the probe's own except Exception would swallow the
        # side effect and this would pass with the guard ignored entirely.
        assert sg.call_count == 0
        assert result == {'available': False}

    def test_a_stale_last_good_is_not_served_as_current(self):
        # A long-dead backend shown as healthy is worse than showing nothing.
        with patch('transcriber.safe_get', return_value=_health('whisper-1')):
            probe_whisper_health(base_url=BASE, samples=1)
        transcriber._health_cache.clear()
        stamped_at, result = transcriber._health_last_good[BASE]
        age = transcriber._HEALTH_LAST_GOOD_MAX_AGE_SECONDS + 1
        transcriber._health_last_good[BASE] = (stamped_at - age, result)
        transcriber._health_inflight.add(BASE)
        try:
            with patch('transcriber.safe_get', side_effect=AssertionError('must not probe')):
                assert probe_whisper_health(base_url=BASE, samples=1) == {'available': False}
        finally:
            transcriber._health_inflight.discard(BASE)

    def test_eviction_keeps_the_most_recent_entries(self):
        for i in range(transcriber._HEALTH_LAST_GOOD_MAX):
            with patch('transcriber.safe_get', return_value=_health(f'w-{i}')):
                probe_whisper_health(base_url=f'http://h{i}:8001/v1', samples=1, refresh=True)
        oldest = 'http://h0:8001/v1'
        newest = f'http://h{transcriber._HEALTH_LAST_GOOD_MAX - 1}:8001/v1'
        with patch('transcriber.safe_get', return_value=_health('w-new')):
            probe_whisper_health(base_url='http://h-new:8001/v1', samples=1, refresh=True)
        assert oldest not in transcriber._health_last_good
        assert newest in transcriber._health_last_good
        assert 'http://h-new:8001/v1' in transcriber._health_last_good

    def test_the_in_flight_marker_is_cleared_when_a_probe_raises(self):
        with patch('transcriber._probe_whisper_health', side_effect=RuntimeError('boom')):
            with pytest.raises(RuntimeError):
                probe_whisper_health(base_url=BASE, samples=1, refresh=True)
        assert BASE not in transcriber._health_inflight


class TestConcurrencyCoercion:
    def test_float_and_numeric_string_max_concurrent_are_counted(self):
        replicas = [_health('whisper-1', max_concurrent=4.0),
                    _health('whisper-2', max_concurrent='4')]
        with patch('transcriber.safe_get', side_effect=replicas):
            result = probe_whisper_health(base_url=BASE, samples=2, refresh=True)
        assert result['suggested_max_requests'] == 8

    def test_infinite_max_concurrent_does_not_raise(self):
        # json.loads accepts bare Infinity, and int(inf) raises OverflowError.
        replicas = [_health('whisper-1', max_concurrent=float('inf')),
                    _health('whisper-2', max_concurrent='1e400')]
        with patch('transcriber.safe_get', side_effect=replicas):
            result = probe_whisper_health(base_url=BASE, samples=2, refresh=True)
        assert result['suggested_max_requests'] == 2

    def test_zero_negative_and_bool_max_concurrent_floor_at_one(self):
        replicas = [_health('whisper-1', max_concurrent=0),
                    _health('whisper-2', max_concurrent=-3),
                    _health('whisper-3', max_concurrent=True)]
        with patch('transcriber.safe_get', side_effect=replicas):
            result = probe_whisper_health(base_url=BASE, samples=3, refresh=True)
        assert result['suggested_max_requests'] == 3
