"""Segment-ID addressing: rendering side."""
import logging
from unittest.mock import MagicMock, patch

from ad_detector import AdDetector
from ad_validator import AdValidator, Decision
from ad_detector.prompts import (
    SEGMENT_ID_SYSTEM_SECTION, format_window_prompt,
    parse_id_ads_from_response, resolve_segment_id_ads,
)


def _detector(mode):
    d = AdDetector.__new__(AdDetector)
    d.db = MagicMock()
    d.db.get_setting.side_effect = (
        lambda key: mode if key == 'ad_addressing_mode' else None)
    return d


def test_mode_resolution():
    assert _detector('segment_ids')._resolve_addressing_mode() == 'segment_ids'
    assert _detector('timestamps')._resolve_addressing_mode() == 'timestamps'
    assert _detector('SEGMENT_IDS ')._resolve_addressing_mode() == 'segment_ids'
    assert _detector('bogus')._resolve_addressing_mode() == 'timestamps'
    assert _detector(None)._resolve_addressing_mode() == 'timestamps'


def test_mode_resolution_accepts_random():
    assert _detector('random')._resolve_addressing_mode() == 'random'
    assert _detector('RANDOM ')._resolve_addressing_mode() == 'random'


def test_effective_mode_non_random_passes_through():
    configured, effective = _detector('segment_ids')._effective_addressing_mode()
    assert configured == effective == 'segment_ids'
    configured, effective = _detector('timestamps')._effective_addressing_mode()
    assert configured == effective == 'timestamps'
    # Unknown value: configured_mode coerces to 'timestamps' same as
    # _resolve_addressing_mode, and stays that way (not random).
    configured, effective = _detector('bogus')._effective_addressing_mode()
    assert configured == effective == 'timestamps'


def test_effective_mode_random_draws_both_outcomes():
    d = _detector('random')
    with patch('ad_detector.random.choice', return_value='segment_ids'):
        configured, effective = d._effective_addressing_mode()
    assert configured == 'random'
    assert effective == 'segment_ids'

    with patch('ad_detector.random.choice', return_value='timestamps'):
        configured, effective = d._effective_addressing_mode()
    assert configured == 'random'
    assert effective == 'timestamps'


def test_effective_mode_random_draws_from_the_two_real_modes():
    # No monkeypatch: confirm the actual random.choice call is constrained
    # to the two real modes, not e.g. 'random' itself.
    d = _detector('random')
    seen = {d._effective_addressing_mode()[1] for _ in range(50)}
    assert seen <= {'timestamps', 'segment_ids'}


def test_effective_mode_random_logs_the_draw(caplog):
    d = _detector('random')
    with patch('ad_detector.random.choice', return_value='segment_ids'):
        with caplog.at_level(logging.INFO, logger='podcast.claude'):
            d._effective_addressing_mode(slug='my-show', episode_id='ep-1')
    messages = [r.message for r in caplog.records]
    assert any('Addressing mode: random -> segment_ids' in m for m in messages)
    assert any('[my-show:ep-1]' in m for m in messages)


def test_id_transcript_lines():
    segs = [{'sid': 0, 'start': 0.0, 'end': 5.0, 'text': 'hello'},
            {'sid': 1, 'start': 5.0, 'end': 9.0, 'text': 'world'}]
    lines = AdDetector._format_transcript_lines(segs, 'segment_ids')
    assert lines == ['[0] hello', '[1] world']
    ts_lines = AdDetector._format_transcript_lines(segs, 'timestamps')
    assert ts_lines == ['[0.0s - 5.0s] hello', '[5.0s - 9.0s] world']


def test_timestamp_transcript_lines_expose_timed_show_and_ad_utterances():
    tokens = ['Host', 'story.', 'Our', 'sponsor', 'is', 'Acme.',
              'Visit', 'acme.com.', 'Back', 'to', 'the', 'topic.']
    segment = {
        'start': 10.0, 'end': 22.0, 'text': ' '.join(tokens),
        'words': [
            {'start': 10.0 + i, 'end': 10.5 + i, 'word': token}
            for i, token in enumerate(tokens)
        ],
    }

    assert AdDetector._format_transcript_lines([segment], 'timestamps') == [
        '[10.0s - 11.5s] Host story.',
        '[12.0s - 15.5s] Our sponsor is Acme.',
        '[16.0s - 17.5s] Visit acme.com.',
        '[18.0s - 21.5s] Back to the topic.',
    ]
    segment['sid'] = 3
    assert AdDetector._format_transcript_lines([segment], 'segment_ids') == [
        '[3] ' + ' '.join(tokens)]


def test_timestamp_transcript_lines_chunk_unpunctuated_speech_without_word_loss():
    words = [
        {'start': float(i), 'end': i + 0.5, 'word': f'word{i}'}
        for i in range(55)
    ]
    segment = {'start': 0.0, 'end': 55.0,
               'text': ' '.join(word['word'] for word in words), 'words': words}

    lines = AdDetector._format_transcript_lines([segment], 'timestamps')
    assert len(lines) == 3
    assert lines[0].startswith('[0.0s - 24.5s] ')
    assert lines[-1] == '[50.0s - 54.5s] word50 word51 word52 word53 word54'
    assert ' '.join(line.split('] ', 1)[1] for line in lines) == segment['text']


def test_timestamp_transcript_lines_fall_back_for_unaligned_word_timing():
    segment = {
        'start': 10.0, 'end': 20.0, 'text': 'The actual transcript.',
        'words': [{'start': 10.0, 'end': 11.0, 'word': 'Different'}],
    }
    assert AdDetector._format_transcript_lines([segment], 'timestamps') == [
        '[10.0s - 20.0s] The actual transcript.']


def test_segment_ids_resolve_word_timed_ad_inside_mixed_segment():
    tokens = ['Host', 'story.', 'Our', 'sponsor', 'is', 'Acme.',
              'Visit', 'acme.com.', 'Back', 'to', 'the', 'topic.']
    segment = {
        'start': 10.0, 'end': 46.0, 'text': ' '.join(tokens),
        'words': [
            {'start': 10.0 + 3 * i, 'end': 12.5 + 3 * i, 'word': token}
            for i, token in enumerate(tokens)
        ],
    }
    detector = AdDetector(api_key='test-key')
    lines = detector._detection_line_segments([segment])
    for sid, line in enumerate(lines):
        line['sid'] = sid
    assert [line['text'] for line in lines] == [
        'Host story.', 'Our sponsor is Acme.',
        'Visit acme.com.', 'Back to the topic.']
    assert detector._format_transcript_lines(lines, 'segment_ids') == [
        '[0] Host story.', '[1] Our sponsor is Acme.',
        '[2] Visit acme.com.', '[3] Back to the topic.']

    response = (
        '[{"start_id": 1, "end_id": 2, "confidence": 0.95, '
        '"category": "sponsor", "sponsor": "Acme", '
        '"reason": "Acme sponsor read"}]')
    result = _run_window(
        detector, addressing_mode='segment_ids', response_text=response,
        window={'start': 10.0, 'end': 46.0, 'segments': lines})
    assert result.compliant is True
    assert len(result.ads) == 1
    assert result.ads[0]['start'] == 16.0
    assert result.ads[0]['end'] == 33.5
    assert result.ads[0]['word_timed_start'] == 16.0
    assert result.ads[0]['word_timed_end'] == 33.5
    assert segment['start'] == 10.0 and segment['end'] == 46.0
    assert 'sid' not in segment
    validated = AdValidator(60.0, [segment]).validate(result.ads)
    assert validated.ads[0]['validation']['decision'] == Decision.ACCEPT.value

    numeric = _run_window(
        detector, addressing_mode='timestamps',
        response_text='[{"start": 16.0, "end": 33.5, "confidence": 0.95, '
                      '"category": "sponsor", "sponsor": "Acme", '
                      '"reason": "Acme sponsor read"}]',
        window={'start': 10.0, 'end': 46.0, 'segments': lines},
        validate_timestamps=True)
    assert numeric.compliant is True
    assert numeric.ads[0]['start'] == 16.0
    assert numeric.ads[0]['end'] == 33.5
    assert numeric.ads[0]['word_timed_start'] == 16.0
    assert numeric.ads[0]['word_timed_end'] == 33.5
    assert AdValidator(60.0, [segment]).validate(numeric.ads).ads[0][
        'validation']['decision'] == Decision.ACCEPT.value


def test_numeric_word_edge_provenance_requires_unique_line_boundary():
    lines = [
        {'start': 10.0, 'end': 20.0, 'text': 'Acme offer.', 'word_timed_line': True},
        {'start': 10.0, 'end': 20.0, 'text': 'Repeated audio.', 'word_timed_line': True},
    ]
    ambiguous = AdDetector._align_numeric_edges_to_word_lines(
        [{'start': 10.0, 'end': 20.0}], lines)[0]
    assert 'word_timed_start' not in ambiguous
    assert 'word_timed_end' not in ambiguous

    arbitrary = AdDetector._align_numeric_edges_to_word_lines(
        [{'start': 10.5, 'end': 19.5}], lines)[0]
    assert 'word_timed_start' not in arbitrary
    assert 'word_timed_end' not in arbitrary

    rounded = AdDetector._align_numeric_edges_to_word_lines(
        [{'start': 31.8, 'end': 40.2}], [
            {'start': 31.75, 'end': 40.25, 'word_timed_line': True},
        ])[0]
    assert rounded['start'] == rounded['word_timed_start'] == 31.75
    assert rounded['end'] == rounded['word_timed_end'] == 40.25


def test_system_section_mentions_ids_not_timestamps():
    assert 'start_id' in SEGMENT_ID_SYSTEM_SECTION
    assert 'end_id' in SEGMENT_ID_SYSTEM_SECTION


def test_system_section_supersedes_timestamp_instructions():
    # The base system prompts (user-editable, stored in DB) still tell the
    # model to read [Xs] markers and emit numeric start/end; this section
    # must explicitly override that so ID mode and the base prompt don't
    # conflict.
    assert '[Xs]' in SEGMENT_ID_SYSTEM_SECTION
    assert 'Ignore any earlier instruction' in SEGMENT_ID_SYSTEM_SECTION


def _window_kwargs():
    return dict(
        podcast_name='Test', episode_title='Ep1',
        description_section='', transcript_lines=['[0] hello'],
        window_index=0, total_windows=1,
        window_start=0.0, window_end=600.0,
    )


def test_window_prompt_rules_are_mode_exclusive():
    id_out = format_window_prompt(**_window_kwargs(), addressing_mode='segment_ids')
    assert 'start_id' in id_out
    assert 'Use absolute timestamps' not in id_out

    ts_out = format_window_prompt(**_window_kwargs(), addressing_mode='timestamps')
    assert 'Use absolute timestamps' in ts_out
    assert 'start_id' not in ts_out


SEGS = [{'sid': i, 'start': i * 10.0, 'end': i * 10.0 + 10.0,
         'text': f'seg {i}'} for i in range(10)]


def test_ids_resolve_to_exact_segment_times():
    ads, used = parse_id_ads_from_response(
        '[{"start_id": 2, "end_id": 4, "confidence": 0.9, '
        '"category": "sponsor", "reason": "promo code read"}]')
    assert used is True
    resolved = resolve_segment_id_ads(ads, SEGS)
    assert resolved[0]['start'] == 20.0
    assert resolved[0]['end'] == 50.0
    assert 'start_id' not in resolved[0]


def test_reversed_ids_normalized():
    ads, _ = parse_id_ads_from_response(
        '[{"start_id": 4, "end_id": 2, "confidence": 0.9, '
        '"category": "sponsor", "reason": "x"}]')
    resolved = resolve_segment_id_ads(ads, SEGS)
    assert resolved[0]['start'] == 20.0 and resolved[0]['end'] == 50.0


def test_out_of_range_id_dropped():
    ads, _ = parse_id_ads_from_response(
        '[{"start_id": 2, "end_id": 99, "confidence": 0.9, '
        '"category": "sponsor", "reason": "x"}]')
    assert resolve_segment_id_ads(ads, SEGS) == []


def test_timestamp_response_falls_back():
    ads, used = parse_id_ads_from_response(
        '[{"start": 45.0, "end": 82.0, "confidence": 0.9, '
        '"category": "sponsor", "reason": "x"}]')
    assert used is False


def test_start_id_never_misread_as_seconds():
    # The generic fuzzy start/end matcher must not turn start_id=2 into
    # start=2.0 seconds; the ID branch owns any object carrying id fields.
    ads, used = parse_id_ads_from_response(
        '[{"start_id": 2, "end_id": 4, "confidence": 0.9, '
        '"category": "sponsor", "reason": "x"}]')
    assert used is True
    assert all('start' not in ad for ad in ads)
    resolved = resolve_segment_id_ads(ads, SEGS)
    assert resolved[0]['start'] == 20.0  # not 2.0


def test_malformed_confidence_drops_one_ad_not_the_whole_pass():
    # A malformed confidence value used to raise ValueError out of
    # _normalize_ad, escaping resolve_segment_id_ads and failing the whole
    # window instead of just the one bad ad.
    ads, used = parse_id_ads_from_response(
        '[{"start_id": 0, "end_id": 1, "confidence": "certain", '
        '"category": "sponsor", "reason": "bad ad"},'
        '{"start_id": 2, "end_id": 4, "confidence": 0.9, '
        '"category": "sponsor", "reason": "promo code read"}]')
    assert used is True
    resolved = resolve_segment_id_ads(ads, SEGS)
    assert len(resolved) == 1
    assert resolved[0]['start'] == 20.0
    assert resolved[0]['end'] == 50.0


def test_startid_endid_key_variant_resolves():
    ads, used = parse_id_ads_from_response(
        '[{"startid": 2, "endid": 4, "confidence": 0.9, '
        '"category": "sponsor", "reason": "promo code read"}]')
    assert used is True
    resolved = resolve_segment_id_ads(ads, SEGS)
    assert resolved[0]['start'] == 20.0
    assert resolved[0]['end'] == 50.0


def test_start_segment_id_end_segment_id_key_variant_resolves():
    ads, used = parse_id_ads_from_response(
        '[{"start_segment_id": 2, "end_segment_id": 4, "confidence": 0.9, '
        '"category": "sponsor", "reason": "promo code read"}]')
    assert used is True
    resolved = resolve_segment_id_ads(ads, SEGS)
    assert resolved[0]['start'] == 20.0
    assert resolved[0]['end'] == 50.0


class _StubResponse:
    """LLMResponse-shaped duck: the parse path reads .content."""

    def __init__(self, content):
        self.content = content
        self.usage = {}


def _window(start=0.0, end=60.0, with_sid=False):
    seg = {'start': start, 'end': start + 1.0, 'text': 'hello'}
    if with_sid:
        seg['sid'] = 0
    return {'start': start, 'end': end, 'segments': [seg]}


def _run_window(detector, *, addressing_mode, response_text='',
                 with_sid=False, llm_failed=False, window=None,
                 validate_timestamps=False):
    def fake_call(*, prompt, window_label, **_kw):
        if llm_failed:
            return None, RuntimeError('boom')
        return _StubResponse(response_text), None

    with patch.object(detector, '_call_llm_for_window', side_effect=fake_call):
        return detector._process_single_window(
            window_idx=0, window=window or _window(with_sid=with_sid), total_windows=1,
            model='m', system_prompt='sys', description_section='',
            podcast_name='p', episode_title='t',
            audio_enforcer=None, audio_analysis=None,
            llm_timeout=30, max_retries=1,
            slug='s', episode_id='e', pass_name='pass1',
            window_label_prefix='Window', validate_timestamps=validate_timestamps,
            addressing_mode=addressing_mode,
        )


class TestWindowCompliance:
    """WindowResult.compliant per the definitions in _process_single_window:
    segment_ids compliance is used_ids; timestamps compliance is a
    successfully parsed ads array (including a valid empty one); an
    LLM-failed window is excluded (compliant is None)."""

    def test_segment_ids_used_ids_is_compliant(self):
        detector = AdDetector(api_key='test-key')
        result = _run_window(
            detector, addressing_mode='segment_ids', with_sid=True,
            response_text='[{"start_id": 0, "end_id": 0, "confidence": 0.9, '
                           '"category": "sponsor", "reason": "x"}]')
        assert result.compliant is True
        assert result.addressing_mode == 'segment_ids'

    def test_segment_ids_fallback_is_non_compliant(self):
        detector = AdDetector(api_key='test-key')
        result = _run_window(
            detector, addressing_mode='segment_ids', with_sid=True,
            response_text='[{"start": 10.0, "end": 20.0, "confidence": 0.9, '
                           '"category": "sponsor", "reason": "x"}]')
        assert result.compliant is False

    def test_timestamps_valid_empty_array_is_compliant(self):
        detector = AdDetector(api_key='test-key')
        result = _run_window(
            detector, addressing_mode='timestamps', response_text='[]')
        assert result.compliant is True
        assert result.ads == []

    def test_timestamps_populated_array_is_compliant(self):
        detector = AdDetector(api_key='test-key')
        result = _run_window(
            detector, addressing_mode='timestamps',
            response_text='[{"start": 10.0, "end": 20.0, "confidence": 0.9, '
                           '"category": "sponsor", "reason": "x"}]')
        assert result.compliant is True

    def test_timestamps_extraction_failure_is_non_compliant(self):
        detector = AdDetector(api_key='test-key')
        result = _run_window(
            detector, addressing_mode='timestamps',
            response_text='This is not JSON and has no brackets at all.')
        assert result.compliant is False

    def test_llm_failed_window_is_excluded(self):
        detector = AdDetector(api_key='test-key')
        result = _run_window(
            detector, addressing_mode='timestamps', llm_failed=True)
        assert result.failed is True
        assert result.compliant is None


def test_mixed_id_and_timestamp_objects_keeps_id_ad():
    # Some models mix formats in one response: one object with ids, one with
    # timestamps instead. The id-less object is skipped (surfaced via a
    # logged warning); used_ids stays True and the id ad still resolves.
    ads, used = parse_id_ads_from_response(
        '[{"start_id": 2, "end_id": 4, "confidence": 0.9, '
        '"category": "sponsor", "reason": "promo code read"},'
        '{"start": 45.0, "end": 82.0, "confidence": 0.9, '
        '"category": "sponsor", "reason": "x"}]')
    assert used is True
    assert len(ads) == 1
    resolved = resolve_segment_id_ads(ads, SEGS)
    assert resolved[0]['start'] == 20.0
    assert resolved[0]['end'] == 50.0
