"""Seed-sponsors toggles: each prompt's {sponsor_database} injection is
independently gated by its setting; missing/unreadable settings fail open."""
from unittest.mock import MagicMock

from ad_detector import AdDetector


def _detector(settings: dict) -> AdDetector:
    d = AdDetector.__new__(AdDetector)
    d.db = MagicMock()
    d.db.get_setting.side_effect = lambda key: settings.get(key)
    d.sponsor_service = MagicMock()
    d.sponsor_service.get_claude_sponsor_list.return_value = "Acme, BetterHelp"
    return d


def test_detection_toggle_on_injects_block():
    d = _detector({'seed_sponsors_detection': 'true'})
    out = d._render_with_sponsors("X {sponsor_database} Y", 'seed_sponsors_detection')
    assert "Acme, BetterHelp" in out


def test_detection_toggle_off_empties_block():
    d = _detector({'seed_sponsors_detection': 'false'})
    out = d._render_with_sponsors("X {sponsor_database} Y", 'seed_sponsors_detection')
    assert "Acme" not in out
    assert "DYNAMIC SPONSOR DATABASE" not in out
    assert out == "X  Y"


def test_verification_toggle_independent_of_detection():
    d = _detector({'seed_sponsors_detection': 'false',
                   'seed_sponsors_verification': 'true'})
    assert "Acme" in d._render_with_sponsors(
        "{sponsor_database}", 'seed_sponsors_verification')
    assert "Acme" not in d._render_with_sponsors(
        "{sponsor_database}", 'seed_sponsors_detection')


def test_missing_setting_fails_open():
    d = _detector({})
    assert "Acme" in d._render_with_sponsors(
        "{sponsor_database}", 'seed_sponsors_detection')


def test_db_error_fails_open():
    d = _detector({})
    d.db.get_setting.side_effect = RuntimeError("db down")
    assert "Acme" in d._render_with_sponsors(
        "{sponsor_database}", 'seed_sponsors_detection')


def test_removed_placeholder_still_yields_no_block():
    d = _detector({'seed_sponsors_detection': 'true'})
    out = d._render_with_sponsors("no placeholder here", 'seed_sponsors_detection')
    assert "Acme" not in out


# Reviewer tests

from ad_reviewer import AdReviewer


def _reviewer(settings: dict) -> AdReviewer:
    r = AdReviewer.__new__(AdReviewer)
    r.db = MagicMock()
    r.db.get_setting.side_effect = lambda key: settings.get(key)
    r.sponsor_service = MagicMock()
    r.sponsor_service.get_claude_sponsor_list.return_value = "Acme, BetterHelp"
    return r


def test_reviewer_block_empty_when_toggle_off():
    r = _reviewer({'seed_sponsors_reviewer': 'false',
                   'seed_sponsors_resurrect': 'true'})
    assert r._sponsor_block_for('seed_sponsors_reviewer') == ""
    assert "Acme" in r._sponsor_block_for('seed_sponsors_resurrect')


def test_resurrect_block_empty_when_toggle_off():
    r = _reviewer({'seed_sponsors_reviewer': 'true',
                   'seed_sponsors_resurrect': 'false'})
    assert "Acme" in r._sponsor_block_for('seed_sponsors_reviewer')
    assert r._sponsor_block_for('seed_sponsors_resurrect') == ""


def test_reviewer_toggles_fail_open():
    r = _reviewer({})
    assert "Acme" in r._sponsor_block_for('seed_sponsors_reviewer')
