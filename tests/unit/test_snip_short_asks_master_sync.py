"""The late short-ask clamp moves the saved marker with the cut, so the render can still find it."""
from tests.app_bootstrap import bootstrap

bootstrap('snip_short_asks_master_sync_test_')

from main_app.pipeline import cuts, detect  # noqa: E402
from tests.unit.marker_test_utils import applied_cut  # noqa: E402


def _ask(start, end, origin):
    return {'start': start, 'end': end, 'category': 'interaction', 'short_ask': True,
            'short_ask_origin': list(origin), 'confidence': 0.9}


SEGS = [
    {'start': 90.0, 'end': 99.0, 'text': 'That was the main story.'},
    {'start': 99.0, 'end': 102.5, 'text': 'If you enjoyed the show'},
    {'start': 102.5, 'end': 106.0, 'text': 'please subscribe.'},
    {'start': 106.0, 'end': 115.0, 'text': 'Now back to the episode.'},
]


def test_clamp_on_a_rebuilt_cut_moves_its_master_and_the_render_marks_it_cut():
    # A sweep (the word snap) moved the end to 106.05 and saved that on both;
    # the cut it returned is a new dict, so the clamp gets a copy.
    master = _ask(99.0, 106.05, (99.0, 106.0))
    cut = dict(master)
    detect._clamp_short_asks([cut], SEGS, masters=[master, _ask(200.0, 205.0, (200.0, 205.0))])
    assert (cut['start'], cut['end']) == (99.0, 106.0)
    assert (master['start'], master['end']) == (99.0, 106.0)
    cuts._finalize_cut_state([master], [cut], [applied_cut(99.0, 106.0)], 600.0)
    assert master['was_cut'] is True


def test_a_flag_the_clamp_drops_is_dropped_on_the_master_too():
    # Start trimmed past the cap: the clamp keeps the span and drops the flag.
    master = _ask(103.0, 106.0, (100.0, 106.0))
    cut = dict(master)
    detect._clamp_short_asks([cut], SEGS, masters=[master])
    assert 'short_ask' not in cut and 'short_ask' not in master
    assert 'short_ask_origin' not in master


def test_without_masters_only_the_list_moves():
    master = _ask(99.4, 105.2, (99.4, 105.2))
    cut = dict(master)
    detect._clamp_short_asks([cut], SEGS)
    assert (cut['start'], cut['end']) == (99.0, 106.0)
    assert (master['start'], master['end']) == (99.4, 105.2)
