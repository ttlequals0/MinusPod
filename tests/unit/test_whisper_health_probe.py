"""Tests for the Whisper backend health probe (#734)."""
from unittest.mock import patch, MagicMock

import requests as requests_lib

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('whisper_health_test_', passphrase='whisper-health-test-pass')

from transcriber import probe_whisper_health  # noqa: E402

BASE = 'http://transcriber:8001/v1'


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


def _bad(status=404):
    r = MagicMock()
    r.status_code = status
    return r


class TestDistinctInstances:
    def test_three_distinct_instances_sum_max_concurrent(self):
        replicas = [_health('whisper-1'), _health('whisper-2'), _health('whisper-3')]
        with patch('transcriber.safe_get', side_effect=replicas) as sg:
            result = probe_whisper_health(base_url=BASE, samples=3)
        assert result['available'] is True
        assert sg.call_args[0][0] == f'{BASE}/health'
        assert [i['instance'] for i in result['instances']] == [
            'whisper-1', 'whisper-2', 'whisper-3']
        assert result['suggested_max_requests'] == 3
        assert result['mismatch'] == []


class TestEarlyStop:
    def test_repeated_instance_stops_after_two_consecutive_repeats(self):
        replicas = [_health('whisper-1', max_concurrent=2)] * 5
        with patch('transcriber.safe_get', side_effect=replicas) as sg:
            result = probe_whisper_health(base_url=BASE, samples=5)
        assert sg.call_count == 3
        assert len(result['instances']) == 1
        assert result['suggested_max_requests'] == 2


class TestMismatch:
    def test_differing_model_is_reported(self):
        replicas = [_health('whisper-1', model='large-v3'),
                   _health('whisper-2', model='medium')]
        with patch('transcriber.safe_get', side_effect=replicas):
            result = probe_whisper_health(base_url=BASE, samples=2)
        assert result['mismatch'] == ['model']


class TestUnavailable:
    def test_404_returns_unavailable_only(self):
        with patch('transcriber.safe_get', return_value=_bad(404)):
            result = probe_whisper_health(base_url=BASE, samples=3)
        assert result == {'available': False}

    def test_transport_error_returns_unavailable_only(self):
        with patch('transcriber.safe_get',
                   side_effect=requests_lib.ConnectionError('refused')):
            result = probe_whisper_health(base_url=BASE, samples=3)
        assert result == {'available': False}

    def test_no_base_url_returns_unavailable(self):
        assert probe_whisper_health(base_url='') == {'available': False}


class TestMaxConcurrentFallback:
    def test_missing_max_concurrent_falls_back_to_instance_count(self):
        r = MagicMock()
        r.status_code = 200
        r.json.return_value = {
            'status': 'ok', 'instance': 'whisper-1', 'model': 'large-v3',
            'device': 'cuda', 'compute_type': 'float16',
        }
        with patch('transcriber.safe_get', return_value=r):
            result = probe_whisper_health(base_url=BASE, samples=1)
        assert result['suggested_max_requests'] == 1
        assert len(result['instances']) == 1
