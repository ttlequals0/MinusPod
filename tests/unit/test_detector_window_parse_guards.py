"""Window-level guards on what an LLM detection window is allowed to return."""
from unittest.mock import patch

from tests.app_bootstrap import bootstrap

bootstrap('window_guards_test_')

from ad_detector import AdDetector
from ad_detector.prompts import parse_ads_from_response
from llm_capabilities import PASS_AD_DETECTION_1, PASS_AD_DETECTION_2


class _StubResponse:
    """LLMResponse-shaped duck: the parse path reads .content."""

    def __init__(self, content):
        self.content = content
        self.usage = {}


def _window(start, end):
    return {
        'start': start, 'end': end,
        'segments': [{'start': start, 'end': start + 1.0, 'text': 'hello'}],
    }


def _run_window(response_text, *, window=(0.0, 60.0),
                pass_name=PASS_AD_DETECTION_1):
    detector = AdDetector(api_key='test-key')

    def fake_call(**_kw):
        return _StubResponse(response_text), None

    window = window if isinstance(window, dict) else _window(*window)
    with patch.object(detector, '_call_llm_for_window', side_effect=fake_call):
        return detector._process_single_window(
            window_idx=0, window=window, total_windows=1,
            model='m', system_prompt='sys', description_section='',
            podcast_name='p', episode_title='t',
            audio_enforcer=None, audio_analysis=None,
            llm_timeout=30, max_retries=1,
            slug='s', episode_id='e', pass_name=pass_name,
            window_label_prefix='Window', validate_timestamps=False,
        )


def _ad(*bounds):
    """One JSON ads array; every (start, end) pair given becomes a proposal."""
    spans = zip(bounds[::2], bounds[1::2], strict=True)
    return '[' + ', '.join(
        f'{{"start": {start}, "end": {end}, "confidence": 0.9, '
        f'"sponsor": "Acme", "reason": "Acme promo code read"}}'
        for start, end in spans) + ']'


def test_pass1_window_ads_carry_the_llm_stage():
    """Unstamped members read as measured evidence at the reviewer's merge
    protection check, freezing every trim on a merged ad (issue #750)."""
    result = _run_window(_ad(10.0, 30.0))

    assert [a['detection_stage'] for a in result.ads] == ['claude']


def test_verification_window_ads_carry_the_verification_stage():
    result = _run_window(_ad(10.0, 30.0), pass_name=PASS_AD_DETECTION_2)

    assert [a['detection_stage'] for a in result.ads] == ['verification']


def test_a_span_overrunning_its_window_inside_the_tolerance_keeps_its_head():
    """An ad straddles the window edge; the neighbouring window saw the rest of
    it, and the cross-window merge re-unions the two."""
    result = _run_window(_ad(550.0, 700.0), window=(600.0, 1200.0))

    assert len(result.ads) == 1
    assert (result.ads[0]['start'], result.ads[0]['end']) == (550.0, 700.0)
    assert result.dropped_out_of_window == 0


def test_a_span_starting_far_outside_the_window_is_dropped_and_counted():
    """Beyond the 120 s placement tolerance the model is extrapolating."""
    result = _run_window(_ad(100.0, 130.0), window=(600.0, 1200.0))

    assert result.ads == []
    assert result.dropped_out_of_window == 1


def test_a_segment_straddling_the_window_start_widens_the_clamp():
    """The window carries that segment's text and timestamps, so an ad opening
    inside it keeps its head. 472 s is below the tolerance bound on the window
    itself (480 s); only the segment reaching back to 470 s vouches for it."""
    window = {'start': 600.0, 'end': 1200.0,
              'segments': [{'start': 470.0, 'end': 540.0, 'text': 'hello'},
                           {'start': 540.0, 'end': 610.0, 'text': 'there'}]}
    result = _run_window(_ad(472.0, 700.0), window=window)

    assert len(result.ads) == 1
    assert (result.ads[0]['start'], result.ads[0]['end']) == (472.0, 700.0)
    assert result.dropped_out_of_window == 0


def test_span_running_past_its_window_end_keeps_its_tail_inside_the_tolerance():
    """Length is judged on the raw span, and a tail inside the 120 s tolerance
    is an ad that outlasts the window, not extrapolation."""
    result = _run_window(_ad(900.0, 1300.0), window=(600.0, 1200.0))

    assert len(result.ads) == 1
    assert (result.ads[0]['start'], result.ads[0]['end']) == (900.0, 1300.0)
    assert result.dropped_too_long == 0


def test_a_span_opening_past_the_transcript_keeps_what_the_tolerance_allows():
    """Nothing in the window transcript reaches 3160 s, so only the placement
    tolerance vouches for the span; past its edge the tail is cut."""
    result = _run_window(_ad(3160.0, 3220.0), window=(2522.0, 3122.0))

    assert len(result.ads) == 1
    assert (result.ads[0]['start'], result.ads[0]['end']) == (3160.0, 3220.0)


def test_a_tail_beyond_the_tolerance_is_clamped_to_its_edge():
    result = _run_window(_ad(3160.0, 3260.0), window=(2522.0, 3122.0))

    assert len(result.ads) == 1
    assert (result.ads[0]['start'], result.ads[0]['end']) == (3160.0, 3242.0)
    assert result.dropped_out_of_window == 0


def test_an_over_long_raw_span_is_refused_before_it_is_clamped():
    """520 s raw, 140 s once clamped to the window's tolerance bound of 480 s:
    clamping first would turn a proposal the 420 s cap refuses into an
    acceptable cut."""
    result = _run_window(_ad(100.0, 620.0), window=(600.0, 1200.0))

    assert result.ads == []
    assert result.dropped_too_long == 1
    assert result.dropped_out_of_window == 0


def test_whole_episode_span_is_refused_for_length():
    """Judged raw, so no clamp can trim it into acceptance: the closing window
    is short enough that its tolerance bounds hold only 309 s."""
    result = _run_window(_ad(0.0, 1789.1), window=(1600.0, 1789.1))

    assert result.ads == []
    assert result.dropped_out_of_window == 0
    assert result.dropped_too_long == 1


def test_a_short_span_at_the_window_edge_is_kept_for_the_merge():
    """No duration drop here: the counters explain every discarded proposal,
    and the duration floors downstream rule on what survives."""
    result = _run_window(_ad(596.0, 602.0), window=(600.0, 1200.0))

    assert len(result.ads) == 1
    assert (result.ads[0]['start'], result.ads[0]['end']) == (596.0, 602.0)
    assert result.dropped_out_of_window == 0


def test_every_discarded_proposal_lands_in_exactly_one_counter():
    result = _run_window(
        _ad(100.0, 1000.0, 100.0, 130.0, 650.0, 700.0), window=(600.0, 1200.0))

    assert len(result.ads) == 1
    assert result.ads_proposed == 3
    assert (result.ads_proposed - len(result.ads)
            == result.dropped_too_long + result.dropped_out_of_window
            + result.dropped_invalid_ref)


def test_null_ad_object_is_discarded():
    """Verification models answer "nothing here" with a zero-length ad."""
    ads = parse_ads_from_response(
        '[{"start": 0.0, "end": 0.0, '
        '"reason": "No promotional content found"}]')

    assert ads == []
