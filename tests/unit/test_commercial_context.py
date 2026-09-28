"""Host-read closings as commercial language, and the splice-veto sponsor waiver."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_validator import AdValidator

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
    ])
    def test_bare_and_spelled_links_are_commercial(self, text):
        assert _validator(text, registry=False)._has_local_commercial_context(
            _SPAN, 'Acme') is True

    @pytest.mark.parametrize('text', [
        'Thanks to Acme for supporting the show.',
        'Thank you to Acme for sponsoring this episode.',
        'Thanks to Acme, we can keep going.',
        'This one comes from our friends at Acme.',
        'A word from our sponsor at Acme.',
        'Acme is a sponsor of the show.',
        'Acme is our sponsor this week.',
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

    def test_other_domain_is_not_commercial(self):
        v = _validator('Acme came up. Go to othersite.com today.', registry=False)
        assert v._has_local_commercial_context(_SPAN, 'Acme') is False

