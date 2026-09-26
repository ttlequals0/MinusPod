"""Window-level guards on what an LLM detection window is allowed to return."""
import logging
from unittest.mock import MagicMock, patch

from tests.app_bootstrap import bootstrap

bootstrap('window_guards_test_')

from ad_detector import AdDetector, _known_sponsor_pattern
from ad_detector.prompts import _normalize_ad, parse_ads_from_response
from llm_capabilities import PASS_AD_DETECTION_1, PASS_AD_DETECTION_2
from sponsor_normalize import extract_description_sponsors
from text_pattern_matcher import TextMatch
from utils.text import word_boundary_re
from verification_pass import VerificationPass


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
                pass_name=PASS_AD_DETECTION_1, **kwargs):
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
            **kwargs,
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


# Production shape: a 159 s window at 0.99 whose reason names the brand.
_LONG_START, _LONG_END = 3521.7, 3680.7
_ACME = word_boundary_re(['Acme Pet Food'])


def _long_window(**fields):
    ad = {'confidence': 0.99,
          'reason': 'Host talks through the Acme Pet Food kibble lineup'}
    ad.update(fields)
    return ad


def _registry(*names):
    registry = MagicMock()
    registry.find_sponsor_in_text.side_effect = lambda text: next(
        (n for n in names if n.lower() in text.lower()), None)
    return registry


def test_normalize_ad_accepts_episode_pattern_sponsor(caplog):
    kept = _normalize_ad(_long_window(), _LONG_START, _LONG_END,
                         episode_sponsor_re=_ACME)
    assert kept is not None
    assert (kept['start'], kept['end']) == (_LONG_START, _LONG_END)

    with caplog.at_level(logging.INFO, logger='podcast.claude'):
        dropped = _normalize_ad(_long_window(), _LONG_START, _LONG_END,
                                episode_sponsor_re=None)
    assert dropped is None
    assert 'no sponsor identified in reason' in caplog.text


def test_normalize_ad_episode_sponsor_in_start_text_counts():
    ad = _long_window(reason='Host talks through a kibble lineup',
                      start_text='acme pet food makes it easy')
    assert _normalize_ad(ad, _LONG_START, _LONG_END,
                         episode_sponsor_re=_ACME) is not None


def test_registry_name_in_a_quote_does_not_admit_a_long_window():
    ad = {'confidence': 0.9, 'reason': 'Hosts discuss the week in review',
          'end_text': 'Indeed, that is the story'}
    start, end = 1000.0, 1150.0
    registry = _registry('Indeed')

    assert _normalize_ad(ad, start, end, sponsor_service=registry) is None
    ad['reason'] = 'Indeed hiring spot'
    assert _normalize_ad(ad, start, end, sponsor_service=registry) is not None


def test_episode_sponsor_in_end_text_counts():
    ad = _long_window(reason='Host talks through a kibble lineup',
                      end_text='that is Acme Pet Food dot com')
    assert _normalize_ad(ad, _LONG_START, _LONG_END,
                         episode_sponsor_re=_ACME) is not None


def test_episode_sponsor_match_is_whole_word():
    ad = _long_window(reason='Hosts recap what happened on Sunday at length')
    assert _normalize_ad(ad, _LONG_START, _LONG_END,
                         episode_sponsor_re=word_boundary_re(['Sun'])) is None


def test_parse_ads_from_response_forwards_episode_sponsors():
    response = ('[{"start": 3521.7, "end": 3680.7, "confidence": 0.99, '
                '"reason": "Host talks through the Acme Pet Food kibble lineup"}]')
    assert parse_ads_from_response(response) == []
    assert len(parse_ads_from_response(
        response, episode_sponsor_re=_ACME)) == 1


def test_window_passes_episode_sponsors_to_the_gate():
    response = ('[{"start": 20.0, "end": 179.0, "confidence": 0.99, '
                '"reason": "Host talks through the Acme Pet Food kibble lineup"}]')
    window = (0.0, 600.0)

    assert _run_window(response, window=window).ads == []
    kept = _run_window(response, window=window,
                       episode_sponsor_re=_ACME)
    assert len(kept.ads) == 1


def test_known_sponsor_pattern_collects_matches_and_description():
    extract_description_sponsors.cache_clear()
    ads = [
        {'detection_stage': 'fingerprint', 'sponsor': 'Acme Pet Food'},
        {'detection_stage': 'text_pattern', 'sponsor': 'Globex'},
        {'detection_stage': 'text_pattern', 'sponsor': None},
        {'detection_stage': 'text_pattern', 'sponsor': 'Today'},
    ]
    description = 'Sponsored by <a href="https://www.umbrellacorp.com/show">Umbrella</a>'

    pattern = _known_sponsor_pattern(ads, description)

    for name in ('acme pet food', 'Globex', 'UMBRELLACORP'):
        assert pattern.search(f'thanks to {name} today')
    # A common word stored as a sponsor must not admit a content window.
    assert not pattern.search('what happened today')


def test_pass2_sponsor_pattern_takes_every_pass1_cut_sponsor():
    response = ('[{"start": 20.0, "end": 179.0, "confidence": 0.99, '
                '"reason": "Host talks through the Initech hiring platform"}]')
    pass1_cuts = [{'start': 3492.9, 'end': 3544.2,
                   'detection_stage': 'dai_differential', 'sponsor': 'Initech'}]

    kept = _run_verification(response, pass1_cuts=pass1_cuts)
    assert [(a['start'], a['end']) for a in kept['ads']] == [(20.0, 179.0)]


def test_description_sponsors_are_whole_words():
    extract_description_sponsors.cache_clear()
    description = ('A keepsake from our honeymoons, a romance at the factory, '
                   'and a calming walk. Brought to you by Squarespace.')
    assert extract_description_sponsors(description) == frozenset({'squarespace'})


def test_known_sponsor_pattern_is_none_without_sponsors():
    assert _known_sponsor_pattern([], None) is None


def test_description_sponsors_extract_once_per_description(caplog):
    extract_description_sponsors.cache_clear()
    description = 'Thanks to <a href="https://globex.com/show">Globex</a>'
    with caplog.at_level(logging.INFO):
        _known_sponsor_pattern([], description)
        _known_sponsor_pattern([], description)
    assert caplog.text.count('Extracted sponsors from description') == 1


class _PatternDb:
    def get_false_positive_corrections(self, podcast_id, episode_id):
        return []

    def get_podcast_by_slug(self, slug):
        return {'id': 1}

    def get_podcast_false_positive_texts(self, slug):
        return []

    def get_setting(self, key):
        return None

    def get_setting_float(self, key, default):
        return default

    def resolve_segment_actions(self, slug):
        return None


class _AcmeMatcher:
    def find_matches(self, *args, **kwargs):
        return [TextMatch(pattern_id=7, start=3492.9, end=3544.2,
                          confidence=0.95, sponsor='Acme Pet Food',
                          match_type='intro', category=None)]


def test_process_transcript_hands_known_sponsors_to_the_llm_pass():
    extract_description_sponsors.cache_clear()
    detector = AdDetector(api_key='test-key')
    detector.db = _PatternDb()
    detector.audio_fingerprinter = None
    detector.text_pattern_matcher = _AcmeMatcher()
    detector.pattern_service = None
    segments = [{'start': 3400.0, 'end': 3700.0, 'text': 'show talk'}]

    # Pass 1 trusts only pattern and fingerprint sponsors, not a differential ad's.
    differential = {'start': 3600.0, 'end': 3650.0, 'confidence': 0.9,
                    'detection_stage': 'dai_differential', 'sponsor': 'Initech'}

    with patch.object(detector, 'initialize_client'), \
         patch('ad_detector.dai_differential_ads', return_value=[differential]), \
         patch.object(detector, 'detect_ads',
                      return_value={'ads': [], 'status': 'success'}) as detect:
        detector.process_transcript(
            segments, 'Example Podcast', 'Episode One',
            slug='example-podcast', episode_id='a1b2c3d4e5f6',
            episode_description='Thanks to <a href="https://globex.com/show">Globex</a>',
            podcast_id='example-podcast', skip_patterns=False,
            audio_path=None, dai_differential=MagicMock(), keep_content=False)

    pattern = detect.call_args.kwargs['episode_sponsor_re']
    assert pattern.search('Acme Pet Food') and pattern.search('globex')
    assert not pattern.search('Initech is hiring')


def _run_verification(response_text, **kwargs):
    detector = AdDetector(api_key='test-key')
    segments = [{'start': float(t), 'end': float(t) + 10.0, 'text': 'show talk'}
                for t in range(0, 600, 10)]
    with patch.object(detector, 'initialize_client'), \
         patch.object(detector, '_effective_addressing_mode',
                      return_value=('timestamps', 'timestamps')), \
         patch.object(detector, 'get_verification_prompt', return_value='v'), \
         patch.object(detector, 'get_verification_model', return_value='m'), \
         patch.object(detector, '_build_known_pattern_hint', return_value=''), \
         patch.object(detector, '_resolve_segment_action_map', return_value=None), \
         patch.object(detector, '_call_llm_for_window',
                      return_value=(_StubResponse(response_text), None)):
        return detector.run_verification_detection(
            segments, slug='example-podcast', episode_id='a1b2c3d4e5f6',
            **kwargs)


def test_verification_keeps_long_window_naming_a_pass1_pattern_sponsor():
    response = ('[{"start": 20.0, "end": 179.0, "confidence": 0.99, '
                '"reason": "Host talks through the Acme Pet Food kibble lineup"}]')
    pass1_cuts = [{'start': 3492.9, 'end': 3544.2,
                   'detection_stage': 'text_pattern', 'sponsor': 'Acme Pet Food'}]

    kept = _run_verification(response, pass1_cuts=pass1_cuts)
    assert [(a['start'], a['end']) for a in kept['ads']] == [(20.0, 179.0)]

    dropped = _run_verification(response, pass1_cuts=[], episode_description='')
    assert dropped['ads'] == []


def test_verification_pass_forwards_pass1_cuts():
    detector = MagicMock()
    detector.run_verification_detection.return_value = {'ads': [], 'status': 'success'}
    analyzer = MagicMock()
    analyzer.analyze.return_value.signals = []
    analyzer.analyze.return_value.get_signals_by_type.return_value = []
    cuts = [{'start': 30.0, 'end': 60.0, 'detection_stage': 'text_pattern',
             'sponsor': 'Acme Pet Food'}]
    VerificationPass(ad_detector=detector, transcriber=MagicMock(),
                     audio_analyzer=analyzer).verify(
        processed_audio_path='/nonexistent.mp3', podcast_name='Example Podcast',
        episode_title='Episode One', slug='example-podcast',
        episode_id='a1b2c3d4e5f6', pass1_cuts=cuts,
        original_segments=[{'start': 0.0, 'end': 100.0, 'text': 'hello'}])

    assert detector.run_verification_detection.call_args.kwargs['pass1_cuts'] is cuts
