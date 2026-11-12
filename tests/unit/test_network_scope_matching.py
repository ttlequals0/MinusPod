"""Network-scope patterns reach feeds of the same effective network, through both matchers."""
import random
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import audio_fingerprinter as af
from ad_detector import AdDetector
from audio_fingerprinter import AudioFingerprinter
from text_pattern_matcher import TextPatternMatcher

IPS = 8.0
EP_SECONDS = 300.0
INTRO = 'this episode is brought to you by acme today'
TRANSCRIPT = (INTRO + ' visit acme dot com for the current offer '
              'and use code podcast to save on your order')
SEGMENTS = [{'start': 0.0, 'end': 30.0, 'text': TRANSCRIPT}]


def _feed(db, slug, network=None, override=None):
    db.create_podcast(slug, f'https://example.com/{slug}.xml', slug)
    fields = {k: v for k, v in (('network_id', network), ('network_id_override', override)) if v}
    if fields:
        db.update_podcast(slug, **fields)


@pytest.fixture
def feeds(temp_db):
    _feed(temp_db, 'same-net', network='net1')
    _feed(temp_db, 'other-net', network='net2')
    _feed(temp_db, 'no-net')
    _feed(temp_db, 'overridden', network='net2', override='net1')
    _feed(temp_db, 'blank-override', network='net1', override='  ')
    return temp_db


@pytest.mark.parametrize('slug,network', [
    ('same-net', 'net1'), ('other-net', 'net2'), ('no-net', None),
    ('overridden', 'net1'), ('blank-override', 'net1'), ('missing', None)])
def test_effective_network_id(feeds, slug, network):
    assert feeds.get_effective_network_id(slug) == network


def _detect(detector, slug, **kw):
    with patch.object(detector, 'initialize_client'), \
         patch.object(detector, 'detect_ads', return_value={'ads': [], 'status': 'success'}):
        result = detector.process_transcript(
            SEGMENTS, 'Example Podcast', 'Episode One', slug=slug, episode_id='a1b2c3d4e5f6',
            podcast_id=slug, skip_patterns=False, dai_differential=None,
            keep_content=False, **kw)
    return result


def _text_hits(db, slug):
    detector = AdDetector(api_key='test-key')
    detector.db = db
    detector.audio_fingerprinter = None
    detector.text_pattern_matcher = TextPatternMatcher(db=db)
    return {ad.get('pattern_id') for ad in _detect(detector, slug)['ads']}


def _text_pattern(db, **kw):
    return db.create_ad_pattern(text_template=TRANSCRIPT, intro_variants=[INTRO],
                                duration=30.0, **kw)


@pytest.mark.parametrize('slug,hit', [
    ('same-net', True), ('overridden', True), ('blank-override', True),
    ('other-net', False), ('no-net', False)])
def test_network_text_pattern_follows_the_effective_network(feeds, slug, hit):
    pid = _text_pattern(feeds, scope='network', network_id='net1')
    assert (pid in _text_hits(feeds, slug)) is hit


def test_global_text_pattern_matches_every_feed(feeds):
    g = _text_pattern(feeds, scope='global')
    assert all(g in _text_hits(feeds, slug) for slug in ('no-net', 'other-net', 'same-net'))


def test_podcast_text_pattern_matches_only_its_feed(feeds):
    p = _text_pattern(feeds, scope='podcast', podcast_id='same-net')
    assert p in _text_hits(feeds, 'same-net')
    assert p not in _text_hits(feeds, 'overridden')


@pytest.fixture
def audio(feeds, monkeypatch):
    rng = random.Random(7)
    ep = [rng.getrandbits(32) - (1 << 31) for _ in range(int(EP_SECONDS * IPS))]
    ints = [rng.getrandbits(32) - (1 << 31) for _ in range(80)]
    ep[400:480] = ints
    pid = feeds.create_ad_pattern(text_template='read ' * 12, scope='network', network_id='net1')
    feeds.create_audio_fingerprint(pid, b'FPN', 10.0)
    monkeypatch.setattr(af, 'acoustid', SimpleNamespace(
        chromaprint=SimpleNamespace(decode_fingerprint=lambda b: (ints, 1))))
    fp = AudioFingerprinter.__new__(AudioFingerprinter)
    fp.db = feeds
    fp._fpcalc_path = '/usr/bin/fpcalc'
    monkeypatch.setattr(fp, 'is_available', lambda: True)
    monkeypatch.setattr(fp, '_generate_full_fingerprint', lambda path, timeout=0, quiet=False: (ep, EP_SECONDS))
    monkeypatch.setattr(fp, '_get_audio_duration', lambda p: EP_SECONDS)
    detector = AdDetector(api_key='test-key')
    detector.db = feeds
    detector.text_pattern_matcher = None
    detector.audio_fingerprinter = fp
    return SimpleNamespace(detector=detector, pid=pid)


@pytest.mark.parametrize('slug,hit', [
    ('same-net', True), ('overridden', True), ('other-net', False), ('no-net', False)])
def test_network_audio_pattern_follows_the_effective_network(audio, slug, hit):
    ads = _detect(audio.detector, slug, audio_path='x.mp3')['ads']
    assert (audio.pid in {ad.get('pattern_id') for ad in ads}) is hit


def test_both_matchers_receive_the_same_network(feeds):
    seen = {}

    class Fp:
        def is_available(self):
            return True

        def find_matches(self, audio_path, **kw):
            seen['audio'] = kw.get('network_id')
            return []

    class Tx:
        def find_matches(self, segments, **kw):
            seen['text'] = kw.get('network_id')
            return []

    detector = AdDetector(api_key='test-key')
    detector.db = feeds
    detector.audio_fingerprinter = Fp()
    detector.text_pattern_matcher = Tx()
    _detect(detector, 'overridden', audio_path='x.mp3')
    assert seen == {'audio': 'net1', 'text': 'net1'}
