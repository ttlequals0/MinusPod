"""Per-feed override for the zero-splice-evidence veto.

The veto is right for a DAI feed and wrong for one that structurally cannot
produce splice evidence, such as a host-read archive. The calibration fix
handles that case on its own; this override is the manual escape hatch, and
it also covers the reverse, forcing the veto on where the global is off.
"""
import pytest

from tests.app_bootstrap import bootstrap

_test_data_dir = bootstrap('splice_override_test_')

from config import resolve_splice_veto_enabled  # noqa: E402
from main_app import processing  # noqa: E402


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


@pytest.mark.parametrize("stored_global, feed, expected", [
    ('false', None, False),
    ('false', 1, True),
    ('true', 0, False),
    ('true', None, True),
])
def test_build_validator_reads_the_stored_global(monkeypatch, stored_global, feed, expected):
    monkeypatch.setattr(processing.db, 'get_podcast_cue_settings_overrides',
                        lambda podcast_id: {'splice_veto_enabled': feed})
    original = processing.db.get_setting('splice_veto_enabled')
    processing.db.set_setting('splice_veto_enabled', stored_global, is_default=False)
    try:
        validator = processing._build_validator(
            600.0, [], '', false_positive_corrections=[], min_cut_confidence=0.8,
            max_ad_duration_override=None, cue_gate_enabled=False, podcast_id=1)
    finally:
        processing.db.set_setting('splice_veto_enabled', original or 'true', is_default=True)
    assert validator.splice_veto_enabled is expected


@pytest.mark.parametrize("name", ['Example Cast', None])
def test_build_validator_passes_the_show_name(name):
    validator = processing._build_validator(
        600.0, [], '', false_positive_corrections=[], min_cut_confidence=0.8,
        max_ad_duration_override=None, cue_gate_enabled=False, splice_veto=False,
        podcast_name=name)
    assert validator.podcast_name == name


class _Captured(Exception):
    pass


def _capture_show_name(monkeypatch):
    seen = {}

    def build(*args, **kwargs):
        seen['podcast_name'] = kwargs.get('podcast_name')
        raise _Captured

    monkeypatch.setattr(processing, '_build_validator', build)
    return seen


def test_pass2_validation_gets_the_show_name(monkeypatch):
    seen = _capture_show_name(monkeypatch)
    ad = {'start': 10.0, 'end': 40.0}
    with pytest.raises(_Captured):
        processing._validate_verification_ads(
            'example-podcast', 'a1b2c3d4e5f6', [dict(ad)], [dict(ad)],
            [{'start': 0.0, 'end': 60.0, 'text': 'x'}], [], None, 0.8, processing.db,
            podcast_name='Example Cast')
    assert seen == {'podcast_name': 'Example Cast'}


def test_recut_validation_gets_the_show_name_from_the_episode_row(monkeypatch):
    seen = _capture_show_name(monkeypatch)
    row = {'podcast_title': 'Example Cast', 'ad_markers_json': '[{"start": 10.0, "end": 40.0}]'}
    with pytest.raises(_Captured):
        processing._build_recut_ad_list(
            'example-podcast', 'a1b2c3d4e5f6', [], 600.0, '', 0.8,
            segment_actions={}, corrections=([], []), episode_row=row)
    assert seen == {'podcast_name': 'Example Cast'}
