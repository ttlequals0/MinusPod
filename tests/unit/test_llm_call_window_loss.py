"""A lost detection window is recorded by what lost it; a reasoning-truncated reply counts as a loss."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ad_detector import AdDetector, WindowResult
from llm_capabilities import PASS_AD_DETECTION_1
from llm_client import LLMResponse, ProviderRateLimitedError
from utils.circuit_breaker import CircuitBreakerOpen
from utils.llm_call import (
    LOSS_CONNECTIVITY,
    LOSS_OTHER,
    LOSS_RATE_LIMIT,
    LOSS_OUTPUT_TRUNCATED,
    LOSS_REASONING_EXHAUSTED,
    LOSS_SERVER_ERROR,
    OutputTruncatedError,
    ReasoningExhaustedError,
    call_llm,
    call_llm_for_window,
    window_loss_class,
)


class _FakeLLMClient:
    """Returns/raises each entry of ``results`` in sequence per call."""

    def __init__(self, results):
        self._results = list(results)
        self.calls = 0
        self.kwargs = []

    def messages_create(self, **kwargs):
        self.calls += 1
        self.kwargs.append(dict(kwargs))
        item = self._results.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _ProviderError(Exception):
    """Stands in for an SDK APIError; the code is in the text as the SDKs put it."""

    def __init__(self, message, status_code):
        super().__init__(f"Error code: {status_code} - {message}")
        self.status_code = status_code


@pytest.fixture
def run_ctx(temp_db):
    import run_context
    temp_db.create_podcast('show', 'https://example.com/a.xml', 'Show')
    ctx = run_context.begin('show', 'ep-1', run_id='run-1')
    yield ctx
    run_context.end(ctx)


def _kwargs(client, **overrides):
    kwargs = dict(
        llm_client=client, model='m', system_prompt='s', prompt='p',
        llm_timeout=30, max_retries=0, max_tokens=4096,
        slug='show', episode_id='ep-1',
        phase_key='detection', pass_name='ad_detection_pass_1',
        provider='anthropic',
    )
    kwargs.update(overrides)
    return kwargs


def _call(client, **overrides):
    """A plain call_llm, as category repair and chapters make it."""
    return call_llm(call_label='Repair 1', **_kwargs(client, **overrides))


def _window_call(client, **overrides):
    """A detection-window call, which opts into the blank-answer rule."""
    return call_llm_for_window(window_label='Window 1', **_kwargs(client, **overrides))


class TestLossClassification:
    def test_5xx_is_a_server_error_not_a_rate_limit(self):
        assert window_loss_class(_ProviderError('bad gateway', 502)) == LOSS_SERVER_ERROR

    def test_held_429_is_a_rate_limit(self):
        held = ProviderRateLimitedError('reset in 60s', retry_after_seconds=60)
        assert window_loss_class(held) == LOSS_RATE_LIMIT

    def test_unreachable_endpoint_is_connectivity(self):
        assert window_loss_class(ConnectionError('refused')) == LOSS_CONNECTIVITY

    def test_exhausted_budget_has_its_own_class(self):
        assert window_loss_class(
            ReasoningExhaustedError('spent')) == LOSS_REASONING_EXHAUSTED

    def test_unknown_error_falls_back(self):
        assert window_loss_class(ValueError('nope')) == LOSS_OTHER


class TestCircuitCooldownRetries:
    def test_main_retry_waits_for_live_breaker_after_triggering_error(
            self, run_ctx, monkeypatch):
        waits = []
        error = _ProviderError('upstream invalid response', 503)
        client = _FakeLLMClient([
            error,
            LLMResponse(content='{"ads": []}', model='m'),
        ])
        client.circuit_retry_after = lambda: 47.0
        monkeypatch.setattr('utils.llm_call.random.uniform', lambda a, b: 0.0)
        monkeypatch.setattr(
            'utils.llm_call._sleep_before_retry',
            lambda delay: waits.append(delay) or True,
        )

        response, last_error = _window_call(client, max_retries=1)

        assert last_error is None
        assert response.content == '{"ads": []}'
        # The margin past the breaker's 47.0s cooldown is why the wait is 48.0s.
        assert waits == [48.0]

    def test_fallback_retries_wait_for_open_breaker_and_remain_bounded(
            self, run_ctx, monkeypatch):
        waits = []
        client = _FakeLLMClient([
            _ProviderError('upstream invalid response', 503),
            CircuitBreakerOpen('review', 41.0),
            _ProviderError('upstream invalid response', 503),
        ])
        monkeypatch.setattr('utils.llm_call.random.uniform', lambda a, b: 0.0)
        monkeypatch.setattr(
            'utils.llm_call._sleep_before_retry',
            lambda delay: waits.append(delay) or True,
        )

        response, last_error = _window_call(client, max_retries=0)

        assert response is None
        assert last_error.status_code == 503
        # The margin past the breaker's 41.0s cooldown is why the wait is 42.0s.
        assert waits == [2, 42.0]
        assert client.calls == 3

    def test_shutdown_during_breaker_wait_stops_retry(self, run_ctx, monkeypatch):
        client = _FakeLLMClient([CircuitBreakerOpen('review', 60.0)])
        monkeypatch.setattr('utils.llm_call._sleep_before_retry', lambda delay: False)

        response, last_error = _window_call(client, max_retries=1)

        assert response is None
        assert isinstance(last_error, CircuitBreakerOpen)
        assert client.calls == 1

    def test_terminal_error_on_first_fallback_stops_retrying(
            self, run_ctx, monkeypatch):
        waits = []
        inconclusive = _ProviderError('Review is inconclusive', 422)
        client = _FakeLLMClient([
            _ProviderError('upstream invalid response', 503),
            inconclusive,
        ])
        monkeypatch.setattr(
            'utils.llm_call._sleep_before_retry',
            lambda delay: waits.append(delay) or True,
        )

        response, last_error = _window_call(client, max_retries=0)

        assert response is None
        assert last_error is inconclusive
        assert waits == [2]
        assert client.calls == 2


def _windows(n):
    return [{'start': i * 60.0, 'end': i * 60.0 + 60.0,
             'segments': [{'start': i * 60.0 + 1.0, 'end': i * 60.0 + 5.0,
                           'text': f'w{i}'}]}
            for i in range(n)]


def _failed_window(idx, error):
    return WindowResult(
        window_idx=idx, window_start=idx * 60.0, window_end=idx * 60.0 + 60.0,
        ads=[], raw_response=None, failed=True, last_error=error,
        loss_class=window_loss_class(error))


def _answered_window(idx):
    return WindowResult(
        window_idx=idx, window_start=idx * 60.0, window_end=idx * 60.0 + 60.0,
        ads=[], raw_response=f'win{idx}', failed=False, last_error=None)


class TestLoopTally:
    """The window loop owns the tally, so it never drifts from failed_windows."""

    def _run(self, results):
        detector = AdDetector(api_key='test-key')
        with patch.object(detector, '_run_windows', return_value=results):
            return detector._run_detection_pass(
                _windows(len(results)), pass_label='Detection', model='x',
                system_prompt='x', description_section='x', podcast_name='p',
                episode_title='e', audio_analysis=None, progress_callback=None,
                progress_base=0, progress_range=100, slug='s', episode_id='1',
                pass_name=PASS_AD_DETECTION_1, window_label_prefix='Window',
                validate_timestamps=False)

    def test_a_5xx_loss_is_tallied_apart_from_a_rate_limit(self):
        results = [_failed_window(0, _ProviderError('bad gateway', 502))]
        results += [_answered_window(i) for i in range(1, 8)]

        *_, losses, _addressing = self._run(results)

        assert losses == {LOSS_SERVER_ERROR: 1}

    def test_the_tally_matches_the_failed_window_count(self):
        held = ProviderRateLimitedError('reset in 60s', retry_after_seconds=60)
        results = [_failed_window(0, held),
                   _failed_window(1, _ProviderError('bad gateway', 502))]
        results += [_answered_window(i) for i in range(2, 10)]

        _ads, _raw, failed_windows, _failure, _cm, _ct, _cr, losses, _a = self._run(
            results)

        assert failed_windows == 2
        assert losses == {LOSS_RATE_LIMIT: 1, LOSS_SERVER_ERROR: 1}

    def test_a_clean_pass_tallies_nothing(self):
        *_, losses, _addressing = self._run([_answered_window(i) for i in range(3)])

        assert losses == {}


class TestExhaustedOutputBudget:
    def _truncated_blank(self):
        return LLMResponse(
            content='{}', model='m', finish_reason='length',
            usage={'input_tokens': 9540, 'output_tokens': 4111},
            reasoning_present=True,
        )

    def test_blank_object_at_max_tokens_is_a_failed_window(self, run_ctx, monkeypatch):
        monkeypatch.setattr('utils.llm_call.time.sleep', lambda s: None)
        monkeypatch.setattr('utils.llm_call._sleep_before_retry', lambda d: True)
        client = _FakeLLMClient([self._truncated_blank()] * 3)

        response, error = _window_call(client)

        assert response is None
        assert isinstance(error, ReasoningExhaustedError)

    def test_a_plain_call_takes_a_blank_object_as_an_answer(self, run_ctx):
        client = _FakeLLMClient([self._truncated_blank()])

        response, error = _call(client)

        assert error is None
        assert response.content == '{}'

    def test_blank_object_that_finished_cleanly_stays_a_clean_window(self, run_ctx):
        client = _FakeLLMClient([
            LLMResponse(content='{}', model='m', finish_reason='stop',
                        usage={'input_tokens': 100, 'output_tokens': 2}),
        ])

        response, error = _window_call(client)

        assert error is None
        assert response.content == '{}'

    def test_null_ads_key_at_max_tokens_with_no_reasoning_is_truncation(
            self, run_ctx, monkeypatch):
        """No reasoning to blame: the answer itself was cut off, and the same
        budget truncates again, so it is terminal rather than retried."""
        monkeypatch.setattr('utils.llm_call.time.sleep', lambda s: None)
        monkeypatch.setattr('utils.llm_call._sleep_before_retry', lambda d: True)
        client = _FakeLLMClient([
            LLMResponse(content='{"ads": null}', model='m', finish_reason='max_tokens'),
        ] * 3)

        _response, error = _window_call(client)

        assert isinstance(error, OutputTruncatedError)
        assert window_loss_class(error) == LOSS_OUTPUT_TRUNCATED
        assert client.calls == 1

    def test_explicit_empty_ads_list_is_an_answer(self, run_ctx):
        client = _FakeLLMClient([
            LLMResponse(content='{"ads": []}', model='m', finish_reason='max_tokens'),
        ])

        response, error = _window_call(client)

        assert error is None
        assert response.content == '{"ads": []}'

    def test_output_tokens_reaching_the_cap_counts_as_cut_off(self, run_ctx, monkeypatch):
        monkeypatch.setattr('utils.llm_call.time.sleep', lambda s: None)
        monkeypatch.setattr('utils.llm_call._sleep_before_retry', lambda d: True)
        client = _FakeLLMClient([
            LLMResponse(content='{}', model='m', finish_reason=None,
                        usage={'input_tokens': 10, 'output_tokens': 4096}),
        ] * 3)

        _response, error = _window_call(client)

        assert isinstance(error, OutputTruncatedError)

    def test_reasoning_tokens_in_usage_name_reasoning_as_the_cause(
            self, run_ctx, monkeypatch):
        """Providers that report reasoning only in usage still get the
        reasoning-off retry rather than a terminal truncation."""
        monkeypatch.setattr('utils.llm_call.time.sleep', lambda s: None)
        monkeypatch.setattr('utils.llm_call._sleep_before_retry', lambda d: True)
        client = _FakeLLMClient([
            LLMResponse(content='{}', model='m', finish_reason='length',
                        usage={'input_tokens': 10, 'output_tokens': 4096,
                               'reasoning_tokens': 4000}),
        ] * 3)

        _response, error = _window_call(client, max_retries=1, reasoning_effort='high')

        assert isinstance(error, ReasoningExhaustedError)
        assert client.kwargs[1]['reasoning_effort'] == 'none'

    @pytest.mark.parametrize('effort', ['none', None, 'low'])
    def test_an_exhausted_budget_buys_exactly_one_retry(
            self, run_ctx, monkeypatch, effort):
        """Same budget whatever the stage asked for: one retry, then the window
        is lost. The ladder would only buy the same truncated answer again."""
        monkeypatch.setattr('utils.llm_call.time.sleep', lambda s: None)
        monkeypatch.setattr('utils.llm_call._sleep_before_retry', lambda d: True)
        client = _FakeLLMClient([self._truncated_blank()] * 6)

        _response, error = _window_call(client, max_retries=2, reasoning_effort=effort)

        assert isinstance(error, ReasoningExhaustedError)
        assert client.calls == 2

    @pytest.mark.parametrize('effort', ['none', None, 'low'])
    def test_the_retry_after_an_exhausted_budget_can_still_answer(
            self, run_ctx, monkeypatch, effort):
        monkeypatch.setattr('utils.llm_call.time.sleep', lambda s: None)
        client = _FakeLLMClient([
            self._truncated_blank(),
            LLMResponse(content='[{"start": 1, "end": 2}]', model='m',
                        finish_reason='stop'),
        ])

        response, error = _window_call(client, max_retries=2, reasoning_effort=effort)

        assert error is None
        assert response.content == '[{"start": 1, "end": 2}]'
        assert client.calls == 2

    def test_an_exhausted_budget_on_the_last_attempt_still_retries(
            self, run_ctx, monkeypatch):
        """The one retry does not depend on where in the ladder the error
        lands; with max_retries=0 it is the only attempt there is."""
        monkeypatch.setattr('utils.llm_call.time.sleep', lambda s: None)
        monkeypatch.setattr('utils.llm_call._sleep_before_retry', lambda d: True)
        client = _FakeLLMClient([self._truncated_blank()] * 3)

        _response, error = _window_call(client, max_retries=0, reasoning_effort='high')

        assert isinstance(error, ReasoningExhaustedError)
        assert client.calls == 2

    def test_retry_turns_reasoning_off_the_way_it_does_for_empty_content(
            self, run_ctx, monkeypatch):
        monkeypatch.setattr('utils.llm_call.time.sleep', lambda s: None)
        client = _FakeLLMClient([
            self._truncated_blank(),
            LLMResponse(content='[{"start": 1, "end": 2}]', model='m',
                        finish_reason='stop'),
        ])

        _response, error = _window_call(client, max_retries=1, reasoning_effort='high')

        assert error is None
        assert client.kwargs[0]['reasoning_effort'] == 'high'
        assert client.kwargs[1]['reasoning_effort'] == 'none'

    def test_a_second_exhaustion_with_reasoning_off_stops_the_window(
            self, run_ctx, monkeypatch):
        monkeypatch.setattr('utils.llm_call.time.sleep', lambda s: None)
        monkeypatch.setattr('utils.llm_call._sleep_before_retry', lambda d: True)
        client = _FakeLLMClient([self._truncated_blank()] * 6)

        _response, error = _window_call(client, max_retries=2, reasoning_effort='high')

        assert isinstance(error, ReasoningExhaustedError)
        assert client.calls == 2


def _recovered_window(idx):
    return WindowResult(
        window_idx=idx, window_start=idx * 60.0, window_end=idx * 60.0 + 60.0,
        ads=[{'start': idx * 60.0 + 10.0, 'end': idx * 60.0 + 30.0,
              'confidence': 0.9, 'sponsor': f'recovered-{idx}'}],
        raw_response=f'recovered{idx}', failed=False, last_error=None)


class TestLostWindowSweep:
    """A window lost to a transient outage gets one sequential retry pass
    once the main loop finishes, but only when every loss is the kind an
    outage causes."""

    def _run(self, results, *, sweep_calls=None):
        detector = AdDetector(api_key='test-key')
        sweep_calls = sweep_calls if sweep_calls is not None else []

        def stub(*, window_idx, window, total_windows, **_kwargs):
            sweep_calls.append(window_idx)
            return _recovered_window(window_idx)

        with patch.object(detector, '_run_windows', return_value=results), \
             patch.object(detector, '_client_for_pass', return_value=SimpleNamespace()), \
             patch.object(detector, '_process_single_window', side_effect=stub):
            return detector._run_detection_pass(
                _windows(len(results)), pass_label='Detection', model='x',
                system_prompt='x', description_section='x', podcast_name='p',
                episode_title='e', audio_analysis=None, progress_callback=None,
                progress_base=0, progress_range=100, slug='s', episode_id='1',
                pass_name=PASS_AD_DETECTION_1, window_label_prefix='Window',
                validate_timestamps=False)

    def test_sweep_recovers_a_server_error_window(self):
        results = [_failed_window(0, _ProviderError('bad gateway', 502))]
        results += [_answered_window(i) for i in range(1, 4)]
        sweep_calls = []

        (ads, _raw, failed_windows, failure, _cm, _ct, _cr, losses, _a) = self._run(
            results, sweep_calls=sweep_calls)

        assert sweep_calls == [0]
        assert failure is None
        assert failed_windows == 0
        assert losses == {}
        assert any(ad['sponsor'] == 'recovered-0' for ad in ads)

    def test_sweep_skipped_when_a_lost_window_is_rate_limit(self):
        held = ProviderRateLimitedError('reset in 60s', retry_after_seconds=60)
        results = [_failed_window(0, _ProviderError('bad gateway', 502)),
                   _failed_window(1, held)]
        results += [_answered_window(i) for i in range(2, 8)]
        sweep_calls = []

        (_ads, _raw, failed_windows, _failure, _cm, _ct, _cr, losses, _a) = self._run(
            results, sweep_calls=sweep_calls)

        assert sweep_calls == []
        assert failed_windows == 2
        assert losses == {LOSS_SERVER_ERROR: 1, LOSS_RATE_LIMIT: 1}

    def test_sweep_skipped_when_all_windows_failed(self):
        results = [_failed_window(i, _ProviderError('bad gateway', 502))
                   for i in range(3)]
        sweep_calls = []

        self._run(results, sweep_calls=sweep_calls)

        assert sweep_calls == []

    def test_sweep_hold_defers_instead_of_crashing(self):
        """A rate limit hit mid-sweep must defer the episode like a held
        window in the main loop, not raise out of the pass. Whatever the
        sweep already recovered before the hold still gets merged in.
        `_process_single_window` reports the hold as a failed WindowResult
        rather than raising, so the sweep must read it off `last_error`."""
        held = ProviderRateLimitedError('resets in 900s', retry_after_seconds=900.0)
        results = [_failed_window(0, _ProviderError('bad gateway', 502)),
                   _failed_window(1, _ProviderError('bad gateway', 502))]
        results += [_answered_window(i) for i in range(2, 5)]
        detector = AdDetector(api_key='test-key')

        def stub(*, window_idx, window, total_windows, **_kwargs):
            if window_idx == 0:
                return _recovered_window(0)
            return _failed_window(1, held)

        with patch.object(detector, '_run_windows', return_value=results), \
             patch.object(detector, '_client_for_pass', return_value=SimpleNamespace()), \
             patch.object(detector, '_process_single_window', side_effect=stub):
            (_ads, raw, _fw, failure, _cm, _ct, _cr, _losses, _a) = (
                detector._run_detection_pass(
                    _windows(len(results)), pass_label='Detection', model='x',
                    system_prompt='x', description_section='x', podcast_name='p',
                    episode_title='e', audio_analysis=None, progress_callback=None,
                    progress_base=0, progress_range=100, slug='s', episode_id='1',
                    pass_name=PASS_AD_DETECTION_1, window_label_prefix='Window',
                    validate_timestamps=False))

        assert failure is not None
        assert failure['rate_limited_hold'] is True
        assert failure['retry_after_seconds'] == 900.0
        assert 'recovered0' in raw

    def test_sweep_recovered_windows_get_category_repair(self):
        """A swept window must go through the same repair as the main loop's results."""
        results = [_failed_window(0, _ProviderError('bad gateway', 502))]
        results += [_answered_window(i) for i in range(1, 3)]
        detector = AdDetector(api_key='test-key')
        repair_mock = MagicMock(side_effect=lambda **kw: 0)

        def stub(*, window_idx, window, total_windows, **_kwargs):
            return _recovered_window(window_idx)

        with patch.object(detector, '_run_windows', return_value=results), \
             patch.object(detector, '_client_for_pass', return_value=SimpleNamespace()), \
             patch.object(detector, '_process_single_window', side_effect=stub), \
             patch.object(detector, '_repair_window_categories', repair_mock):
            detector._run_detection_pass(
                _windows(len(results)), pass_label='Detection', model='x',
                system_prompt='x', description_section='x', podcast_name='p',
                episode_title='e', audio_analysis=None, progress_callback=None,
                progress_base=0, progress_range=100, slug='s', episode_id='1',
                pass_name=PASS_AD_DETECTION_1, window_label_prefix='Window',
                validate_timestamps=False, category_repair_enabled=True)

        repaired_labels = [c.kwargs['window_label'] for c in repair_mock.call_args_list]
        assert 'Window 1' in repaired_labels

    def test_repair_hold_still_counts_windows_judged_for_that_window(self):
        """Addressing stats fold in before category repair runs, so a repair
        hold still counts the window towards windows_judged."""
        import ad_detector as ad_detector_module

        held = ProviderRateLimitedError('resets in 60s', retry_after_seconds=60.0)
        results = [WindowResult(
            window_idx=0, window_start=0.0, window_end=60.0, ads=[],
            raw_response='w0', failed=False, last_error=None,
            compliant=True, ads_proposed=1)]
        detector = AdDetector(api_key='test-key')
        captured = []
        real_cls = ad_detector_module.AddressingStats

        def spy(*args, **kwargs):
            instance = real_cls(*args, **kwargs)
            captured.append(instance)
            return instance

        with patch.object(detector, '_run_windows', return_value=results), \
             patch.object(detector, '_client_for_pass', return_value=SimpleNamespace()), \
             patch.object(detector, '_repair_window_categories', side_effect=held), \
             patch.object(ad_detector_module, 'AddressingStats', side_effect=spy):
            detector._run_detection_pass(
                _windows(1), pass_label='Detection', model='x',
                system_prompt='x', description_section='x', podcast_name='p',
                episode_title='e', audio_analysis=None, progress_callback=None,
                progress_base=0, progress_range=100, slug='s', episode_id='1',
                pass_name=PASS_AD_DETECTION_1, window_label_prefix='Window',
                validate_timestamps=False, category_repair_enabled=True)

        assert captured[0].windows_judged == 1
