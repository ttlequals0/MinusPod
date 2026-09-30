"""Host-read closings as commercial language."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_detector.prompts import _span_names_sponsor
from ad_validator import AdValidator
from sponsor_context import framed_sponsor_names, local_commercial_context, names_vanity_link
from tests.unit.marker_test_utils import ACME_REGISTRY, CLOSING, RegistryStub, registry_confirms


def _validator(text, registry=True):
    segments = [{'start': 0.0, 'end': 400.0, 'text': text}]
    return AdValidator(3600.0, segments, episode_description='',
                       sponsor_service=ACME_REGISTRY if registry else None)


_SPAN = {'start': 0.0, 'end': 400.0}


def _commercial(v, sponsor='Acme'):
    return local_commercial_context(
        v._bounded_text_segments(_SPAN), sponsor, names_sponsor=v._names_sponsor,
        matches_expected=v._matches_expected_sponsor)


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


class CountingRegistry(RegistryStub):
    """Acme registry stub that counts full-registry scans and row reads."""

    def __init__(self):
        super().__init__({'Acme': ('acme',)})
        self.calls = {'get_sponsors': 0, 'brand_mention_offsets': 0}

    def get_sponsors(self):
        self.calls['get_sponsors'] += 1
        return [{'name': 'Acme', 'aliases': '["Acme Corp"]'}, {'name': 'Other', 'aliases': '[]'}]

    def brand_mention_offsets(self, text):
        self.calls['brand_mention_offsets'] += 1
        return super().brand_mention_offsets(text)

    def mentions_brand(self, text, name):
        return name in super().brand_mention_offsets(text)


READ_SEGMENTS = [
    {'start': 0.0, 'end': 150.0, 'text': 'Acme makes the tools we use every day.'},
    {'start': 150.0, 'end': 300.0, 'text': 'Thanks to Acme Corp for sponsoring. Acme is great.'},
    {'start': 300.0, 'end': 400.0, 'text': 'Thanks to Acme Corp for supporting the show.'},
]


def test_validate_reads_the_registry_rows_once_and_scans_the_span_once():
    registry = CountingRegistry()
    validator = AdValidator(3600.0, READ_SEGMENTS, episode_description='',
                            min_cut_confidence=0.80, sponsor_service=registry)
    ad = {'start': 0.0, 'end': 400.0, 'confidence': 0.9, 'sponsor': 'Acme Corp',
          'reason': 'sponsor read', 'detection_stage': 'claude'}
    assert validator.validate([ad]).ads[0]['validation']['decision'] == 'ACCEPT'
    assert registry.calls == {'get_sponsors': 1, 'brand_mention_offsets': 1}


def test_detector_span_gate_scans_the_registry_once():
    registry = CountingRegistry()
    segments = [{'start': 0.0, 'end': 200.0, 'text': 'Acme makes the tools we use.'},
                {'start': 200.0, 'end': 400.0, 'text': 'This show is sponsored by Acme.'}]
    assert _span_names_sponsor(segments, 0.0, 400.0, None, registry) is True
    assert registry.calls['brand_mention_offsets'] == 1


def test_a_conversational_brand_named_more_does_not_hide_the_advertised_one():
    registry = RegistryStub({'Calm': ('calm',), 'Acme': ('acme',)})
    segments = [
        {'start': 0.0, 'end': 200.0, 'text': 'I stayed calm, you know. ' * 12},
        {'start': 200.0, 'end': 400.0,
         'text': 'Acme is the easiest way to protect your home. ' * 8 + 'Learn more at acme.com.'},
    ]
    validator = AdValidator(3600.0, segments, episode_description='', sponsor_service=registry)
    assert registry_confirms(validator, dict(_SPAN)) is True
    assert _span_names_sponsor(segments, 0.0, 400.0, None, registry) is True


@pytest.mark.parametrize('text', [
    'See what it looks like at acme.com slash show',
    'Go to acme.com/show today',
    'That is acme dot com slash show',
    'A-C-M-E.com/show',
])
def test_vanity_link_on_the_sponsor_domain(text):
    assert names_vanity_link([text], 'Acme') is True


@pytest.mark.parametrize('text', [
    'I tried acme.com last week',
    'See initech.com slash show',
    'acme and then slash show',
])
def test_no_vanity_link_without_a_path_on_the_sponsor_domain(text):
    assert names_vanity_link([text], 'Acme') is False


@pytest.mark.parametrize('text,names', [
    ('Based on transcript: This episode of the show is brought to you by Acme. A-C-M-E.', ['Acme']),
    ('Sponsored by Acme Home, the alarm people', ['Acme Home']),
    ('Thanks to Acme for supporting the show', ['Acme']),
    ('Brought to you by the folks at Acme', []),
    ('No framing here, just Acme', []),
    ('', []),
])
def test_framed_sponsor_names(text, names):
    assert framed_sponsor_names(text) == names
