"""Chapters built from timestamps the LLM reads out of the episode description (show notes)."""
import re
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('show_notes_chapters_test_')

import chapters_generator  # noqa: E402
from chapters_generator import ChaptersGenerator  # noqa: E402
from llm_client import ProviderRateLimitedError  # noqa: E402

DESCRIPTION = """This week on the show
01:00 Shipping and drone makers $SHIP $DRON
14:45 Commodities: Tungsten $TUNG, $WOLF.L
21:00 Commodities: Fertilizer $FERT (also $AGRO $SOIL)
29:00 Northwind Consulting $NWND
35:00 Contoso Health $CTSO
43:00 Fabrikam Brokerage $FBRK & Litware Securities $LTWR
"""
TITLES = [
    'Shipping and drone makers $SHIP $DRON',
    'Commodities: Tungsten $TUNG, $WOLF.L',
    'Commodities: Fertilizer $FERT (also $AGRO $SOIL)',
    'Northwind Consulting $NWND',
    'Contoso Health $CTSO',
    'Fabrikam Brokerage $FBRK & Litware Securities $LTWR',
]
ANCHOR_TIMES = [60, 885, 1260, 1740, 2100, 2580]


@dataclass
class _Response:
    content: str
    model: str = 'stub'
    usage: dict = None
    raw_response: object = None


@pytest.fixture(autouse=True)
def _model_configured(monkeypatch):
    monkeypatch.setattr('chapters_generator.get_chapters_model', lambda: 'test-model')


def _segments(duration=3190):
    return [
        {'start': i, 'end': i + 10,
         'text': f'segment {i} with enough words to look like a transcript a b c d e'}
        for i in range(0, duration, 10)
    ]


def _gen():
    gen = ChaptersGenerator(api_key='test')
    gen._llm_client = object()
    return gen


EXTRACT = 'chapter show-notes extraction'
ALIGN = 'chapter show-notes alignment'
_NOTE_LINE = re.compile(r'^[(\[]?(\d{1,3}:\d{2}(?::\d{2})?)[)\]]?\s*[-:]*\s*(.+)$')


def _notes_reply(description):
    """What a well-behaved model answers to the extraction prompt for line-format notes."""
    found = [f'{m.group(1)} {m.group(2)}' for line in (description or '').split('\n')
             if (m := _NOTE_LINE.match(line.strip()))]
    return '\n'.join(found) or 'NONE'


def _llm(reply='', description=DESCRIPTION, extract=None):
    """call_llm stand-in: the extraction call gets `extract` (default: the notes' own lines), the
    alignment and any other call gets `reply`. Calls are recorded in .labels."""
    labels = []

    def fake(**kwargs):
        labels.append(kwargs['call_label'])
        if kwargs['call_label'] == EXTRACT:
            return _Response(_notes_reply(description) if extract is None else extract), None
        return _Response(reply), None

    fake.labels = labels
    return fake


def _run(gen, description=DESCRIPTION, duration=3190, reply='', extract=None):
    """Run generate_chapters with a mocked LLM; return (chapters, call_llm mock)."""
    mock = MagicMock(side_effect=_llm(reply, description, extract))
    with patch('chapters_generator.call_llm', mock):
        out = gen.generate_chapters(
            _segments(duration), episode_description=description,
            podcast_name='Show', episode_title='Ep')
    return out['chapters'], mock


def _labels(mock):
    return [c.kwargs['call_label'] for c in mock.call_args_list]


def _starts(chapters):
    return [c['startTime'] for c in chapters]


def test_six_anchor_example_gets_intro_and_verbatim_titles():
    gen = _gen()
    with patch.object(gen, '_detect_boundaries_windowed', return_value=[]):
        chapters, mock = _run(gen)  # nothing located: listed times stand

    assert len(chapters) == 7
    assert chapters[0] == {'startTime': 1, 'title': 'Intro'}
    assert [c['title'] for c in chapters[1:]] == TITLES
    assert _starts(chapters)[1:] == ANCHOR_TIMES
    assert 'Contoso Health $CTSO' in [c['title'] for c in chapters]
    assert gen.chapters_degraded is False
    # Extraction and alignment only: no title generation.
    assert set(_labels(mock)) == {EXTRACT, ALIGN}


def _align_prompt(mock):
    return next(c.kwargs['prompt'] for c in mock.call_args_list
                if c.kwargs['call_label'] == ALIGN)


def test_alignment_prompt_lists_topics_transcript_and_feed_notes():
    gen = _gen()
    _, mock = _run(gen, description=DESCRIPTION)
    prompt = _align_prompt(mock)
    assert '1. (about 01:00) Shipping and drone' in prompt
    assert '<number> MM:SS' in prompt
    assert '[00:00] segment 0' in prompt

    gen = _gen()
    mock = MagicMock(side_effect=_llm())
    with patch('chapters_generator.call_llm', mock):
        gen.generate_chapters(
            _segments(), episode_description=DESCRIPTION,
            podcast_name='Show', episode_title='Ep',
            chapter_notes='Swedish finance show')
    for call in mock.call_args_list:
        assert call.kwargs['prompt'].endswith('Feed notes:\nSwedish finance show')


def test_snapped_times_within_radius_are_accepted():
    reply = '1 01:30\n2 15:30\n3 21:10\n4 28:30\n5 36:00\n6 43:20'
    # Topic numbers are per group, so keep all six in one group.
    with patch.object(chapters_generator, 'resolve_chapter_geometry',
                      return_value=(600, 3600, 20, 180)):
        chapters, _ = _run(_gen(), reply=reply)
    assert _starts(chapters) == [1, 90, 930, 1270, 1710, 2160, 2600]


def test_snap_beyond_radius_or_unparseable_keeps_anchor_time():
    reply = (
        'Here you go\n'
        '1 01:30\n'     # accepted
        '2 25:00\n'     # 1500 s vs 885 s listed: beyond the radius
        '3 20:00\n'     # accepted
        '4 banana\n'    # unparseable
        '5 09:00\n'     # 540 s vs 2100 s listed: beyond the radius
        '6 -\n'         # not found
    )
    with patch.object(chapters_generator, 'resolve_chapter_geometry',
                      return_value=(600, 3600, 20, 180)):
        chapters, _ = _run(_gen(), reply=reply)
    assert _starts(chapters) == [1, 90, 885, 1200, 1740, 2100, 2580]


def test_snap_not_after_previous_chapter_is_rejected():
    description = '00:00 Opening\n10:00 Alpha\n11:00 Bravo'
    chapters, _ = _run(_gen(), description=description, duration=1800,
                       reply='1 10:50\n2 10:40')
    assert _starts(chapters) == [1, 650, 660]


def test_snap_past_next_anchor_is_rejected():
    description = '00:00 Opening\n10:00 Alpha\n11:00 Bravo'
    chapters, _ = _run(_gen(), description=description, duration=1800,
                       reply='1 11:10')
    assert _starts(chapters) == [1, 600, 660]


def _no_client_run(description=DESCRIPTION, segments=None, **kwargs):
    gen = ChaptersGenerator(api_key='test')
    with patch('chapters_generator.get_llm_client', return_value=None), \
         patch.object(ChaptersGenerator, '_client_for', return_value=None), \
         patch('chapters_generator.call_llm') as mock:
        out = gen.generate_chapters(
            segments or _segments(), episode_description=description,
            podcast_name='Show', episode_title='Ep', **kwargs)
    mock.assert_not_called()
    return gen, out['chapters']


def test_no_llm_client_means_no_extraction_and_the_output_of_a_run_without_notes():
    gen, chapters = _no_client_run(description=PROSE)
    base_gen, base = _no_client_run(description=None)
    assert chapters == base
    assert gen.chapters_degraded == base_gen.chapters_degraded
    assert gen.chapters_degradation_reason == base_gen.chapters_degradation_reason
    assert chapters_generator.CHAPTERS_AI_UNAVAILABLE in gen.chapters_degradation_reason


def test_no_llm_client_still_reads_line_format_notes_by_pattern():
    gen, chapters = _no_client_run()
    assert _starts(chapters)[1:] == ANCHOR_TIMES
    assert [c['title'] for c in chapters[1:]] == TITLES
    assert gen.show_notes_starts == frozenset([1, *ANCHOR_TIMES])


def test_pattern_path_keeps_the_ten_minute_first_entry_gate():
    description = '15:00 Opening\n25:00 Middle\n35:00 End'
    gen, chapters = _no_client_run(description=description)
    assert gen.show_notes_starts == frozenset()
    assert all(c['title'] != 'Middle' for c in chapters)


def test_pattern_path_drops_clock_and_sponsor_titles():
    description = ('01:00 Opening\n05:00 pm tickets\n10:00 Visit acme.com for a deal\n'
                   '20:00 Middle\n30:00 End')
    gen, chapters = _no_client_run(description=description)
    assert [c['title'] for c in chapters if c['startTime'] in gen.show_notes_starts] == [
        'Intro', 'Opening', 'Middle', 'End']


def test_llm_call_returning_none_degrades_and_uses_topic_detection():
    gen = _gen()
    with patch.object(gen, '_detect_boundaries_windowed', return_value=[]) as windowed, \
         patch('chapters_generator.call_llm', return_value=(None, RuntimeError('boom'))):
        gen.generate_chapters(
            _segments(), episode_description=PROSE,
            podcast_name='Show', episode_title='Ep')
    windowed.assert_called_once()
    assert gen.show_notes_starts == frozenset()
    assert gen.chapters_degraded is True
    assert 'show-notes alignment failed' in gen.chapters_degradation_reason


def test_failed_extraction_falls_back_to_line_format_notes_and_degrades():
    gen = _gen()

    def fake(**kwargs):
        if kwargs['call_label'] == EXTRACT:
            return None, RuntimeError('boom')
        return _Response(''), None

    with patch.object(gen, '_detect_boundaries_windowed', return_value=[]), \
         patch('chapters_generator.call_llm', side_effect=fake):
        out = gen.generate_chapters(
            _segments(), episode_description=DESCRIPTION,
            podcast_name='Show', episode_title='Ep')
    assert _starts(out['chapters'])[1:] == ANCHOR_TIMES
    assert gen.show_notes_starts == frozenset([1, *ANCHOR_TIMES])
    assert gen.chapters_degraded is True
    assert 'show-notes alignment failed' in gen.chapters_degradation_reason


def test_successful_extraction_never_consults_the_pattern_parser():
    gen = _gen()
    with patch.object(gen, '_detect_boundaries_windowed', return_value=[]) as windowed, \
         patch('chapters_generator._pattern_show_notes_entries') as by_pattern:
        _run(gen, extract='NONE')
    by_pattern.assert_not_called()
    windowed.assert_called_once()
    assert gen.show_notes_starts == frozenset()
    assert gen._show_notes_alignment_failed is False


def test_extraction_ignores_the_ten_minute_gate():
    description = '15:00 Opening\n25:00 Middle\n35:00 End'
    gen = _gen()
    with patch.object(gen, '_detect_boundaries_windowed', return_value=[]):
        _run(gen, description=description)
    assert gen.show_notes_starts == frozenset({1, 900, 1500, 2100})


def test_llm_exception_in_extraction_degrades_and_uses_topic_detection():
    gen = _gen()
    with patch.object(gen, '_detect_boundaries_windowed', return_value=[]) as windowed, \
         patch('chapters_generator.call_llm', side_effect=RuntimeError('boom')):
        gen.generate_chapters(
            _segments(), episode_description=PROSE,
            podcast_name='Show', episode_title='Ep')
    windowed.assert_called_once()
    assert gen.chapters_degraded is True


def test_failed_alignment_keeps_the_listed_times_and_degrades():
    gen = _gen()

    def fake(**kwargs):
        if kwargs['call_label'] == EXTRACT:
            return _Response(_notes_reply(DESCRIPTION)), None
        return None, RuntimeError('boom')

    with patch.object(gen, '_detect_boundaries_windowed', return_value=[]), \
         patch('chapters_generator.call_llm', side_effect=fake):
        out = gen.generate_chapters(
            _segments(), episode_description=DESCRIPTION,
            podcast_name='Show', episode_title='Ep')
    assert _starts(out['chapters'])[1:] == ANCHOR_TIMES
    assert gen.chapters_degraded is True
    assert 'show-notes alignment failed' in gen.chapters_degradation_reason


def test_rejected_or_unlocated_times_do_not_degrade():
    gen = _gen()
    with patch.object(chapters_generator, 'resolve_chapter_geometry',
                      return_value=(600, 3600, 20, 180)), \
         patch.object(gen, '_detect_boundaries_windowed', return_value=[]):
        _run(gen, reply='1 30:00\n2 -')
    assert gen.chapters_degraded is False


def test_first_anchor_at_zero_has_no_intro():
    description = '00:00 Opening\n10:00 Middle\n20:00 End'
    chapters, _ = _run(_gen(), description=description, duration=1800)
    assert chapters == [
        {'startTime': 1, 'title': 'Opening'},
        {'startTime': 600, 'title': 'Middle'},
        {'startTime': 1200, 'title': 'End'},
    ]


def test_anchors_beyond_episode_end_are_dropped():
    description = '05:00 First\n10:00 Second\n15:00 Third\n90:00 Way past the end'
    chapters, _ = _run(_gen(), description=description, duration=1800)
    assert [c['title'] for c in chapters] == ['Intro', 'First', 'Second', 'Third']


def test_single_in_range_anchor_uses_the_llm_topic_path():
    description = '05:00 First\n90:00 Way past the end'
    gen = _gen()
    with patch.object(gen, '_detect_boundaries_windowed', return_value=[]) as windowed, \
         patch('chapters_generator.call_llm', side_effect=_llm('Opening', description)) as call:
        gen.generate_chapters(
            _segments(1800), episode_description=description,
            podcast_name='Show', episode_title='Ep')
    windowed.assert_called_once()
    assert ALIGN not in _labels(call)


SHORT_NOTES = '01:00 First\n02:30 Second\n04:00 Third'


def test_short_episode_still_gets_show_notes_chapters():
    chapters, _ = _run(_gen(), description=SHORT_NOTES, duration=400)
    assert [c['title'] for c in chapters] == ['Intro', 'First', 'Second', 'Third']


def test_times_shifted_into_one_cut_are_made_strictly_increasing():
    # 03:00 and 04:00 sit inside the 150-300 s cut, so both shift to 150 s.
    gen = _gen()
    notes = '01:00 Alpha\n03:00 Bravo\n04:00 Charlie'
    with patch('chapters_generator.call_llm', side_effect=_llm('', notes)):
        out = gen.generate_chapters(
            _segments(1800), episode_description=notes,
            ads_removed=[{'start': 150, 'end': 300}], podcast_name='Show', episode_title='Ep')
    assert _starts(out['chapters']) == [1, 60, 150, 151]


def test_rate_limit_propagates():
    gen = _gen()
    with patch('chapters_generator.call_llm',
               return_value=(None, ProviderRateLimitedError('slow down', 60))):
        with pytest.raises(ProviderRateLimitedError):
            gen.generate_chapters(
                _segments(), episode_description=DESCRIPTION,
                podcast_name='Show', episode_title='Ep')


def test_rate_limit_in_alignment_propagates():
    gen = _gen()

    def fake(**kwargs):
        if kwargs['call_label'] == EXTRACT:
            return _Response(_notes_reply(DESCRIPTION)), None
        return None, ProviderRateLimitedError('slow down', 60)

    with patch('chapters_generator.call_llm', side_effect=fake):
        with pytest.raises(ProviderRateLimitedError):
            gen.generate_chapters(
                _segments(), episode_description=DESCRIPTION,
                podcast_name='Show', episode_title='Ep')


def test_anchors_are_grouped_by_window_span():
    gen = _gen()
    prompts = []

    base = _llm()

    def fake(**kwargs):
        if kwargs['call_label'] == ALIGN:
            prompts.append(kwargs['prompt'])
        return base(**kwargs)

    with patch('chapters_generator.call_llm', side_effect=fake), \
         patch.object(chapters_generator, 'resolve_chapter_geometry',
                      return_value=(600, 1200, 20, 180)):
        gen.generate_chapters(
            _segments(), episode_description=DESCRIPTION,
            podcast_name='Show', episode_title='Ep')
    assert len(prompts) > 1
    for title in TITLES:
        assert sum(title in p for p in prompts) == 1


NOTES3 = '08:00 Alpha topic\n10:30 Bravo topic\n35:30 Charlie topic'


def _with_detection(boundaries=None, side_effect=None, reply='3 33:00'):
    gen = _gen()
    detector = MagicMock(return_value=boundaries, side_effect=side_effect)
    with patch.object(gen, '_detect_boundaries_windowed', detector):
        chapters, _ = _run(gen, description=NOTES3, duration=3000, reply=reply)
    return gen, chapters, detector


def _b(mmss, title='Extra topic'):
    m, sec = mmss.split(':')
    return {'original_time': int(m) * 60 + int(sec), 'title': title}


def test_llm_adds_only_extras_far_from_listed_chapters():
    gen, chapters, detector = _with_detection(
        [_b('06:19'), _b('31:19'), _b('38:20', 'Guest interview')])
    # Charlie snapped to 33:00; 06:19 and 31:19 are under 180 s from a listed chapter.
    assert chapters == [
        {'startTime': 1, 'title': 'Intro'},
        {'startTime': 480, 'title': 'Alpha topic'},
        {'startTime': 630, 'title': 'Bravo topic'},
        {'startTime': 1980, 'title': 'Charlie topic'},
        {'startTime': 2300, 'title': 'Guest interview'},
    ]
    kwargs = detector.call_args.kwargs
    # The listed chapters replace the description and its anchors in the extras call.
    assert kwargs['episode_description'] is None
    assert kwargs['description_anchors'] is None
    assert [c['title'] for c in kwargs['listed_chapters']] == [
        'Intro', 'Alpha topic', 'Bravo topic', 'Charlie topic']
    assert gen.chapters_degraded is False


def test_extra_within_min_duration_of_a_listed_chapter_is_dropped():
    _, chapters, _ = _with_detection([_b('10:50')])
    assert [c['title'] for c in chapters] == [
        'Intro', 'Alpha topic', 'Bravo topic', 'Charlie topic']


def test_extra_within_min_duration_of_intro_is_dropped():
    _, chapters, _ = _with_detection([_b('01:30')])
    assert 'Extra topic' not in [c['title'] for c in chapters]


def test_extra_within_min_duration_of_the_end_is_dropped():
    # duration 3000 s: 48:30 leaves 90 s, 46:30 leaves 210 s.
    _, chapters, _ = _with_detection([_b('48:30', 'Outro')])
    assert 'Outro' not in [c['title'] for c in chapters]
    _, chapters, _ = _with_detection([_b('46:30', 'Kept')])
    assert [c['title'] for c in chapters][-1] == 'Kept'


def test_extras_too_close_to_each_other_keep_the_first():
    _, chapters, _ = _with_detection(
        [_b('38:20', 'First'), _b('40:00', 'Second'), _b('45:00', 'Third')])
    assert [c['title'] for c in chapters[-2:]] == ['First', 'Third']
    assert 'Second' not in [c['title'] for c in chapters]


def test_extra_without_title_is_dropped():
    _, chapters, _ = _with_detection([{'original_time': 2300, 'title': None}])
    assert len(chapters) == 4


def test_failed_extra_detection_keeps_show_notes_without_degrading():
    def failing(*args, **kwargs):
        gen._topic_detection_failed = True
        gen._model_not_configured_message = 'x'
        return []

    gen = _gen()
    with patch.object(gen, '_detect_boundaries_windowed', side_effect=failing):
        chapters, _ = _run(gen, description=NOTES3, duration=3000, reply='')
    assert [c['title'] for c in chapters] == [
        'Intro', 'Alpha topic', 'Bravo topic', 'Charlie topic']
    assert gen.chapters_degraded is False
    assert gen._topic_detection_failed is False


def test_rate_limit_from_extra_detection_propagates():
    gen = _gen()
    with patch.object(gen, '_detect_boundaries_windowed',
                      side_effect=ProviderRateLimitedError('slow down', 60)):
        with pytest.raises(ProviderRateLimitedError):
            _run(gen, description=NOTES3, duration=3000, reply='')


def test_short_episode_skips_extra_detection():
    gen = _gen()
    with patch.object(gen, '_detect_boundaries_windowed') as detector:
        _run(gen, description=SHORT_NOTES, duration=400)
    detector.assert_not_called()


# Listed times are original-audio times, shifted by the applied cuts.

SHIFT_NOTES = '00:00 Opening\n10:00 Alpha\n20:00 Bravo'
CUT = [{'start': 300, 'end': 420}]  # 120 s removed before both topics


def _shift_run(reply='', segments=None, **kwargs):
    gen = _gen()
    with patch.object(gen, '_detect_boundaries_windowed', return_value=[]), \
         patch('chapters_generator.call_llm', side_effect=_llm(reply, SHIFT_NOTES)) as mock:
        out = gen.generate_chapters(
            segments or _segments(1800), episode_description=SHIFT_NOTES,
            podcast_name='Show', episode_title='Ep', **kwargs)
    return out['chapters'], mock


def test_unaligned_times_fall_back_to_the_shifted_time():
    chapters, _ = _shift_run(ads_removed=CUT)
    assert chapters == [
        {'startTime': 1, 'title': 'Opening'},
        {'startTime': 480, 'title': 'Alpha'},
        {'startTime': 1080, 'title': 'Bravo'},
    ]


def test_alignment_hint_uses_the_shifted_time():
    chapters, mock = _shift_run(ads_removed=CUT)
    prompt = _align_prompt(mock)
    assert '(about 08:00) Alpha' in prompt and '(about 18:00) Bravo' in prompt
    assert _starts(chapters) == [1, 480, 1080]


def test_snap_radius_is_measured_from_the_shifted_time():
    # Shifted Alpha is 08:00; 12:30 is 270 s from it, so it stands.
    chapters, _ = _shift_run('1 12:30', ads_removed=CUT)
    assert _starts(chapters) == [1, 750, 1080]


def test_regeneration_shifts_by_marker_cuts():
    # Regeneration passes processed segments and the applied cuts as marker_cuts.
    chapters, _ = _shift_run(segments=_segments(1680), marker_cuts=CUT)
    assert _starts(chapters) == [1, 480, 1080]


def test_beep_replacement_shortens_the_shift():
    chapters, _ = _shift_run(ads_removed=CUT, replacement_duration=2.0)
    assert _starts(chapters) == [1, 482, 1082]


# The gate that switches to show-notes chapters.

def _uses_show_notes(description, duration=3000, extract=None, **kwargs):
    gen = _gen()
    with patch.object(gen, '_detect_boundaries_windowed', return_value=[]) as windowed, \
         patch('chapters_generator.call_llm',
               side_effect=_llm('Opening', description, extract)) as call:
        out = gen.generate_chapters(
            _segments(duration), episode_description=description,
            podcast_name='Show', episode_title='Ep', **kwargs)
    aligned = ALIGN in _labels(call)
    if not aligned:
        windowed.assert_called_once()
    return aligned, out['chapters']


def test_gate_accepts_three_entries_at_the_limits():
    # A pair exactly 60 s apart; a first entry after ten minutes no longer matters.
    aligned, chapters = _uses_show_notes('10:00 Alpha\n11:00 Bravo\n20:00 Charlie')
    assert aligned
    assert [c['title'] for c in chapters] == ['Intro', 'Alpha', 'Bravo', 'Charlie']


def test_gate_rejects_two_entries():
    assert _uses_show_notes('01:00 Alpha\n10:00 Bravo')[0] is False


def test_first_entry_after_ten_minutes_passes():
    aligned, chapters = _uses_show_notes('10:01 Alpha\n20:00 Bravo\n30:00 Charlie')
    assert aligned and chapters[0]['title'] == 'Intro'


def test_gate_rejects_entries_closer_than_a_minute():
    assert _uses_show_notes('01:00 Alpha\n01:59 Bravo\n10:00 Charlie')[0] is False


def test_equal_times_are_one_entry():
    assert _uses_show_notes('5:00 Alpha\n05:00 Bravo\n10:00 Charlie')[0] is False


def test_feed_switch_off_still_extracts_for_hints_but_builds_no_chapters():
    gen = _gen()
    with patch.object(gen, '_detect_boundaries_windowed', return_value=[]) as windowed, \
         patch('chapters_generator.call_llm', side_effect=_llm('Opening')) as call:
        gen.generate_chapters(
            _segments(), episode_description=DESCRIPTION,
            podcast_name='Show', episode_title='Ep', show_notes_chapters=False)
    windowed.assert_called_once()
    assert EXTRACT in _labels(call) and ALIGN not in _labels(call)
    hints = windowed.call_args.kwargs['description_anchors']
    assert [t for t, _ in hints] == ANCHOR_TIMES
    assert [title for _, title in hints] == TITLES
    assert gen.show_notes_starts == frozenset()


def test_fewer_than_three_entries_are_hints_only():
    gen = _gen()
    with patch.object(gen, '_detect_boundaries_windowed', return_value=[]) as windowed, \
         patch('chapters_generator.call_llm', side_effect=_llm('Opening', '01:00 A\n10:00 B')):
        gen.generate_chapters(
            _segments(), episode_description='01:00 A\n10:00 B',
            podcast_name='Show', episode_title='Ep')
    assert windowed.call_args.kwargs['description_anchors'] == [(60.0, 'A'), (600.0, 'B')]


def test_hints_are_shifted_by_the_cuts():
    gen = _gen()
    with patch.object(gen, '_detect_boundaries_windowed', return_value=[]) as windowed, \
         patch('chapters_generator.call_llm', side_effect=_llm('Opening', SHIFT_NOTES)):
        gen.generate_chapters(
            _segments(1800), episode_description='10:00 Alpha\n20:00 Bravo',
            ads_removed=CUT, podcast_name='Show', episode_title='Ep')
    assert windowed.call_args.kwargs['description_anchors'] == [(480.0, 'Alpha'), (1080.0, 'Bravo')]


# Show-notes chapters are reported so the chapter trim can skip them.

def test_show_notes_starts_cover_listed_chapters_and_intro_not_extras():
    gen, chapters, _ = _with_detection([_b('38:20', 'Guest interview')])
    assert _starts(chapters) == [1, 480, 630, 1980, 2300]
    assert gen.show_notes_starts == frozenset({1, 480, 630, 1980})


def test_an_extra_titled_with_a_listed_name_comes_through_as_given():
    _, chapters, _ = _with_detection([_b('38:20', 'Interview: Nuclear power')])
    assert chapters[-1]['title'] == 'Interview: Nuclear power'


def _topic_prompts(window_span, **kwargs):
    """Topic-detection prompts of a NOTES3 run on a 3000 s episode (listed times stand)."""
    gen = _gen()
    prompts = []
    base = _llm('', NOTES3)

    def fake(**kw):
        if kw['call_label'] == 'chapter topic detection':
            prompts.append(kw['prompt'])
        return base(**kw)

    with patch('chapters_generator.call_llm', side_effect=fake), \
         patch.object(chapters_generator, 'resolve_chapter_geometry',
                      return_value=(600, window_span, 20, 180)):
        gen.generate_chapters(_segments(3000), episode_description=NOTES3,
                              podcast_name='Show', episode_title='Ep', **kwargs)
    return prompts


def test_each_window_lists_only_the_overlapping_chapters():
    first, second = _topic_prompts(1500)[:2]
    assert '00:00-08:00 Intro\n08:00-10:30 Alpha topic\n10:30-35:30 Bravo topic' in first
    assert 'Charlie topic' not in first
    assert '10:30-35:30 Bravo topic\n35:30-50:00 Charlie topic' in second
    assert 'Intro' not in second.split('already list')[1]
    assert 'Do not emit a boundary at the start of a listed chapter' in first
    assert '`<short name>: <subtopic>`' in first and 'plain title' in first


def test_a_long_window_lists_every_chapter_it_spans():
    (prompt,) = _topic_prompts(3000)
    assert ('00:00-08:00 Intro\n08:00-10:30 Alpha topic\n10:30-35:30 Bravo topic\n'
            '35:30-50:00 Charlie topic') in prompt


def test_plain_topic_detection_has_no_listed_block():
    prompts = _topic_prompts(1500, show_notes_chapters=False)
    assert prompts and all('already list' not in p for p in prompts)


# The LLM reads the notes: prose, invented times, NONE, no time token.

PROSE = ("Some intro sentence. First some news since last time (06:25). Then the companies this "
         "time: Acme (56:15); Globex (65:35); Initech (80:50) and finally an interview with "
         "Jane Doe, CEO of Umbrella (113:40). Enjoy!")
PROSE_REPLY = ('06:25 News since last time\n56:15 Acme\n65:35 Globex\n80:50 Initech\n'
               '113:40 Interview with Jane Doe')
PROSE_SHUFFLED = ("Some intro. News since last time can be found from (24:00). Today's companies "
                  "are Acme (05:45); Globex (64:46); Initech (78:40); Hooli (92:00) and the "
                  "interview with CEO John Smith (104:00). Welcome!")
SHUFFLED_REPLY = ('24:00 News since last time\n5:45 Acme\n64:46 Globex\n78:40 Initech\n'
                  '92:00 Hooli\n104:00 Interview with John Smith')


def _prose_run(description, extract, duration=7200, reply=''):
    gen = _gen()
    with patch.object(gen, '_detect_boundaries_windowed', return_value=[]):
        chapters, mock = _run(gen, description, duration, reply, extract)
    return gen, chapters, mock


def test_prose_notes_become_chapters_with_the_llm_titles():
    # The first listed time is past ten minutes in the second example; that no longer matters.
    _, chapters, mock = _prose_run(PROSE, PROSE_REPLY)
    assert [c['title'] for c in chapters] == [
        'Intro', 'News since last time', 'Acme', 'Globex', 'Initech', 'Interview with Jane Doe']
    assert _starts(chapters)[1:] == [385, 3375, 3935, 4850, 6820]
    assert EXTRACT in _labels(mock)


def test_extraction_prompt_carries_the_stripped_notes():
    _, _, mock = _prose_run('<p>' + PROSE + '</p>', PROSE_REPLY)
    prompt = mock.call_args_list[0].kwargs['prompt']
    assert prompt == chapters_generator.SHOW_NOTES_EXTRACT_PROMPT.format(notes=PROSE)


def test_prose_notes_out_of_order_are_sorted_and_short_minutes_match():
    _, chapters, _ = _prose_run(PROSE_SHUFFLED, SHUFFLED_REPLY)
    assert _starts(chapters)[1:] == [345, 1440, 3886, 4720, 5520, 6240]
    assert [c['title'] for c in chapters][1:3] == ['Acme', 'News since last time']


def test_invented_times_are_dropped():
    extract = PROSE_REPLY + '\n99:00 Something the notes never listed'
    _, chapters, _ = _prose_run(PROSE, extract)
    assert len(chapters) == 6
    assert 99 * 60 not in _starts(chapters)


def test_entries_with_a_bad_time_or_no_title_are_skipped():
    gen = _gen()
    with patch('chapters_generator.call_llm',
               return_value=(_Response('06:25\nsoon Acme\n56:15 Acme\n99:99 Typo'), None)):
        assert gen._extract_show_notes_entries(PROSE) == [(3375.0, 'Acme')]


def test_list_markers_and_markdown_around_the_time_are_read():
    gen = _gen()
    reply = '- 06:25 News\n2. `56:15` Acme\n* **65:35** - Globex\n(80:50) Initech'
    with patch('chapters_generator.call_llm', return_value=(_Response(reply), None)):
        assert gen._extract_show_notes_entries(PROSE) == [
            (385.0, 'News'), (3375.0, 'Acme'), (3935.0, 'Globex'), (4850.0, 'Initech')]


def test_duplicate_times_keep_the_first_title():
    gen = _gen()
    with patch('chapters_generator.call_llm',
               return_value=(_Response('56:15 First\n56:15 Second\n6:25 News'), None)):
        assert gen._extract_show_notes_entries(PROSE) == [
            (385.0, 'News'), (3375.0, 'First')]


def test_none_reply_goes_to_topic_detection_with_the_raw_description():
    gen = _gen()
    with patch.object(gen, '_detect_boundaries_windowed', return_value=[]) as windowed, \
         patch('chapters_generator.call_llm', side_effect=_llm('', PROSE, 'NONE')):
        gen.generate_chapters(_segments(), episode_description=PROSE,
                              podcast_name='Show', episode_title='Ep')
    windowed.assert_called_once()
    assert windowed.call_args.kwargs['episode_description'] == PROSE
    assert not windowed.call_args.kwargs['description_anchors']
    assert gen.show_notes_starts == frozenset()


def test_raw_description_goes_to_window_zero_only_without_entries():
    gen = _gen()
    prompts = []
    with patch.object(gen, '_detect_topic_boundaries', side_effect=lambda *a, **k: prompts.append(
            k['episode_description']) or []):
        gen._detect_boundaries_windowed(
            _segments(3000), 3000, 600, 1200, 20, episode_description=PROSE)
    assert prompts[0] == PROSE and all(p is None for p in prompts[1:])


@pytest.mark.parametrize('description', [None, '', 'No times here, just prose (about 5 minutes).',
                                         'Release 1.2.3 on 2026-11-26'])
def test_no_time_token_means_no_extraction_call(description):
    gen = _gen()
    with patch.object(gen, '_detect_boundaries_windowed', return_value=[]), \
         patch('chapters_generator.call_llm', side_effect=_llm('Opening', description)) as call:
        gen.generate_chapters(_segments(), episode_description=description,
                              podcast_name='Show', episode_title='Ep')
    assert EXTRACT not in _labels(call)


def test_extraction_rate_limit_propagates():
    gen = _gen()
    with patch('chapters_generator.call_llm',
               return_value=(None, ProviderRateLimitedError('slow down', 60))):
        with pytest.raises(ProviderRateLimitedError):
            gen._extract_show_notes_entries(PROSE)


def test_time_token_ignores_parts_of_longer_numbers():
    tokens = chapters_generator._TIME_TOKEN
    assert [m.group(1) for m in tokens.finditer('at 5:45 and (104:00) 1:02:03 12:345 9:5')] == [
        '5:45', '104:00', '1:02:03']
