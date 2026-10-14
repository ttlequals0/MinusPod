"""Generated chapter match: maps back through the cut list, adds one marker, and never repeats."""
import os
import tempfile
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

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


def _generate(markers, regenerate_chapters, generated=None, trim_spy=None):
    gen_cls = MagicMock()
    gen_cls.return_value.generate_chapters.return_value = generated
    gen_cls.return_value.chapters_degraded = False
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
            original_duration=3200.0, podcast_row=PODCAST, markers=markers)
    return spy


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
