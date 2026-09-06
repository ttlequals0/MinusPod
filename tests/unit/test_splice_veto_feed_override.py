"""Per-feed override for the zero-splice-evidence veto.

The veto is right for a DAI feed and wrong for one that structurally cannot
produce splice evidence, such as a host-read archive. The calibration fix
handles that case on its own; this override is the manual escape hatch, and
it also covers the reverse, forcing the veto on where the global is off.
"""
import os
import sys
import tempfile

import pytest

os.environ.setdefault('MINUSPOD_DATA_DIR', tempfile.mkdtemp(prefix='splice_override_test_'))
os.environ.setdefault('SECRET_KEY', 'test-secret')
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from config import resolve_splice_veto_enabled


class _DB:
    """Stands in for the podcast override read."""

    def __init__(self, value):
        self._value = value

    def get_podcast_cue_settings_overrides(self, podcast_id):
        return {'splice_veto_enabled': self._value}


class _BrokenDB:
    def get_podcast_cue_settings_overrides(self, podcast_id):
        raise RuntimeError('database unavailable')


@pytest.mark.parametrize("stored, global_default, expected", [
    # NULL inherits whatever the global says.
    (None, True, True),
    (None, False, False),
    # An override wins in both directions.
    (0, True, False),
    (1, False, True),
    (1, True, True),
    (0, False, False),
])
def test_override_precedence(stored, global_default, expected):
    assert resolve_splice_veto_enabled(_DB(stored), 1, global_default) is expected


def test_no_podcast_id_falls_back_to_the_global():
    assert resolve_splice_veto_enabled(_DB(0), None, True) is True


def test_a_failed_read_falls_back_to_the_global():
    """_resolve_override fails open, so a broken read must not silently
    disable a safety check."""
    assert resolve_splice_veto_enabled(_BrokenDB(), 1, True) is True
    assert resolve_splice_veto_enabled(_BrokenDB(), 1, False) is False


def test_the_column_is_a_registered_feed_override():
    """Without this the value is written but never read back."""
    from database.podcasts import PodcastMixin
    assert 'splice_veto_enabled' in PodcastMixin._HELD_REVIEW_COLS


def test_the_api_exposes_it_as_a_nullable_bool():
    """Tri-state on the wire: null inherits, true and false are explicit."""
    from api.feeds import _NULLABLE_BOOL_FIELDS
    assert ('spliceVetoEnabled', 'splice_veto_enabled') in _NULLABLE_BOOL_FIELDS
