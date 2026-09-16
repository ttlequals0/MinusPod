"""Confirming an ad's sponsor from the episode description."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_validator import AdValidator
from utils.text import word_boundary_re


def _validator(description='', segments=None, sponsors=None):
    v = AdValidator(1200.0, segments or [], episode_description=description)
    if sponsors is not None:
        v.description_sponsors = set(sponsors)
        v._description_sponsor_re = word_boundary_re(sponsors)
    return v


class TestDescriptionHarvest:
    def test_a_real_brand_link_is_harvested(self):
        v = _validator('<a href="https://betterhelp.com/show">BetterHelp</a>')
        assert 'betterhelp' in v.description_sponsors

    def test_a_hosting_platform_is_never_a_sponsor(self):
        v = _validator('<a href="https://acast.com/privacy">Privacy</a>'
                       '<a href="https://megaphone.fm/adchoices">Choices</a>')
        assert 'acast' not in v.description_sponsors
        assert 'megaphone' not in v.description_sponsors

    def test_listening_apps_and_socials_are_dropped(self):
        v = _validator('<a href="https://apple.com/podcast">Apple</a>'
                       '<a href="https://spotify.com/show">Spotify</a>'
                       '<a href="https://instagram.com/show">Us</a>')
        assert v.description_sponsors == set()

    def test_a_three_letter_outlet_token_is_dropped(self):
        v = _validator('<a href="https://wsj.com/article">Read it</a>')
        assert 'wsj' not in v.description_sponsors

    def test_a_curated_short_brand_survives_the_length_floor(self):
        v = _validator('<a href="https://ag1.com/show">AG1</a>')
        assert 'ag1' in v.description_sponsors

    def test_a_spoken_two_word_brand_is_kept_in_both_spellings(self):
        v = _validator('This episode is sponsored by Liquid IV.')
        assert {'liquid iv', 'liquidiv'} <= v.description_sponsors


class TestWordBoundaryConfirmation:
    ADS = {'start': 10.0, 'end': 40.0, 'reason': 'sponsor read'}

    def _segments(self, text):
        return [{'start': 10.0, 'end': 40.0, 'text': text}]

    def test_a_token_inside_a_longer_word_does_not_confirm(self):
        v = _validator('<a href="https://wsj.com/x">WSJ</a>',
                       self._segments('The wsjournal covered it at length.'),
                       sponsors={'wsj'})
        assert v._is_sponsor_confirmed(dict(self.ADS)) is False

    def test_the_same_token_standing_alone_confirms(self):
        v = _validator('', self._segments('Brought to you by WSJ this week.'),
                       sponsors={'wsj'})
        assert v._is_sponsor_confirmed(dict(self.ADS)) is True

    def test_a_real_brand_still_confirms_from_the_reason(self):
        v = _validator('<a href="https://betterhelp.com/show">BetterHelp</a>',
                       self._segments('Ordinary conversation here.'))
        ad = dict(self.ADS, reason='BetterHelp sponsor read with a code')
        assert v._is_sponsor_confirmed(ad) is True

    def test_a_real_brand_still_confirms_from_the_transcript(self):
        v = _validator('<a href="https://betterhelp.com/show">BetterHelp</a>',
                       self._segments('Try BetterHelp and get ten percent off.'))
        assert v._is_sponsor_confirmed(dict(self.ADS)) is True

    def test_the_match_is_case_insensitive(self):
        assert word_boundary_re(['betterhelp']).search('Visit BetterHelp today')

    def test_nothing_to_match_compiles_to_nothing(self):
        assert word_boundary_re([]) is None
        assert word_boundary_re(['']) is None

    def test_a_brand_ending_in_punctuation_still_matches(self):
        pattern = word_boundary_re(['Liquid I.V.', 'Yahoo!'])
        assert pattern.search('try Liquid I.V. today')
        assert pattern.search('over on Yahoo! Finance')
