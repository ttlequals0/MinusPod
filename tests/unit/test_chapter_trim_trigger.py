"""Generated chapter match: maps back through the cut list, adds one marker, and never repeats."""
import os
import tempfile
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='chapter_trim_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')

from main_app.pipeline import publish  # noqa: E402

CHAPTERS = [
    {'startTime': 1, 'title': 'Opening'},
    {'startTime': 1200, 'title': 'Listener Donations'},
    {'startTime': 1500, 'title': 'Main Topic'},
]
PODCAST = {'description_trim_pattern': '*Donations*', 'description_trim_mode': 'to_next'}


def _run(markers, cuts=None, podcast=PODCAST, keep_original=True, chapters=CHAPTERS):
    with patch.object(publish, 'db', MagicMock(resolve_keep_original_audio=lambda *a: keep_original)):
        return publish._queue_chapter_trim(
            's', 'e', chapters, podcast, cuts or [], 0.0, 3000.0, 3200.0, markers,
            'generated_chapter')


def test_no_cuts_maps_straight_through():
    markers = []
    assert _run(markers) is True
    marker = markers[0]
    assert (marker['start'], marker['end']) == (1200.0, 1500.0)
    assert marker['category'] == 'outro' and marker['confidence'] == 1.0
    assert marker['detection_stage'] == 'description_chapter'
    assert marker['reason'] == 'Chapter: Listener Donations'
    assert marker['source'] == 'generated_chapter' and marker['sponsor'] is None


def test_cut_before_the_chapter_shifts_it_back_to_original_time():
    markers = []
    _run(markers, cuts=[{'start': 100.0, 'end': 160.0}])
    assert (markers[0]['start'], markers[0]['end']) == (1260.0, 1560.0)


def test_cut_inside_the_chapter_stretches_its_original_end():
    markers = []
    _run(markers, cuts=[{'start': 1300.0, 'end': 1360.0}])
    assert (markers[0]['start'], markers[0]['end']) == (1200.0, 1560.0)


def test_last_chapter_ends_at_original_duration():
    markers = []
    _run(markers, podcast={**PODCAST, 'description_trim_mode': 'to_end'})
    assert (markers[0]['start'], markers[0]['end']) == (1200.0, 3200.0)


def test_existing_marker_covering_span_suppresses_trigger():
    markers = [{'start': 1199.5, 'end': 1500.5, 'detection_stage': 'description_chapter'}]
    assert _run(markers) is False
    assert len(markers) == 1


def test_no_pattern_no_match_or_no_retained_original_does_nothing():
    for kwargs in ({'podcast': {'description_trim_pattern': None}},
                   {'podcast': {**PODCAST, 'description_trim_pattern': '*Nope*'}},
                   {'keep_original': False}):
        markers = []
        assert _run(markers, **kwargs) is False
        assert markers == []


def test_pending_selector_finds_unvalidated_chapter_marker_only():
    pending = {'detection_stage': 'description_chapter', 'source': 'generated_chapter'}
    done = {**pending, 'validation': {'decision': 'ACCEPT'}}
    assert publish.pending_chapter_trim([done, {'start': 1}]) is False
    assert publish.pending_chapter_trim([done, pending]) is True


def test_beep_block_boundary_maps_to_the_cut_start():
    # A 60 s cut at 100 s leaves a 2 s beep at processed 100-102; a chapter at 101 sits inside it.
    markers = []
    chapters = [{'startTime': 1, 'title': 'Opening'}, {'startTime': 101, 'title': 'Listener Donations'}]
    with patch.object(publish, 'db', MagicMock(resolve_keep_original_audio=lambda *a: True)):
        publish._queue_chapter_trim(
            's', 'e', chapters, PODCAST, [{'start': 100.0, 'end': 160.0}], 2.0, 3000.0, 3200.0,
            markers, 'generated_chapter')
    assert markers[0]['start'] == 100.0


def _generate(markers, regenerate_chapters, generated=None, trim_spy=None,
              show_notes_starts=frozenset(), podcast=PODCAST):
    gen_cls = MagicMock()
    gen_cls.return_value.generate_chapters.return_value = generated
    gen_cls.return_value.chapters_degraded = False
    gen_cls.return_value.show_notes_starts = show_notes_starts
    with ExitStack() as stack:
        enter = stack.enter_context
        enter(patch.object(publish, 'db', MagicMock(
            get_setting=lambda key: None, resolve_keep_original_audio=lambda *a: True)))
        enter(patch.object(publish, 'storage', MagicMock()))
        enter(patch.object(publish, 'get_replacement_duration', return_value=0.0))
        enter(patch.object(publish, 'resolve_chapters_mode', return_value='generate'))
        enter(patch.object(publish, 'resolve_ad_chapter_config', return_value=None))
        enter(patch.object(publish, 'merge_ad_chapters', side_effect=lambda topic, *a: topic))
        enter(patch.object(publish, '_publish_chapters'))
        enter(patch.object(publish, 'clear_fallback'))
        enter(patch.object(publish, '_remap_stored_chapters'))
        enter(patch('transcript_generator.TranscriptGenerator'))
        enter(patch('chapters_generator.ChaptersGenerator', gen_cls))
        spy = enter(patch.object(publish, '_queue_chapter_trim', wraps=publish._queue_chapter_trim)) \
            if trim_spy else None
        publish._generate_assets(
            's', 'e', [{'start': 0, 'end': 1, 'text': 'x'}], [], 'desc', 'Show', 'Ep',
            regenerate_chapters=regenerate_chapters, audio_duration=3000.0,
            original_duration=3200.0, podcast_row=podcast, markers=markers)
    return gen_cls if spy is None else spy


def test_recut_run_never_triggers_the_chapter_trim():
    markers = []
    spy = _generate(markers, regenerate_chapters=False, trim_spy=True)
    spy.assert_not_called()
    assert markers == []


def test_generating_run_appends_exactly_one_pending_marker():
    markers = []
    _generate(markers, regenerate_chapters=True, generated={'version': '1.2.0', 'chapters': CHAPTERS})
    assert len(markers) == 1 and markers[0]['source'] == 'generated_chapter'
    assert publish.pending_chapter_trim(markers) is True


def test_unretained_original_logs_the_match_once(caplog):
    caplog.set_level('INFO', logger='podcast.audio')
    _run([], keep_original=False)
    lines = [r.message for r in caplog.records if 'original audio is not retained' in r.message]
    assert len(lines) == 1 and "'Listener Donations' matched" in lines[0]


def test_original_timeline_chapters_skip_the_cut_mapping():
    markers = []
    with patch.object(publish, 'db', MagicMock(resolve_keep_original_audio=lambda *a: True)):
        publish._queue_chapter_trim(
            's', 'e', CHAPTERS, PODCAST, [{'start': 100.0, 'end': 160.0}], 0.0, 3000.0, 3200.0,
            markers, 'upstream_chapter', original_timeline=True)
    assert (markers[0]['start'], markers[0]['end']) == (1200.0, 1500.0)


SKIP_DETECTION_PODCAST = {**PODCAST, 'skip_ad_detection': 1}


def test_skip_detection_feed_still_queues_its_chapter_trim():
    markers = []
    _generate(markers, regenerate_chapters=True,
              generated={'version': '1.2.0', 'chapters': CHAPTERS}, podcast=SKIP_DETECTION_PODCAST)
    assert len(markers) == 1
    assert publish.pending_chapter_trim(markers) is True


def test_skipped_chapter_never_matches_but_still_ends_the_one_before():
    chapters = [*CHAPTERS, {'startTime': 2000, 'title': 'Listener Donations Again'}]
    markers = []
    with patch.object(publish, 'db', MagicMock(resolve_keep_original_audio=lambda *a: True)):
        publish._queue_chapter_trim(
            's', 'e', chapters, PODCAST, [], 0.0, 3000.0, 3200.0, markers,
            'generated_chapter', skip_starts={1200, 1500})
    assert (markers[0]['start'], markers[0]['end']) == (2000.0, 3200.0)
    assert markers[0]['reason'] == 'Chapter: Listener Donations Again'


def test_show_notes_chapters_never_trigger_the_chapter_trim():
    markers = []
    _generate(markers, regenerate_chapters=True,
              generated={'version': '1.2.0', 'chapters': CHAPTERS},
              show_notes_starts=frozenset({1, 1200, 1500}))
    assert markers == []


def test_llm_chapter_beside_show_notes_chapters_still_triggers_the_trim():
    chapters = [{'startTime': 1, 'title': 'Intro'}, {'startTime': 600, 'title': 'Main Topic'},
                {'startTime': 1200, 'title': 'Listener Donations'}]
    markers = []
    _generate(markers, regenerate_chapters=True,
              generated={'version': '1.2.0', 'chapters': chapters},
              show_notes_starts=frozenset({1, 600}))
    assert len(markers) == 1 and markers[0]['reason'] == 'Chapter: Listener Donations'


@pytest.mark.parametrize('column,expected', [(None, True), (1, True), (0, False)])
def test_feed_switch_reaches_the_generator(column, expected):
    gen_cls = _generate([], regenerate_chapters=True,
                        generated={'version': '1.2.0', 'chapters': CHAPTERS},
                        podcast={**PODCAST, 'show_notes_chapters': column})
    kwargs = gen_cls.return_value.generate_chapters.call_args.kwargs
    assert kwargs['show_notes_chapters'] is expected


DONATIONS = [
    {'startTime': 1, 'title': 'Opening'},
    {'startTime': 300, 'title': 'Donations'}, {'startTime': 600, 'title': 'Talk'},
    {'startTime': 1200, 'title': 'Donations'}, {'startTime': 1500, 'title': 'Talk 2'},
    {'startTime': 2400, 'title': 'Donations'},
]


def test_to_next_queues_every_matching_chapter_with_original_spans():
    markers = []
    _run(markers, chapters=DONATIONS, podcast={**PODCAST, 'description_trim_pattern': 'Donations'})
    assert [(m['start'], m['end']) for m in markers] == [
        (300.0, 600.0), (1200.0, 1500.0), (2400.0, 3200.0)]


def test_to_next_maps_each_span_back_through_earlier_cuts():
    markers = []
    podcast = {**PODCAST, 'description_trim_pattern': 'Donations'}
    chapters = [{'startTime': 1, 'title': 'Opening'}, {'startTime': 500, 'title': 'Donations'},
                {'startTime': 600, 'title': 'Talk'}, {'startTime': 2000, 'title': 'Donations'},
                {'startTime': 2100, 'title': 'Talk 2'}]
    _run(markers, cuts=[{'start': 100.0, 'end': 160.0}], podcast=podcast, chapters=chapters)
    assert [(m['start'], m['end']) for m in markers] == [(560.0, 660.0), (2060.0, 2160.0)]


def test_adjacent_matching_chapters_merge_into_one_marker():
    markers = []
    chapters = [{'startTime': 1, 'title': 'Opening'}, {'startTime': 500, 'title': 'Donations'},
                {'startTime': 600, 'title': 'More Donations'}, {'startTime': 700, 'title': 'Talk'}]
    _run(markers, chapters=chapters)
    assert [(m['start'], m['end']) for m in markers] == [(500.0, 700.0)]


def test_to_end_queues_one_marker_from_the_first_match():
    markers = []
    _run(markers, chapters=DONATIONS,
         podcast={'description_trim_pattern': 'Donations', 'description_trim_mode': 'to_end'})
    assert [(m['start'], m['end']) for m in markers] == [(300.0, 3200.0)]


def test_second_call_with_markers_present_adds_nothing():
    markers = []
    podcast = {**PODCAST, 'description_trim_pattern': 'Donations'}
    assert _run(markers, chapters=DONATIONS, podcast=podcast) is True
    assert _run(markers, chapters=DONATIONS, podcast=podcast) is False
    assert len(markers) == 3


def test_skip_starts_apply_per_chapter():
    markers = []
    with patch.object(publish, 'db', MagicMock(resolve_keep_original_audio=lambda *a: True)):
        publish._queue_chapter_trim(
            's', 'e', DONATIONS, {**PODCAST, 'description_trim_pattern': 'Donations'}, [], 0.0,
            3000.0, 3200.0, markers, 'generated_chapter', skip_starts={1200})
    assert [m['start'] for m in markers] == [300.0, 2400.0]


def test_generating_run_appends_a_pending_marker_per_match():
    markers = []
    _generate(markers, regenerate_chapters=True, generated={'version': '1.2.0', 'chapters': DONATIONS},
              podcast={**PODCAST, 'description_trim_pattern': 'Donations'})
    assert len(markers) == 3 and publish.pending_chapter_trim(markers) is True
