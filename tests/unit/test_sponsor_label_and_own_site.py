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
        # The name is not repeated, and the description it used to swallow
        # is all still there.
        assert ads[0]['reason'].startswith('Box: sponsor read begins')
        assert 'box.com/AI' in ads[0]['reason']

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

    def test_an_embellished_continuation_note_is_not_a_sponsor(self):
        """Stripping the prefix and keeping the remainder minted labels like
        'window' and 'in next segment'."""
        for note in ('continues in next window',
                     'continued in next segment',
                     'continues from previous; more detail'):
            ads = parse_ads_from_response(json.dumps([
                {'start': 100, 'end': 160, 'note': note, 'confidence': 0.9}]),
                'slug', 'ep')
            assert ads and ads[0].get('sponsor') is None, note

    def test_a_listed_continuation_note_is_not_a_sponsor(self):
        ads = parse_ads_from_response(json.dumps([
            {'start': 100, 'end': 160, 'sponsor': ['continues in next window'],
             'confidence': 0.9}]), 'slug', 'ep')

        assert ads and ads[0].get('sponsor') is None

    def test_a_real_brand_in_a_note_still_counts(self):
        ads = parse_ads_from_response(json.dumps([
            {'start': 100, 'end': 160, 'note': 'Acme', 'confidence': 0.9}]),
            'slug', 'ep')

        assert ads[0]['sponsor'] == 'Acme'


class TestRationaleFilterDoesNotEatDescriptions:
    """A full ad description can mention the transcript in passing. Restoring
    those descriptions started tripping the rationale filter, which rejected
    legitimate pattern learning."""

    DESCRIPTION = (
        "Arctic Wolf: Host-read ad break beginning with 'this show brought to "
        "you today by Arctic Wolf' at ~3769.7s. Ad ends at ~3938.7s when the "
        "host says 'our hydration break is over'. Overlapping timestamps in "
        "transcript appear to be duplicated audio channel text; ad content "
        "itself concludes with the arcticwolf.com/trends CTA and thanks."
    )

    def test_a_long_description_is_not_a_rationale(self):
        from utils.constants import is_sponsor_reasoning_rationale
        assert not is_sponsor_reasoning_rationale(self.DESCRIPTION)

    def test_a_rationale_only_string_still_is(self):
        from utils.constants import is_sponsor_reasoning_rationale
        assert is_sponsor_reasoning_rationale(
            'Inferred from ~26 second gap in transcript with no spoken content')
        assert is_sponsor_reasoning_rationale('volume anomaly at 42s')

    def test_a_rationale_prefix_decides_at_any_length(self):
        from utils.constants import is_sponsor_reasoning_rationale
        assert is_sponsor_reasoning_rationale('Inferred from ' + 'x' * 400)


class TestReasonRegexWordBoundaries:
    """"ad" must not match inside a longer word."""

    def test_mailing_address_is_not_a_sponsor(self):
        from sponsor_service import SponsorService
        text = ('Host-read self-promotion: donation links, mailing address, '
                'example.com bonus-episode subscription pitch')
        assert SponsorService.extract_sponsor_from_reason(text) is None

    def test_a_real_ad_phrase_still_resolves(self):
        from sponsor_service import SponsorService
        assert SponsorService.extract_sponsor_from_reason(
            'This is a BetterHelp ad for listeners') == 'BetterHelp'


class TestContinuationScaffoldingStrippedFromReason:
    """The window prompt asks for these notes; a reader should not see them."""

    def test_leading_continuation_note_is_dropped(self):
        ads = parse_ads_from_response(json.dumps([
            {'start': 840, 'end': 900, 'type': 'promo',
             'note': 'continues from previous; host self-promo for Example.com'}]),
            'slug', 'ep')
        assert ads[0]['reason'] == 'host self-promo for Example.com'

    def test_continues_in_next_is_dropped_too(self):
        ads = parse_ads_from_response(json.dumps([
            {'start': 840, 'end': 900, 'sponsor': 'Acme',
             'note': 'continues in next: Acme pitch with promo code SAVE'}]),
            'slug', 'ep')
        assert 'continues in next' not in ads[0]['reason']
        assert ads[0]['reason'] == 'Acme: pitch with promo code SAVE'

    def test_a_description_that_merely_mentions_it_is_untouched(self):
        ads = parse_ads_from_response(json.dumps([
            {'start': 840, 'end': 900, 'sponsor': 'Acme',
             'note': 'Acme read that continues in next window'}]), 'slug', 'ep')
        assert 'continues in next window' in ads[0]['reason']


class TestShowNameIsNotASponsor:
    """A self-promo or listener-support read has no advertiser, so the model
    puts the show's own name in the sponsor slot. Seen in production as a
    Patreon thank-you credited to "Dailytechnewsshow"."""

    SHOW = 'Daily Tech News Show'

    def test_the_show_name_is_rejected(self):
        from utils.constants import sanitize_sponsor_label
        for label in ('Dailytechnewsshow', 'daily tech news show',
                      'Daily-Tech-News-Show', '  Daily Tech News Show  '):
            assert sanitize_sponsor_label(label, show_name=self.SHOW) is None, label

    def test_a_real_sponsor_survives(self):
        from utils.constants import sanitize_sponsor_label
        for label in ('Squarespace', 'Morning Brew Daily', 'Cologuard'):
            assert sanitize_sponsor_label(label, show_name=self.SHOW) == label

    def test_without_a_show_name_nothing_changes(self):
        from utils.constants import sanitize_sponsor_label
        assert sanitize_sponsor_label('Dailytechnewsshow') == 'Dailytechnewsshow'

    def test_a_sponsor_that_merely_contains_the_show_name_survives(self):
        """Only an exact match counts; a brand is not the show because the
        show's name appears inside it."""
        from utils.constants import sanitize_sponsor_label
        assert sanitize_sponsor_label('Daily Tech News Show Store',
                                      show_name=self.SHOW) == 'Daily Tech News Show Store'


class TestBrandFurtherFromTheReadPhrase:
    """A brand can open the reason with the read described several words later,
    and can be written as a slash-joined pair. The tight patterns need the
    brand within two words of "sponsor read", and \\w never matches a slash."""

    def _extract(self, text):
        from sponsor_service import SponsorService
        return SponsorService.extract_sponsor_from_reason(text)

    def test_a_slash_joined_brand_reduces_to_its_first_form(self):
        assert self._extract(
            'PestEase/Pesti pest control sponsor read with a discount offer'
        ) == 'PestEase'

    def test_a_brand_separated_by_a_descriptor_is_found(self):
        assert self._extract(
            'Squarespace website builder sponsor read') == 'Squarespace'

    def test_a_lowercase_opening_is_not_a_brand(self):
        assert self._extract('the host reads a sponsor read') is None
        assert self._extract('ad break sponsor read') is None

    def test_the_mailing_address_regression_stays_fixed(self):
        assert self._extract('mailing address mentioned in passing') is None

    def test_a_plain_leading_brand_is_unchanged(self):
        assert self._extract('Acme sponsor read') == 'Acme'


class TestAdvertisersListedAfterAColon:
    """A break that names its advertisers after a colon matched nothing, so
    the ad-evidence gate saw no sponsor and dropped a real 158s break as
    content. A hyphenated compound before "sponsor" also captured a fragment.
    """

    def _extract(self, text):
        from sponsor_service import SponsorService
        return SponsorService.extract_sponsor_from_reason(text)

    def test_a_hyphenated_compound_does_not_yield_a_fragment(self):
        """"host-read sponsor spots" stored "read" as the advertiser."""
        assert self._extract(
            'Ad break with host-read sponsor spots: IQ Bar (text Tosh to 64000)'
        ) == 'IQ Bar'

    def test_the_first_advertiser_of_a_list_becomes_the_label(self):
        assert self._extract(
            'Ad break with three DAI host/produced reads: Serval '
            '(serval.com/tickets), Just Food for Dogs, and LifeLock'
        ) == 'Serval'

    def test_a_pre_roll_list_is_matched_too(self):
        assert self._extract(
            'Back-to-back dynamically-inserted pre-roll ads: Serval '
            '(serval.com/tickets) and Lincoln Tech (lincolntech.edu), merged'
        ) == 'Serval'

    def test_a_non_brand_after_the_colon_is_refused(self):
        assert self._extract('Ad break: Host discusses the news at length') is None
        assert self._extract('Ad segment: This is regular content') is None

    def test_a_colon_without_ad_context_is_refused(self):
        assert self._extract('Chapter marker: Introduction to the topic') is None

    def test_the_brand_capture_does_not_swallow_the_sentence(self):
        """The brand-shape patterns must run case-sensitively. Under
        re.IGNORECASE, [A-Z] matches any letter, so the capitalized-run
        capture ran to the end of the clause."""
        assert self._extract(
            'Back-to-back dynamically inserted ads (audio confirmed DAI): '
            'Hykes law enforcement boots with URL HykesUSA.com, followed by '
            'Belmont Park Village luxury outlet promo'
        ) == 'Hykes'

    def test_a_multiword_brand_after_the_colon_is_kept_whole(self):
        assert self._extract(
            'Ad block: Belmont Park Village luxury outlet promo'
        ) == 'Belmont Park Village'
