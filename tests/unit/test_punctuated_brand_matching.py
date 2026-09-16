"""A brand whose name ends in punctuation has to match wherever brands match.

A word boundary sits between a word character and a non-word one, so the
seeded 'Liquid IV' alias "Liquid I.V." has no boundary after its trailing dot
and a \\b-anchored matcher never matched it. Every brand scan goes through
utils.text.word_boundary_re lookarounds instead.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_validator import AdValidator
from sponsor_service import SponsorService
from text_pattern_matcher import TextPatternMatcher

AD_TEXT = ('Liquid I.V. keeps you hydrated all day. Get your first order of '
           'Liquid I.V. with promo code POD at checkout.')


class TestTheRegistryMatcher:
    def test_it_finds_both_mentions(self, temp_db):
        offsets = SponsorService(temp_db).brand_mention_offsets(AD_TEXT)

        assert len(offsets['Liquid IV']) == 2

    def test_a_longer_word_is_still_not_a_mention(self, temp_db):
        assert 'Ramp' not in SponsorService(temp_db).brand_mention_offsets(
            'the cramped studio')


class TestTheValidatorConfirmsIt:
    def test_two_mentions_confirm_the_read(self, temp_db):
        validator = AdValidator(
            3600.0, [{'start': 0.0, 'end': 400.0, 'text': AD_TEXT}],
            episode_description='', min_cut_confidence=0.80,
            sponsor_service=SponsorService(temp_db))

        assert validator._registry_confirms({'start': 0.0, 'end': 400.0}) is True


class TestLearningSeesTheContamination:
    def test_a_second_brand_read_blocks_the_pattern(self, temp_db):
        temp_db.create_known_sponsor(name='Acme Tools')
        matcher = TextPatternMatcher(db=temp_db)

        assert matcher._contaminating_brands(
            f'Acme Tools is our sponsor. {AD_TEXT}', 'Acme Tools') == ['Liquid IV']
