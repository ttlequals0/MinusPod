"""Tests for the detection-window failure-ratio hold threshold.

A quarter (default) of a pass's windows failing now returns the same
failure envelope as an all-windows-failed run, so processing.py fails
the episode as retryable instead of publishing with unexamined gaps.
"""
from unittest.mock import patch

import ad_detector
from ad_detector import AdDetector, WindowResult


def _make_windows(n: int):
    out = []
    for i in range(n):
        start = i * 60.0
        out.append({
            'start': start,
            'end': start + 60.0,
            'segments': [{'start': start + 1.0, 'end': start + 5.0, 'text': f'w{i}'}],
        })
    return out


def _window_result(idx, *, failed):
    if failed:
        return WindowResult(
            window_idx=idx, window_start=idx * 60.0, window_end=idx * 60.0 + 60.0,
            ads=[], raw_response=None, failed=True, last_error=RuntimeError('boom'),
        )
    return WindowResult(
        window_idx=idx, window_start=idx * 60.0, window_end=idx * 60.0 + 60.0,
        ads=[{
            'start': idx * 60.0 + 10.0, 'end': idx * 60.0 + 30.0,
            'confidence': 0.9, 'sponsor': f'sponsor-{idx}',
        }],
        raw_response=f'win{idx}', failed=False, last_error=None,
    )


def _run_pass(detector, num_windows, failed_idxs):
    windows = _make_windows(num_windows)
    results = [_window_result(i, failed=i in failed_idxs) for i in range(num_windows)]
    with patch.object(detector, '_run_windows', return_value=results):
        return detector._run_detection_pass(
            windows,
            pass_label='Detection',
            model='x',
            system_prompt='x',
            description_section='x',
            podcast_name='p',
            episode_title='e',
            audio_analysis=None,
            progress_callback=None,
            progress_base=0,
            progress_range=100,
            slug='s',
            episode_id='1',
            pass_name='ad_detection_1',
            window_label_prefix='Window',
            validate_timestamps=False,
        )


class TestWindowFailureThreshold:
    def test_over_threshold_returns_failure_envelope(self):
        # 2 of 6 windows failed (33% > 25% default) -> failure envelope, no ads.
        detector = AdDetector(api_key='test-key')
        (final_ads, all_raw_responses, failed_windows, failure,
         category_missing, category_total, category_repaired,
         windows_judged, windows_compliant) = _run_pass(
            detector, 6, {0, 1})

        assert failure is not None
        assert final_ads == []
        assert failed_windows == 2
        assert failure['status'] == 'failed'
        assert failure['windows_total'] == 6
        assert failure['windows_failed'] == 2
        assert '2/6 detection windows failed' in failure['error']

    def test_under_threshold_publishes_partial(self):
        # 1 of 6 windows failed (17% < 25% default) -> ads returned, no envelope.
        detector = AdDetector(api_key='test-key')
        (final_ads, all_raw_responses, failed_windows, failure,
         category_missing, category_total, category_repaired,
         windows_judged, windows_compliant) = _run_pass(
            detector, 6, {0})

        assert failure is None
        assert failed_windows == 1
        # 5 succeeded windows each contribute one ad.
        assert len(final_ads) == 5

    def test_ratio_one_disables(self, monkeypatch):
        # ratio=1.0 restores legacy publish-anyway behavior: only an
        # all-windows-failed run still triggers the envelope.
        monkeypatch.setattr(ad_detector, '_resolve_max_failed_window_ratio',
                            lambda: 1.0)
        detector = AdDetector(api_key='test-key')

        (final_ads, _all_raw, failed_windows, failure,
         _cm, _ct, _cr, _wj, _wc) = _run_pass(detector, 6, {0, 1, 2, 3, 4})
        assert failure is None
        assert failed_windows == 5
        assert len(final_ads) == 1

        (final_ads, _all_raw, failed_windows, failure,
         _cm, _ct, _cr, _wj, _wc) = _run_pass(detector, 6, {0, 1, 2, 3, 4, 5})
        assert failure is not None
        assert failed_windows == 6

    def test_all_windows_failed_message_unchanged(self):
        # All-failed wording stays the pre-existing phrasing (no "N/M" needed
        # since N == M), keeping any log/alert scraping on that text intact.
        detector = AdDetector(api_key='test-key')
        (_ads, _raw, _fw, failure, *_rest) = _run_pass(detector, 3, {0, 1, 2})

        assert failure is not None
        assert 'All 3 detection windows failed' in failure['error']


class TestResolveMaxFailedWindowRatio:
    """The threshold is runtime-tunable through the env-backed settings seam."""

    def _resolve(self, monkeypatch, db_value):
        monkeypatch.setattr('llm_client._get_cached_setting',
                            lambda key: db_value)
        return ad_detector._resolve_max_failed_window_ratio()

    def test_env_default_when_no_db_row(self, monkeypatch):
        monkeypatch.delenv('AD_DETECTION_MAX_FAILED_WINDOW_RATIO', raising=False)
        assert self._resolve(monkeypatch, None) == 0.25

    def test_env_value_seeds_the_default(self, monkeypatch):
        monkeypatch.setenv('AD_DETECTION_MAX_FAILED_WINDOW_RATIO', '0.5')
        assert self._resolve(monkeypatch, None) == 0.5

    def test_db_value_wins_over_env(self, monkeypatch):
        monkeypatch.setenv('AD_DETECTION_MAX_FAILED_WINDOW_RATIO', '0.5')
        assert self._resolve(monkeypatch, '0.1') == 0.1

    def test_out_of_range_db_value_is_clamped(self, monkeypatch):
        assert self._resolve(monkeypatch, '4.2') == 1.0
        assert self._resolve(monkeypatch, '-1') == 0.0

    def test_junk_db_value_falls_back_to_the_parse_time_default(self, monkeypatch):
        assert self._resolve(monkeypatch, 'not-a-number') == (
            ad_detector.AD_DETECTION_MAX_FAILED_WINDOW_RATIO)

    def test_junk_env_value_falls_back_to_the_registered_default(self, monkeypatch):
        monkeypatch.setenv('AD_DETECTION_MAX_FAILED_WINDOW_RATIO', 'junk')
        assert self._resolve(monkeypatch, None) == 0.25
