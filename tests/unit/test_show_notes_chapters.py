"""Chapters built from timestamps in the episode description (show notes)."""
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


def _run(gen, description=DESCRIPTION, duration=3190, reply=''):
    """Run generate_chapters with call_llm answering `reply`; return (chapters, call_llm mock)."""
    mock = MagicMock(return_value=(_Response(reply), None))
    with patch('chapters_generator.call_llm', mock):
        out = gen.generate_chapters(
            _segments(duration), episode_description=description,
            podcast_name='Show', episode_title='Ep')
    return out['chapters'], mock


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
    # Only alignment calls here: no title generation.
    assert {c.kwargs['call_label'] for c in mock.call_args_list} == {
        'chapter show-notes alignment'}


def test_alignment_prompt_lists_topics_transcript_and_feed_notes():
    gen = _gen()
    _, mock = _run(gen, description=DESCRIPTION)
    prompt = mock.call_args_list[0].kwargs['prompt']
    assert '1. (about 01:00) Shipping and drone' in prompt
    assert '<number> MM:SS' in prompt
    assert '[00:00] segment 0' in prompt

    gen = _gen()
    with patch('chapters_generator.call_llm',
               return_value=(_Response(''), None)) as mock:
        gen.generate_chapters(
            _segments(), episode_description=DESCRIPTION,
            podcast_name='Show', episode_title='Ep',
            chapter_notes='Swedish finance show')
    assert mock.call_args_list[0].kwargs['prompt'].endswith(
        'Feed notes:\nSwedish finance show')


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


def test_no_llm_client_keeps_anchor_times_and_degrades():
    gen, chapters = _no_client_run()
    assert _starts(chapters)[1:] == ANCHOR_TIMES
    assert [c['title'] for c in chapters[1:]] == TITLES
    assert gen.chapters_degraded is True
    assert chapters_generator.CHAPTERS_AI_UNAVAILABLE in gen.chapters_degradation_reason


def test_llm_call_returning_none_keeps_anchor_times_and_degrades():
    gen = _gen()
    with patch('chapters_generator.call_llm',
               return_value=(None, RuntimeError('boom'))):
        out = gen.generate_chapters(
            _segments(), episode_description=DESCRIPTION,
            podcast_name='Show', episode_title='Ep')
    assert _starts(out['chapters'])[1:] == ANCHOR_TIMES
    assert gen.chapters_degraded is True
    assert 'show-notes alignment failed' in gen.chapters_degradation_reason


def test_llm_exception_keeps_anchor_times_and_degrades():
    gen = _gen()
    with patch('chapters_generator.call_llm', side_effect=RuntimeError('boom')):
        out = gen.generate_chapters(
            _segments(), episode_description=DESCRIPTION,
            podcast_name='Show', episode_title='Ep')
    assert _starts(out['chapters'])[1:] == ANCHOR_TIMES
    assert gen.chapters_degraded is True


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
         patch('chapters_generator.call_llm',
               return_value=(_Response('Opening'), None)) as call:
        gen.generate_chapters(
            _segments(1800), episode_description=description,
            podcast_name='Show', episode_title='Ep')
    windowed.assert_called_once()
    assert all(c.kwargs['call_label'] != 'chapter show-notes alignment'
               for c in call.call_args_list)


SHORT_NOTES = '01:00 First\n02:30 Second\n04:00 Third'


def test_short_episode_still_gets_show_notes_chapters():
    chapters, _ = _run(_gen(), description=SHORT_NOTES, duration=400)
    assert [c['title'] for c in chapters] == ['Intro', 'First', 'Second', 'Third']


def test_times_shifted_into_one_cut_are_made_strictly_increasing():
    # 03:00 and 04:00 sit inside the 150-300 s cut, so both shift to 150 s.
    gen = _gen()
    with patch('chapters_generator.call_llm', return_value=(_Response(''), None)):
        out = gen.generate_chapters(
            _segments(1800), episode_description='01:00 Alpha\n03:00 Bravo\n04:00 Charlie',
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


def test_anchors_are_grouped_by_window_span():
    gen = _gen()
    prompts = []

    def fake(**kwargs):
        if kwargs['call_label'] == 'chapter show-notes alignment':
            prompts.append(kwargs['prompt'])
        return _Response(''), None

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
    assert kwargs['episode_description'] == NOTES3
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


def test_no_llm_client_falls_back_to_the_shifted_time():
    _, chapters = _no_client_run(SHIFT_NOTES, segments=_segments(1800), ads_removed=CUT)
    assert chapters == [
        {'startTime': 1, 'title': 'Opening'},
        {'startTime': 480, 'title': 'Alpha'},
        {'startTime': 1080, 'title': 'Bravo'},
    ]


def test_alignment_hint_uses_the_shifted_time():
    gen = _gen()
    with patch('chapters_generator.call_llm', return_value=(_Response(''), None)) as mock:
        out = gen.generate_chapters(
            _segments(1800), episode_description=SHIFT_NOTES, ads_removed=CUT,
            podcast_name='Show', episode_title='Ep')
    prompt = mock.call_args_list[0].kwargs['prompt']
    assert '(about 08:00) Alpha' in prompt and '(about 18:00) Bravo' in prompt
    assert _starts(out['chapters']) == [1, 480, 1080]


def test_snap_radius_is_measured_from_the_shifted_time():
    # Shifted Alpha is 08:00; 12:30 is 270 s from it, so it stands.
    gen = _gen()
    with patch('chapters_generator.call_llm',
               return_value=(_Response('1 12:30'), None)):
        out = gen.generate_chapters(
            _segments(1800), episode_description=SHIFT_NOTES, ads_removed=CUT,
            podcast_name='Show', episode_title='Ep')
    assert _starts(out['chapters']) == [1, 750, 1080]


def test_regeneration_shifts_by_marker_cuts():
    # Regeneration passes processed segments and the applied cuts as marker_cuts.
    _, chapters = _no_client_run(SHIFT_NOTES, segments=_segments(1680), marker_cuts=CUT)
    assert _starts(chapters) == [1, 480, 1080]


def test_beep_replacement_shortens_the_shift():
    _, chapters = _no_client_run(SHIFT_NOTES, segments=_segments(1800), ads_removed=CUT,
                                 replacement_duration=2.0)
    assert _starts(chapters) == [1, 482, 1082]


# The gate that switches to show-notes chapters.

def _uses_show_notes(description, duration=3000, **kwargs):
    gen = _gen()
    with patch.object(gen, '_detect_boundaries_windowed', return_value=[]) as windowed, \
         patch('chapters_generator.call_llm',
               return_value=(_Response('Opening'), None)) as call:
        out = gen.generate_chapters(
            _segments(duration), episode_description=description,
            podcast_name='Show', episode_title='Ep', **kwargs)
    aligned = any(c.kwargs['call_label'] == 'chapter show-notes alignment'
                  for c in call.call_args_list)
    if not aligned:
        windowed.assert_called_once()
    return aligned, out['chapters']


def test_gate_accepts_three_entries_at_the_limits():
    # First at exactly 600 s and a pair exactly 60 s apart.
    aligned, chapters = _uses_show_notes('10:00 Alpha\n11:00 Bravo\n20:00 Charlie')
    assert aligned
    assert [c['title'] for c in chapters] == ['Intro', 'Alpha', 'Bravo', 'Charlie']


def test_gate_rejects_two_entries():
    assert _uses_show_notes('01:00 Alpha\n10:00 Bravo')[0] is False


def test_gate_rejects_first_entry_after_ten_minutes():
    assert _uses_show_notes('10:01 Alpha\n20:00 Bravo\n30:00 Charlie')[0] is False


def test_gate_measures_the_first_entry_in_original_audio():
    # A cut moves 11:00 to 09:00 on the processed timeline; the listed time still decides.
    description = '11:00 Alpha\n20:00 Bravo\n30:00 Charlie'
    assert _uses_show_notes(description, ads_removed=CUT)[0] is False


def test_gate_rejects_entries_closer_than_a_minute():
    assert _uses_show_notes('01:00 Alpha\n01:59 Bravo\n10:00 Charlie')[0] is False
    assert _uses_show_notes('5:00 Alpha\n05:00 Bravo\n10:00 Charlie')[0] is False


def test_gate_ignores_clock_times():
    description = '10:00 AM Doors open\n11:00 a.m. Keynote\n12:30 PM Lunch break'
    assert _uses_show_notes(description)[0] is False


def test_clock_times_do_not_become_chapters():
    description = ('Live from the conference, doors at\n9:30 AM Registration\n'
                   '00:00 Opening\n10:00 Alpha\n20:00 Bravo')
    aligned, chapters = _uses_show_notes(description)
    assert aligned
    assert [c['title'] for c in chapters] == ['Opening', 'Alpha', 'Bravo']


def test_feed_switch_off_uses_topic_detection_with_anchor_hints():
    gen = _gen()
    with patch.object(gen, '_detect_boundaries_windowed', return_value=[]) as windowed, \
         patch('chapters_generator.call_llm',
               return_value=(_Response('Opening'), None)) as call:
        gen.generate_chapters(
            _segments(), episode_description=DESCRIPTION,
            podcast_name='Show', episode_title='Ep', show_notes_chapters=False)
    windowed.assert_called_once()
    assert windowed.call_args.kwargs['episode_description'] == DESCRIPTION
    assert all(c.kwargs['call_label'] != 'chapter show-notes alignment'
               for c in call.call_args_list)
    assert gen.show_notes_starts == frozenset()


# Show-notes chapters are reported so the chapter trim can skip them.

def test_show_notes_starts_cover_listed_chapters_and_intro_not_extras():
    gen, chapters, _ = _with_detection([_b('38:20', 'Guest interview')])
    assert _starts(chapters) == [1, 480, 630, 1980, 2300]
    assert gen.show_notes_starts == frozenset({1, 480, 630, 1980})
