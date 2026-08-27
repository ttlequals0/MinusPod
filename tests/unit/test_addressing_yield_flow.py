"""Yield counters must flow from window results into AddressingStats.

The compliance metric alone reads 100% for both addressing modes; these
counters are what makes the A/B comparable. Modeled on
test_window_failure_threshold's _run_pass harness.
"""
from unittest.mock import patch

from ad_detector import AdDetector, WindowResult


def _make_windows(n):
    out = []
    for i in range(n):
        start = i * 60.0
        out.append({
            'start': start,
            'end': start + 60.0,
            'segments': [{'start': start + 1.0, 'end': start + 5.0, 'text': f'w{i}'}],
        })
    return out


def _result(idx, *, proposed, kept, invalid_ref=0, out_of_window=0,
            too_long=0, compliant=True, failed=False):
    if failed:
        return WindowResult(
            window_idx=idx, window_start=idx * 60.0,
            window_end=idx * 60.0 + 60.0, ads=[], raw_response=None,
            failed=True, last_error=RuntimeError('boom'))
    ads = [{'start': idx * 60.0 + 10.0 + j, 'end': idx * 60.0 + 30.0 + j,
            'confidence': 0.9, 'sponsor': f's{idx}-{j}'} for j in range(kept)]
    return WindowResult(
        window_idx=idx, window_start=idx * 60.0,
        window_end=idx * 60.0 + 60.0, ads=ads,
        raw_response=f'win{idx}', failed=False, last_error=None,
        addressing_mode='segment_ids', compliant=compliant,
        ads_proposed=proposed, dropped_invalid_ref=invalid_ref,
        dropped_out_of_window=out_of_window, dropped_too_long=too_long)


def _run(detector, results):
    windows = _make_windows(len(results))
    with patch.object(detector, '_run_windows', return_value=results):
        return detector._run_detection_pass(
            windows, pass_label='Detection', pass_name='ad_detection_1',
            model='x', system_prompt='x',
            description_section='x', podcast_name='p', episode_title='e',
            audio_analysis=None, progress_callback=None, progress_base=0,
            progress_range=100, slug='s', episode_id='1',
            window_label_prefix='Window', validate_timestamps=False)


def test_counters_aggregate_across_windows():
    detector = AdDetector(api_key='test-key')
    results = [
        _result(0, proposed=3, kept=2, invalid_ref=1),
        _result(1, proposed=2, kept=1, out_of_window=1),
        _result(2, proposed=1, kept=0, too_long=1, compliant=False),
    ]
    *_, addressing = _run(detector, results)
    assert addressing.windows_judged == 3
    assert addressing.windows_compliant == 2
    assert addressing.ads_proposed == 6
    assert addressing.ads_kept == 3
    assert addressing.dropped_invalid_ref == 1
    assert addressing.dropped_out_of_window == 1
    assert addressing.dropped_too_long == 1


def test_failed_windows_contribute_nothing():
    # 1 failed of 5 stays under the 25% failure-ratio hold, so the pass
    # publishes and the sample must cover only the judged windows.
    detector = AdDetector(api_key='test-key')
    results = [
        _result(0, proposed=4, kept=4),
        _result(1, proposed=0, kept=0, failed=True),
        _result(2, proposed=1, kept=1),
        _result(3, proposed=0, kept=0),
        _result(4, proposed=0, kept=0),
    ]
    *_, addressing = _run(detector, results)
    assert addressing.windows_judged == 4
    assert addressing.ads_proposed == 5
    assert addressing.ads_kept == 5
