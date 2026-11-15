"""With snip_short_asks off, the per-feed resolver and unflagged markers behave as main."""
import copy
import itertools

import pytest

from tests.app_bootstrap import bootstrap

bootstrap('snip_short_asks_defaults_test_')

from database.podcasts import PodcastMixin
from main_app.pipeline import detect
from utils.short_asks import floor_for


class _Podcasts(PodcastMixin):
    """Resolver-only stub: no DB, just a per-feed row and a global setting."""

    def __init__(self, podcast, global_value):
        self._podcast, self._global = podcast, global_value

    def get_podcast_row(self, slug):
        return self._podcast

    def get_setting(self, key):
        return self._global


@pytest.mark.parametrize('podcast,global_value', list(itertools.product(
    [None, {}, {'detect_show_segments': None}, {'detect_show_segments': 0}, {'detect_show_segments': 1}],
    [None, 'true', 'false'])))
def test_generic_resolver_equals_upstream_show_segments_resolver(podcast, global_value):
    db = _Podcasts(podcast, global_value)
    assert (db.resolve_feed_bool('feed', 'detect_show_segments', 'detect_show_segments')
            == db.resolve_detect_show_segments('feed'))


def test_unflagged_markers_pass_through_the_clamp_and_floor_unchanged():
    ads = [{'start': 100.0, 'end': 103.0, 'category': 'sponsor', 'confidence': 0.9},
           {'start': 5.0, 'end': 40.0, 'category': 'interaction', 'confidence': 0.5}]
    before = copy.deepcopy(ads)
    segs = [{'start': 0.0, 'end': 200.0, 'text': 'Some talk.'}]
    assert detect._clamp_short_asks(ads, segs) == before
    assert all(floor_for(ad, 10.0) == 10.0 for ad in ads)
