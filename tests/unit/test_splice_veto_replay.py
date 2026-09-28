"""Replay of a host-read ad and of news content through the splice-veto sponsor waiver."""
import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from ad_validator import AdValidator, Decision
from sponsor_service import SponsorService

HOST_READ = [
    "Acme is the easiest way to protect your home.",
    "I have been using Acme for a year now.",
    "With Acme you get cameras, sensors and monitoring.",
    "Acme setup took me about ten minutes.",
    "What I love about Acme is the app.",
    "Acme sends alerts straight to your phone.",
    "My wife loves Acme too.",
    "Learn more at acme.com.",
    "That's A-C-M-E.com.",
    "Let me thank him so much for supporting the show.",
]

CONTENT = ["Amazon reported earnings today.",
           "Amazon.com revenue rose nine percent."]


def _service(*names):
    db = MagicMock()
    db.get_sponsor_normalizations.return_value = []
    db.get_known_sponsors.return_value = [
        {'id': i, 'name': n, 'aliases': '[]', 'tags': '[]', 'is_active': 1}
        for i, n in enumerate(names)]
    return SponsorService(db)


def _segments(lines, start=1000.0, end=1187.0):
    step = (end - start) / len(lines)
    return [{'start': start + i * step, 'end': start + (i + 1) * step, 'text': t}
            for i, t in enumerate(lines)]


def _validate(lines, description='', service=None, sponsor=None):
    v = AdValidator(3600.0, _segments(lines), episode_description=description,
                    sponsor_service=service)
    ad = {'start': 1000.0, 'end': 1187.0, 'confidence': 0.98,
          'reason': 'Host-read ad', 'detection_stage': 'claude'}
    if sponsor:
        ad['sponsor'] = sponsor
    evidence = {'splice_evidence': {'version': 1, 'events': [],
                                    'calibration': {'status': 'calibrated'}}}
    return v.validate([ad], audio_analysis=evidence).ads[0]


def test_host_read_is_confirmed_by_registry():
    ad = _validate(HOST_READ, service=_service('Acme', 'Amazon'), sponsor='Acme')
    assert ad['validation']['decision'] == Decision.ACCEPT.value
    assert not ad.get('held_for_review')
    assert ('INFO: Splice veto waived, sponsor confirmed by registry'
            in ad['validation']['flags'])


def test_host_read_is_confirmed_by_description():
    description = 'This episode is sponsored by <a href="https://acme.com/show">Acme</a>.'
    ad = _validate(HOST_READ, description=description)
    assert ad['validation']['decision'] == Decision.ACCEPT.value
    assert ('INFO: Splice veto waived, sponsor confirmed by transcript'
            in ad['validation']['flags'])


def test_content_naming_a_registry_brand_is_held():
    ad = _validate(CONTENT + ['show talk'] * 8, service=_service('Amazon'))
    assert ad['validation']['decision'] == Decision.REVIEW.value
    assert ad['hold_reason'] == 'no_splice_evidence'


def test_conversational_registry_brand_without_a_closing_is_held():
    ad = _validate(HOST_READ[:7], service=_service('Acme'), sponsor='Acme')
    assert ad['hold_reason'] == 'no_splice_evidence'
