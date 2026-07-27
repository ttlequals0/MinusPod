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
        assert 'dailytech' in SponsorService.own_site_tokens(
            'The Daily Tech Show')

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
            'website, dailytech.com. if you want to level up')

    def test_host_domain_is_not_a_sponsor(self):
        found = SponsorService.extract_sponsors_from_transcript(
            self.TEXT,
            exclude=SponsorService.own_site_tokens('The Daily Tech Show'))

        assert 'dailytech' not in found

    def test_real_sponsors_are_kept(self):
        found = SponsorService.extract_sponsors_from_transcript(
            'go to betterhelp.com slash show today',
            exclude=SponsorService.own_site_tokens('The Daily Tech Show'))

        assert 'betterhelp' in found

    def test_without_an_exclusion_set_nothing_changes(self):
        assert 'dailytech' in SponsorService.extract_sponsors_from_transcript(
            self.TEXT)


class TestReasonKeepsItsDescription:
    """A bare sponsor name must not swallow the note that explains the read.

    The name is both a prefix of its own description and a full word subset of
    it, so the duplicate check discarded the description and left markers
    reading only "Box" or "Gusto". A long ad then failed the evidence gate and
    was dropped outright.
    """

    NOTE = ("Box sponsor read begins at 'This episode is brought to you by "
            "Box' (box.com/AI); continues in next")

    def _parse(self, sponsor_key):
        ad = {'ad_start': 1710.8, 'ad_end': 1860.0, 'note': self.NOTE}
        ad[sponsor_key] = ['Box'] if sponsor_key == 'sponsors' else 'Box'
        return parse_ads_from_response(json.dumps([ad]), 'slug', 'ep')

    def test_description_survives_a_sponsor_name_prefix(self):
        ads = self._parse('sponsor')
        assert len(ads) == 1
        assert ads[0]['reason'].startswith('Box: Box sponsor read begins')

    def test_a_pluralized_sponsor_key_still_counts_as_evidence(self):
        """The model writes "sponsors" sometimes. extract_sponsor_name already
        read it; the evidence gate did not, so the ad was rejected."""
        ads = self._parse('sponsors')
        assert len(ads) == 1
        assert 'box.com/AI' in ads[0]['reason']

    def test_genuinely_duplicate_text_is_still_collapsed(self):
        ad = {'ad_start': 10.0, 'ad_end': 90.0, 'sponsor': 'Acme sponsor read',
              'note': 'Acme sponsor read'}
        ads = parse_ads_from_response(json.dumps([ad]), 'slug', 'ep')
        assert ads[0]['reason'] == 'Acme sponsor read'


class TestContinuationNotesAreNotSponsors:
    """The window prompt asks for note "continues in next", and `note` is a
    sponsor-candidate key, so a short one became the brand name and reached
    pattern learning as a sponsor."""

    def test_a_continuation_note_is_not_a_sponsor(self):
        ads = parse_ads_from_response(json.dumps([
            {'start': 100, 'end': 160, 'note': 'continues in next',
             'confidence': 0.9}]), 'slug', 'ep')

        assert ads and ads[0].get('sponsor') is None

    def test_continues_from_previous_is_also_rejected(self):
        ads = parse_ads_from_response(json.dumps([
            {'start': 100, 'end': 160, 'note': 'continues from previous',
             'confidence': 0.9}]), 'slug', 'ep')

        assert ads and ads[0].get('sponsor') is None

    def test_a_real_brand_in_a_note_still_counts(self):
        ads = parse_ads_from_response(json.dumps([
            {'start': 100, 'end': 160, 'note': 'Acme', 'confidence': 0.9}]),
            'slug', 'ep')

        assert ads[0]['sponsor'] == 'Acme'
