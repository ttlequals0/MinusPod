"""Tests for the tiered known-pattern hint injected into the pass-1 prompt."""
from tests.app_bootstrap import bootstrap
bootstrap('known_pattern_hint_test_')

from unittest.mock import MagicMock
from ad_detector import AdDetector


def _detector_with(patterns):
    d = AdDetector.__new__(AdDetector)
    d.db = MagicMock()
    d.db.get_ad_patterns.return_value = patterns
    return d


def test_defined_pattern_gets_category_and_snippet():
    hint = _detector_with([{
        'id': 1, 'created_by': 'user', 'source': 'local',
        'sponsor': 'Acme', 'category': 'sponsor',
        'intro_text': 'this episode is brought to you by acme the best widgets money can buy today'}
    ])._build_known_pattern_hint('example-podcast')
    assert 'Acme' in hint
    assert 'sponsor' in hint
    assert 'this episode is brought to you by acme' in hint
    assert 'ads on this feed' in hint  # scrutiny line


def test_auto_pattern_name_only():
    hint = _detector_with([{
        'id': 2, 'created_by': 'auto', 'source': 'local',
        'sponsor': 'WidgetCo', 'category': 'sponsor', 'intro_text': 'buy widgets now ok'}
    ])._build_known_pattern_hint('example-podcast')
    assert 'WidgetCo' in hint
    assert 'buy widgets now' not in hint


def test_no_patterns_no_hint():
    assert _detector_with([])._build_known_pattern_hint('example-podcast') == ''


def test_tier1_capped_at_12():
    pats = [{'id': i, 'created_by': 'user', 'source': 'local',
             'sponsor': f'S{i}', 'category': 'sponsor',
             'intro_text': 'x' * 120} for i in range(20)]
    hint = _detector_with(pats)._build_known_pattern_hint('example-podcast')
    assert hint.count('Opens like') == 12
