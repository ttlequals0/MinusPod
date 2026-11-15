"""Word snap, then the late short-ask clamp, then the render: a cut short ask reads as cut."""
from tests.app_bootstrap import bootstrap

bootstrap('short_ask_after_word_snap_test_')

from main_app.pipeline import cuts, detect  # noqa: E402
from tests.unit.marker_test_utils import applied_cut  # noqa: E402

SEGS = [
    {'start': 3790.0, 'end': 3798.0, 'text': 'That is the whole point.'},
    {'start': 3798.0, 'end': 3806.0, 'text': 'Guys, I have got a favour to ask'},
    {'start': 3806.0, 'end': 3814.94, 'text': 'please hit subscribe.'},
    {'start': 3815.3, 'end': 3830.0, 'text': 'So back to the conversation.'},
]


def _fake_word_snap(ads, *args, **kwargs):
    """The word snap moved the end into the pause after the sentence (3815.00, as on a recorded episode)."""
    ads[0]['end'] = 3815.0
    ads[0]['edge_snap'] = {'end': {'acoustic': True}}
    ads[0]['edge_pause_s'] = {'end': 0.3}


def test_short_ask_cut_after_word_snap_and_clamp_is_marked_cut(monkeypatch):
    monkeypatch.setattr(cuts, 'resolve_precise_cut_edges', lambda db: True)
    monkeypatch.setattr(cuts, 'snap_ad_boundaries_to_silence', _fake_word_snap)
    monkeypatch.setattr(cuts.storage, 'save_combined_ads', lambda *a: None)
    master = {'start': 3798.0, 'end': 3814.94, 'category': 'interaction', 'confidence': 0.9,
              'short_ask': True, 'short_ask_origin': [3798.0, 3814.94]}
    snapped = cuts._snap_cut_edges_to_words(
        'slug', 'ep', [master], [master], SEGS, None, 6800.8, energy_reader=lambda a, b: None)
    assert snapped[0] is not master and master['end'] == 3815.0

    clamped = detect._clamp_short_asks(snapped, SEGS, masters=[master])
    assert clamped[0]['end'] == 3814.94 and master['end'] == 3814.94

    cuts._finalize_cut_state([master], clamped, [applied_cut(3798.0, 3814.94)], 6800.8)
    assert master['was_cut'] is True
