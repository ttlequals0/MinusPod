"""Host-read closings as commercial language."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_detector.prompts import _span_names_sponsor
from ad_validator import AdValidator
from tests.unit.marker_test_utils import RegistryStub, registry_confirms

CLOSING = ("Learn more at acme.com. That's A-C-M-E.com. "
           "Let me thank them so much for supporting the show.")


# Acme wherever the text names it, spelled out or not.
ACME_REGISTRY = RegistryStub({'Acme': ('acme', 'a-c-m-e')})


def _validator(text, registry=True):
    segments = [{'start': 0.0, 'end': 400.0, 'text': text}]
    return AdValidator(3600.0, segments, episode_description='',
                       sponsor_service=ACME_REGISTRY if registry else None)


_SPAN = {'start': 0.0, 'end': 400.0}


def _commercial(v, sponsor='Acme'):
    return v._has_local_commercial_context(v._bounded_text_segments(_SPAN), sponsor)


class TestCommercialContext:
    def test_production_closing_is_commercial(self):
        v = _validator(CLOSING)
        assert _commercial(v) is True
        assert registry_confirms(v, dict(_SPAN)) is True

    @pytest.mark.parametrize('text', [
        'Acme keeps you safe, find it at Acme dot com.',
        'Acme is great. That is A C M E dot com.',
        'Acme is great. Check them out at acme.com.',
        'Acme is great. Find out more at acme.io.',
        "Acme is great. That's A-C-M-E.com.",
    ])
    def test_bare_and_spelled_links_are_commercial(self, text):
        assert _commercial(_validator(text, registry=False)) is True

    @pytest.mark.parametrize('text', [
        'Thanks to Acme for supporting the show.',
        'Thank you to Acme for sponsoring this episode.',
        'Thanks to Acme for sponsoring this episode.',
        'A word from our sponsor at Acme.',
        'Acme is our sponsor.',
        'This week, Acme is our sponsor.',
    ])
    def test_framing_phrases_are_commercial(self, text):
        assert _commercial(_validator(text, registry=False)) is True

    def test_conversational_mention_is_not_commercial(self):
        v = _validator('I use Acme at home, Acme is fine')
        assert _commercial(v) is False
        assert registry_confirms(v, dict(_SPAN)) is False

    def test_thanks_for_listening_is_not_sponsor_framing(self):
        v = _validator('Thank you for listening, Acme was mentioned earlier.',
                       registry=False)
        assert _commercial(v) is False

    @pytest.mark.parametrize('registry', [True, False])
    def test_learn_more_at_another_domain_is_not_commercial(self, registry):
        v = _validator('Learn more at wikipedia.org, Acme did well.',
                       registry=registry)
        assert _commercial(v) is False

    @pytest.mark.parametrize('registry', [True, False])
    def test_thanks_to_someone_else_is_not_sponsor_framing(self, registry):
        v = _validator('Thanks to our listeners, Acme came up again.',
                       registry=registry)
        assert _commercial(v) is False

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
        assert _commercial(v) is False

    def test_other_domain_is_not_commercial(self):
        v = _validator('Acme came up. Go to othersite.com today.', registry=False)
        assert _commercial(v) is False



# Production shapes: a host read naming the brand nine times with a link, and the closing above.
HOST_READ = ' '.join(['Acme is the easiest way to protect your home.'] * 9
                     + ['Learn more at acme.com.'])


@pytest.mark.parametrize(('text', 'expected'), [
    (HOST_READ, True),
    (CLOSING, True),
    ('I use Acme at home, Acme is fine', False),
])
def test_detector_and_validator_registry_gates_agree(text, expected):
    segments = [{'start': 0.0, 'end': 400.0, 'text': text}]
    validator = AdValidator(3600.0, segments, episode_description='',
                            sponsor_service=ACME_REGISTRY)
    assert registry_confirms(validator, dict(_SPAN)) is expected
    assert _span_names_sponsor(segments, 0.0, 400.0, None, ACME_REGISTRY) is expected
