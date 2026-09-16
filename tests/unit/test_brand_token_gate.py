"""Junk that reached the sponsor slot and the boundary-extension brand set."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from sponsor_service import SponsorService
from utils.constants import (
    is_brand_token, is_hosting_platform_name, is_non_brand_name,
    sanitize_sponsor_label,
)


class TestIsBrandToken:
    @pytest.mark.parametrize('token', [
        'betterhelp', 'athleticgreens', 'liquidiv', 'vention', 'article',
    ])
    def test_a_brand_token_passes(self, token):
        assert is_brand_token(token) is True

    @pytest.mark.parametrize('token', [
        'info', 'www', 'blog', 'support', 'privacy', 'shop',
    ])
    def test_a_generic_web_word_is_rejected(self, token):
        assert is_brand_token(token) is False

    @pytest.mark.parametrize('token', ['zoominfo', 'shoponline'])
    def test_a_brand_ending_in_a_web_word_is_still_a_candidate(self, token):
        """It is a candidate, not a cut: it still has to be spoken in the span.
        Rejecting the shape lost real URL brands."""
        assert is_brand_token(token) is True

    def test_a_hosting_platform_is_rejected(self):
        assert is_brand_token('acast') is False
        assert is_brand_token('megaphone') is False

    def test_a_token_below_the_floor_is_rejected(self):
        assert is_brand_token('wsj') is False

    def test_a_curated_short_brand_is_exempt_from_the_floor(self):
        assert is_brand_token('ag1') is True

    def test_empty_input_is_rejected(self):
        assert is_brand_token('') is False
        assert is_brand_token(None) is False


class TestTranscriptSponsorHarvest:
    def test_generic_web_tokens_do_not_become_sponsors(self):
        text = 'For more, example info dot com has the details.'
        assert SponsorService.extract_sponsors_from_transcript(text) == set()

    def test_a_url_brand_ending_in_a_web_word_is_harvested(self):
        text = 'Their sales team runs on zoominfo.com every day.'
        assert 'zoominfo' in SponsorService.extract_sponsors_from_transcript(text)

    def test_a_real_brand_url_is_still_harvested(self):
        text = 'Visit betterhelp.com/show for ten percent off your first month.'
        assert 'betterhelp' in SponsorService.extract_sponsors_from_transcript(text)

    def test_a_hosting_domain_is_not_harvested(self):
        text = 'Hosted on Acast. See acast.com/privacy for more information.'
        assert 'acast' not in SponsorService.extract_sponsors_from_transcript(text)

    def test_a_spoken_dot_com_brand_is_still_harvested(self):
        text = 'Head to squarespace dot com for a free trial today.'
        assert 'squarespace' in SponsorService.extract_sponsors_from_transcript(text)


class TestSponsorLabelGate:
    @pytest.mark.parametrize('label', [
        'Produced', 'Non-English', 'Post-signoff', "Show's", 'Material',
        'Final', 'our sponsor',
    ])
    def test_role_and_structural_labels_are_rejected(self, label):
        assert sanitize_sponsor_label(label) is None

    def test_a_host_lead_in_yields_the_brand_behind_it(self):
        assert sanitize_sponsor_label(
            'their friends and sponsors at Threat Locker') == 'Threat Locker'

    @pytest.mark.parametrize('label,brand', [
        ('our friends at Acme', 'Acme'),
        ('the partners at Beta Corp', 'Beta Corp'),
        ('sponsors from Gamma Industries', 'Gamma Industries'),
    ])
    def test_other_lead_in_shapes_yield_the_brand(self, label, brand):
        assert sanitize_sponsor_label(label) == brand

    @pytest.mark.parametrize('label', ['Presented Health', 'Written Word'])
    def test_a_credit_verb_without_by_is_part_of_the_name(self, label):
        """Only "<verb> by" is a credit; the bare verb is the first word of a
        real brand at least as often as it is a label."""
        assert sanitize_sponsor_label(label) == label

    def test_a_credit_lead_in_yields_the_brand_behind_it(self):
        assert sanitize_sponsor_label('Presented by Acme') == 'Acme'

    def test_the_shows_own_name_behind_a_credit_lead_in_is_rejected(self):
        assert sanitize_sponsor_label(
            'Presented by Acme Show', show_name='Acme Show') is None

    def test_a_possessive_in_front_of_a_real_brand_keeps_the_whole_name(self):
        assert sanitize_sponsor_label('Our Place') == 'Our Place'

    @pytest.mark.parametrize('brand', [
        'BetterHelp', 'GRC', 'State Farm', 'Squarespace', 'Capital One',
    ])
    def test_real_advertisers_are_untouched(self, brand):
        assert sanitize_sponsor_label(brand) == brand


class TestNonBrandNameGate:
    @pytest.mark.parametrize('label', ['Mid-Roll', 'Post-roll'])
    def test_an_ad_shape_label_is_not_a_brand(self, label):
        assert is_non_brand_name(label) is True

    @pytest.mark.parametrize('label', ['The Gap', 'The Post', 'Content Network',
                                       'Full Spot'])
    def test_ordinary_words_a_real_brand_is_built_from_pass(self, label):
        """Every word here appears in NON_BRAND_WORDS, which also feeds keyword
        extraction; only audio-signal and structural labels rule a name out."""
        assert is_non_brand_name(label) is False

    @pytest.mark.parametrize('label', ['Post-signoff', 'Non-English',
                                       'volume_decrease', 'Produced'])
    def test_a_signal_or_structure_label_is_rejected(self, label):
        assert is_non_brand_name(label) is True


class TestHostingPlatformNames:
    @pytest.mark.parametrize('name', ['Hosted on Acast', 'Acast ads',
                                      'Acast.com', 'Anchor FM', 'acast'])
    def test_a_platform_behind_a_lead_in_or_suffix_is_recognized(self, name):
        assert is_hosting_platform_name(name) is True

    @pytest.mark.parametrize('name', ['Liquid I.V.', 'Acme', 'Acme FM Radio'])
    def test_an_advertiser_is_not_a_platform(self, name):
        assert is_hosting_platform_name(name) is False
