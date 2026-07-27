"""A pattern marker has to say what it matched, not just name the pattern."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_detector import _pattern_match_evidence
from text_pattern_matcher import TextMatch
from utils.constants import PATTERN_EVIDENCE_MAX_CHARS


def _match(**kw):
    base = dict(pattern_id=485, start=0.0, end=60.0, confidence=0.86,
                sponsor='Squarespace', match_type='outro')
    base.update(kw)
    return TextMatch(**base)


class TestPatternMatchEvidence:
    def test_quotes_the_transcript_text_that_matched(self):
        match = _match(matched_text='slash rogan for a free trial')
        assert _pattern_match_evidence(match, match.match_type) == (
            'outro "slash rogan for a free trial" 86%')

    def test_falls_back_to_the_kind_when_nothing_was_captured(self):
        assert _pattern_match_evidence(_match(), 'outro') == 'outro 86%'

    def test_uses_the_caller_kind_for_a_match_without_a_type(self):
        class FingerprintMatch:
            confidence = 0.99

        assert _pattern_match_evidence(
            FingerprintMatch(), 'fingerprint') == 'fingerprint 99%'

    def test_truncates_a_long_quote(self):
        evidence = _pattern_match_evidence(_match(matched_text='x' * 500), 'outro')
        assert len(evidence) < PATTERN_EVIDENCE_MAX_CHARS + 30
        assert evidence.endswith('..." 86%')


class TestReasonCarriesEvidence:
    def _detector(self):
        from ad_detector import AdDetector
        det = AdDetector.__new__(AdDetector)
        det.pattern_service = None
        return det

    def test_sponsor_reason_names_the_matched_words(self):
        all_ads, regions = [], []
        match = _match(matched_text='if you know, you vrbo')
        self._detector()._add_pattern_match(
            match, 'text_pattern', match.match_type, all_ads, regions,
            episode_id='ep1')

        assert all_ads[0]['reason'] == (
            'Squarespace (pattern #485, outro "if you know, you vrbo" 86%)')

    def test_reason_without_a_sponsor_still_names_the_match(self):
        all_ads, regions = [], []
        match = _match(sponsor=None, matched_text='brought to you by')
        self._detector()._add_pattern_match(
            match, 'text_pattern', match.match_type, all_ads, regions,
            episode_id='ep1')

        assert all_ads[0]['reason'] == (
            'Pattern #485 (outro "brought to you by" 86%)')
