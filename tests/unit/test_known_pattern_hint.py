"""Tests for the tiered known-pattern hint injected into the pass-1 prompt."""
from tests.app_bootstrap import bootstrap
bootstrap('known_pattern_hint_test_')

from unittest.mock import MagicMock
from ad_detector import AdDetector


def _detector_with(patterns, sponsor_categories=None):
    d = AdDetector.__new__(AdDetector)
    d.db = MagicMock()
    d.db.get_ad_patterns.return_value = patterns
    d.db.get_sponsor_segment_categories.return_value = sponsor_categories or {}
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


def test_sponsor_category_lists_auto_pattern_with_category():
    hint = _detector_with([{
        'id': 3, 'created_by': 'auto', 'source': 'local',
        'sponsor': 'Acme', 'category': 'sponsor', 'intro_text': 'my drive died'}],
        sponsor_categories={'acme': 'self_promo'},
    )._build_known_pattern_hint('example-podcast')
    assert '- Acme (self_promo read).' in hint
    assert 'Previously detected sponsors' not in hint


def test_sponsor_category_outranks_defined_pattern_category():
    hint = _detector_with([{
        'id': 4, 'created_by': 'user', 'source': 'local',
        'sponsor': 'Acme', 'category': 'sponsor',
        'intro_text': 'a listener wrote in about acme saving a drive today'}],
        sponsor_categories={'acme': 'self_promo'},
    )._build_known_pattern_hint('example-podcast')
    assert hint.count('Acme') == 1
    assert '- Acme (self_promo read). Opens like:' in hint


def test_uncategorized_names_stay_in_leftovers():
    hint = _detector_with([
        {'id': 5, 'created_by': 'auto', 'source': 'local', 'sponsor': 'Acme',
         'category': 'sponsor', 'intro_text': 'x'},
        {'id': 6, 'created_by': 'auto', 'source': 'local', 'sponsor': 'Widgets',
         'category': 'sponsor', 'intro_text': 'y'}],
        sponsor_categories={'widgets': 'self_promo'},
    )._build_known_pattern_hint('example-podcast')
    assert 'Previously detected sponsors for this podcast: Acme' in hint
    assert '- Widgets (self_promo read).' in hint


def test_hint_initializes_services_when_first_caller(caplog):
    from unittest.mock import patch
    stub = MagicMock()
    stub.get_ad_patterns.return_value = [{
        'id': 7, 'created_by': 'user', 'source': 'local', 'sponsor': 'Acme',
        'category': 'sponsor', 'intro_text': 'brought to you by acme'}]
    stub.get_sponsor_segment_categories.return_value = {}
    d = AdDetector()
    d.db = None
    with patch('database.Database', return_value=stub), patch('ad_detector.SponsorService'), \
            patch('audio_fingerprinter.AudioFingerprinter'), patch('text_pattern_matcher.TextPatternMatcher'), \
            patch('pattern_service.PatternService'), caplog.at_level('WARNING'):
        hint = d._build_known_pattern_hint('example-podcast')
    assert 'Acme' in hint
    assert 'Could not fetch patterns' not in caplog.text


def _learner(registry=()):
    d = AdDetector.__new__(AdDetector)
    d.db = MagicMock()
    d.db.get_setting_float.side_effect = lambda key, default: default
    d.db.get_active_pattern_sponsors.return_value = set(registry)
    d.initialize_client = MagicMock()
    d.text_pattern_matcher = MagicMock()
    d.text_pattern_matcher.create_patterns_from_ad.return_value = [1]
    d.sponsor_service = MagicMock()
    d.sponsor_service.get_sponsors.return_value = [{'name': n} for n in registry]
    d.sponsor_service.find_sponsor_in_text.side_effect = lambda text: text if text in registry else None
    return d


def _mark(sponsor, **extra):
    return {'start': 10.0, 'end': 60.0, 'confidence': 1.0, 'source': 'sponsorblock',
            'detection_stage': 'sponsorblock', 'was_cut': True, 'sponsor': sponsor,
            'category': 'sponsor', **extra}


def test_sponsorblock_label_sponsor_mark_makes_no_pattern_attempt():
    d = _learner()
    segs = [{'start': 10.0, 'end': 60.0, 'text': 'this is a long sponsor read'}]
    assert d.learn_from_detections([_mark('Intro', label_sponsor=True)], segs, 'example-podcast', 'a1b2c3d4e5f6') == 0
    d.text_pattern_matcher.create_patterns_from_ad.assert_not_called()


def test_sponsorblock_mark_learns_registry_sponsor():
    d = _learner(registry={'Acme'})
    segs = [{'start': 10.0, 'end': 60.0, 'text': 'this is a long sponsor read'}]
    assert d.learn_from_detections([_mark('Acme')], segs, 'example-podcast', 'a1b2c3d4e5f6') == 1
    assert d.text_pattern_matcher.create_patterns_from_ad.call_args.kwargs['sponsor'] == 'Acme'
