"""A list sponsor must not be stored as its Python repr, and a host's own
site must not be harvested as an advertiser."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_detector.prompts import parse_ads_from_response
from sponsor_service import SponsorService


def _response(sponsor):
    return json.dumps([{'start': 100, 'end': 160, 'sponsor': sponsor,
                        'confidence': 0.9}])


class TestListSponsorIsReadable:
    def test_two_sponsors_are_joined_not_repr_ed(self):
        ads = parse_ads_from_response(
            _response(['Dodge', "The Farmer's Dog"]), 'slug', 'ep1')

        assert ads[0]['sponsor'] == "Dodge, The Farmer's Dog"

    def test_plain_string_sponsor_is_unchanged(self):
        ads = parse_ads_from_response(_response('Squarespace'), 'slug', 'ep1')
        assert ads[0]['sponsor'] == 'Squarespace'

    def test_empty_entries_are_dropped(self):
        ads = parse_ads_from_response(_response(['Dodge', '', None]), 'slug', 'ep1')
        assert ads[0]['sponsor'] == 'Dodge'


class TestOwnSiteTokens:
    def test_covers_the_domain_a_host_plugs_in_a_read(self):
        assert 'joerogan' in SponsorService.own_site_tokens(
            'The Joe Rogan Experience')

    def test_a_single_title_word_can_still_be_an_advertiser(self):
        # "save" is a plausible brand; only runs of two or more words, and the
        # whole name, are treated as the show's own site.
        tokens = SponsorService.own_site_tokens('Pod Save America')
        assert 'save' not in tokens
        assert 'podsaveamerica' in tokens

    def test_a_one_word_show_name_is_still_covered(self):
        assert SponsorService.own_site_tokens('Smartless') == {'smartless'}

    def test_no_name_yields_nothing(self):
        assert SponsorService.own_site_tokens(None) == set()
        assert SponsorService.own_site_tokens('') == set()


class TestExtractSponsorsExcludesOwnSite:
    TEXT = ('this episode is brought to you by squarespace, the home of my '
            'website, joerogan.com. if you want to level up')

    def test_host_domain_is_not_a_sponsor(self):
        found = SponsorService.extract_sponsors_from_transcript(
            self.TEXT,
            exclude=SponsorService.own_site_tokens('The Joe Rogan Experience'))

        assert 'joerogan' not in found

    def test_real_sponsors_are_kept(self):
        found = SponsorService.extract_sponsors_from_transcript(
            'go to betterhelp.com slash rogan today',
            exclude=SponsorService.own_site_tokens('The Joe Rogan Experience'))

        assert 'betterhelp' in found

    def test_without_an_exclusion_set_nothing_changes(self):
        assert 'joerogan' in SponsorService.extract_sponsors_from_transcript(
            self.TEXT)
