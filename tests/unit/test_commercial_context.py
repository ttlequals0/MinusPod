"""Host-read closings as commercial language, and the splice-veto sponsor waiver."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_validator import AdValidator, Decision
from utils.text import word_boundary_re

CLOSING = ("Learn more at acme.com. That's A-C-M-E.com. "
           "Let me thank them so much for supporting the show.")


class _Registry:
    """Reports Acme wherever the text names it, spelled out or not."""

    def brand_mention_offsets(self, text):
        low = (text or '').lower()
        offsets = [i for i in range(len(low))
                   if low.startswith('acme', i) or low.startswith('a-c-m-e', i)]
        return {'Acme': offsets} if offsets else {}


def _validator(text, registry=True):
    segments = [{'start': 0.0, 'end': 400.0, 'text': text}]
    return AdValidator(3600.0, segments, episode_description='',
                       sponsor_service=_Registry() if registry else None)


_SPAN = {'start': 0.0, 'end': 400.0}


class TestCommercialContext:
    def test_production_closing_is_commercial(self):
        v = _validator(CLOSING)
        assert v._has_local_commercial_context(_SPAN, 'Acme') is True
        assert v._registry_confirms(dict(_SPAN)) is True

    @pytest.mark.parametrize('text', [
        'Acme keeps you safe, find it at Acme dot com.',
        'Acme is great. That is A C M E dot com.',
        'Acme is great. Check them out at acme.com.',
        'Acme is great. Find out more at acme.io.',
        "Acme is great. That's A-C-M-E.com.",
    ])
    def test_bare_and_spelled_links_are_commercial(self, text):
        assert _validator(text, registry=False)._has_local_commercial_context(
            _SPAN, 'Acme') is True

    @pytest.mark.parametrize('text', [
        'Thanks to Acme for supporting the show.',
        'Thank you to Acme for sponsoring this episode.',
        'Thanks to Acme for sponsoring this episode.',
        'A word from our sponsor at Acme.',
        'Acme is our sponsor.',
        'This week, Acme is our sponsor.',
    ])
    def test_framing_phrases_are_commercial(self, text):
        assert _validator(text, registry=False)._has_local_commercial_context(
            _SPAN, 'Acme') is True

    def test_conversational_mention_is_not_commercial(self):
        v = _validator('I use Acme at home, Acme is fine')
        assert v._has_local_commercial_context(_SPAN, 'Acme') is False
        assert v._registry_confirms(dict(_SPAN)) is False

    def test_thanks_for_listening_is_not_sponsor_framing(self):
        v = _validator('Thank you for listening, Acme was mentioned earlier.',
                       registry=False)
        assert v._has_local_commercial_context(_SPAN, 'Acme') is False

    @pytest.mark.parametrize('registry', [True, False])
    def test_learn_more_at_another_domain_is_not_commercial(self, registry):
        v = _validator('Learn more at wikipedia.org, Acme did well.',
                       registry=registry)
        assert v._has_local_commercial_context(_SPAN, 'Acme') is False

    @pytest.mark.parametrize('registry', [True, False])
    def test_thanks_to_someone_else_is_not_sponsor_framing(self, registry):
        v = _validator('Thanks to our listeners, Acme came up again.',
                       registry=registry)
        assert v._has_local_commercial_context(_SPAN, 'Acme') is False

    @pytest.mark.parametrize('registry', [True, False])
    @pytest.mark.parametrize('text', [
        'Acme reported earnings. Acme.com revenue rose nine percent.',
        'Thanks to Acme, shipping is faster.',
        'Our friends at Acme decided to raise prices.',
        'Acme is a sponsor of the league now.',
        'Acme is our sponsor this week.',
        'Thank you for listening, Acme came up.',
    ])
    def test_content_phrasing_is_not_commercial(self, text, registry):
        v = _validator(text, registry=registry)
        assert v._has_local_commercial_context(_SPAN, 'Acme') is False

    def test_other_domain_is_not_commercial(self):
        v = _validator('Acme came up. Go to othersite.com today.', registry=False)
        assert v._has_local_commercial_context(_SPAN, 'Acme') is False


def _calibrated_no_events():
    return {'splice_evidence': {'version': 1, 'events': [],
                                'calibration': {'status': 'calibrated'}}}


class TestSpliceVetoSponsorWaiver:
    """A 187 s cut on a calibrated feed with no audio corroboration."""

    def _run(self, text, stage='claude', registry=False, description=None,
             reason='Sponsor read'):
        segments = [{'start': 1000.0, 'end': 1187.0, 'text': text}]
        v = AdValidator(3600.0, segments, episode_description='',
                        sponsor_service=_Registry() if registry else None)
        if description:
            v._description_sponsor_re = word_boundary_re((description,))
        ad = {'start': 1000.0, 'end': 1187.0, 'confidence': 0.95,
              'reason': reason, 'detection_stage': stage}
        return v.validate([ad], audio_analysis=_calibrated_no_events()).ads[0]

    @pytest.mark.parametrize('stage', ['claude', 'text_pattern'])
    def test_registry_confirmed_is_accepted(self, stage, caplog):
        with caplog.at_level('INFO'):
            ad = self._run(CLOSING, stage=stage, registry=True)
        assert ad['validation']['decision'] == Decision.ACCEPT.value
        assert not ad.get('held_for_review')
        assert ('INFO: Splice veto waived, sponsor confirmed by registry'
                in ad['validation']['flags'])
        assert 'Splice veto waived for 1000.0s-1187.0s: sponsor confirmed by registry' \
            in caplog.text

    @pytest.mark.parametrize('stage', ['claude', 'text_pattern'])
    def test_transcript_confirmed_is_accepted(self, stage):
        ad = self._run(CLOSING, stage=stage, description='Acme')
        assert ad['validation']['decision'] == Decision.ACCEPT.value
        assert ('INFO: Splice veto waived, sponsor confirmed by transcript'
                in ad['validation']['flags'])

    @pytest.mark.parametrize('stage', ['claude', 'text_pattern'])
    def test_reason_only_is_held(self, stage):
        ad = self._run('ordinary conversation about the week', stage=stage,
                       description='Acme', reason='Acme sponsor read')
        assert ad['validation']['decision'] == Decision.REVIEW.value
        assert ad['hold_reason'] == 'no_splice_evidence'

    @pytest.mark.parametrize('stage', ['claude', 'text_pattern'])
    def test_unconfirmed_is_held(self, stage):
        ad = self._run('I use Acme at home, Acme is fine', stage=stage,
                       registry=True)
        assert ad['validation']['decision'] == Decision.REVIEW.value
        assert ad['hold_reason'] == 'no_splice_evidence'
