"""Generated chapter match: maps back through the cut list, adds one marker, and never repeats."""
import os
import tempfile
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
