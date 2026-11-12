"""AudioFingerprinter.find_matches: scope, settings, timeout scaling, cancel, deadline, fallback."""
import random
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import audio_fingerprinter as af
from audio_fingerprinter import AudioFingerprint, AudioFingerprinter, FingerprintMatch
from config import FINGERPRINT_TIMEOUT_FACTOR, FPCALC_TIMEOUT_FULL
from ad_detector import AdDetector

IPS = 8.0
EP_SECONDS = 300.0


def _rand_ints(rng, n):
    return [rng.getrandbits(32) - (1 << 31) for _ in range(n)]


@pytest.fixture
def world(temp_db, monkeypatch):
    rng = random.Random(42)
    ep = _rand_ints(rng, int(EP_SECONDS * IPS))
    pats = {}

    def add(name, planted_at, **pattern_kwargs):
        ints = _rand_ints(rng, 80)
        ep[int(planted_at * IPS):int(planted_at * IPS) + 80] = ints
        pid = temp_db.create_ad_pattern(text_template='read ' * 12, **pattern_kwargs)
        temp_db.create_audio_fingerprint(pid, name.encode(), 10.0)
        pats[name.encode()] = ints
        return pid

    ids = SimpleNamespace(
        a=add('FPA', 50.0, scope='podcast', podcast_id='showA'),
        b=add('FPB', 120.0, scope='podcast', podcast_id='showB'),
        g=add('FPG', 200.0, scope='global'),
        n=add('FPN', 250.0, scope='network', network_id='net1'),
    )
    def decode(b):
        return pats[bytes(b)], 1
    monkeypatch.setattr(af, 'acoustid', SimpleNamespace(
        chromaprint=SimpleNamespace(decode_fingerprint=decode)))
    fp = AudioFingerprinter.__new__(AudioFingerprinter)
    fp.db = temp_db
    fp._fpcalc_path = '/usr/bin/fpcalc'
    seen = {}

    def full(path, timeout=FPCALC_TIMEOUT_FULL):
        seen['timeout'] = timeout
        return ep, EP_SECONDS
    monkeypatch.setattr(fp, 'is_available', lambda: True)
    monkeypatch.setattr(fp, '_generate_full_fingerprint', full)
    monkeypatch.setattr(fp, '_get_audio_duration', lambda p: EP_SECONDS)
    return SimpleNamespace(fp=fp, ids=ids, seen=seen, db=temp_db, ep=ep, pats=pats)


def _pids(matches):
    return sorted(m.pattern_id for m in matches)


def test_scope_filters_out_of_scope_patterns(world):
    out = world.fp.find_matches('x.mp3', podcast_id='showA', network_id=None, podcast_tags=set())
    assert _pids(out) == sorted([world.ids.a, world.ids.g])


def test_network_scope_requires_matching_network(world):
    out = world.fp.find_matches('x.mp3', podcast_id='showB', network_id='net1', podcast_tags=set())
    assert _pids(out) == sorted([world.ids.b, world.ids.g, world.ids.n])


def test_matches_carry_measured_fields(world):
    out = world.fp.find_matches('x.mp3', podcast_id='showA')
    by_id = {m.pattern_id: m for m in out}
    m = by_id[world.ids.a]
    assert m.similarity == m.confidence >= 0.99
    assert m.coverage >= 0.99
    assert abs(m.start - 50.0) < 0.3 and abs(m.end - 60.0) < 0.3


def test_disabled_setting_returns_empty(world):
    world.db.set_setting('fingerprint_enabled', 'false')
    assert world.fp.find_matches('x.mp3', podcast_id='showA') == []
    assert 'timeout' not in world.seen


def test_disabled_pattern_is_not_searched(world):
    world.db.set_fingerprint_enabled(world.ids.g, False)
    out = world.fp.find_matches('x.mp3', podcast_id='showA')
    assert _pids(out) == [world.ids.a]


def test_strictness_and_coverage_settings_are_read(world):
    world.db.set_setting('fingerprint_match_strictness', '0.95')
    rng = random.Random(1)
    start = int(50.0 * IPS)
    for j in range(80):
        for _ in range(3):
            world.ep[start + j] ^= 1 << rng.randrange(32)
    out = world.fp.find_matches('x.mp3', podcast_id='showA')
    assert world.ids.a not in _pids(out) and world.ids.g in _pids(out)
    world.db.set_setting('fingerprint_match_strictness', '0.65')
    out = world.fp.find_matches('x.mp3', podcast_id='showA')
    assert world.ids.a in _pids(out)


def test_full_fingerprint_timeout_scales_with_duration(world):
    fp = world.fp
    assert fp._full_fingerprint_timeout(60.0) == FPCALC_TIMEOUT_FULL
    two_hours = fp._full_fingerprint_timeout(7200.0)
    assert two_hours >= 120
    scaled = int(7200 * FINGERPRINT_TIMEOUT_FACTOR)
    if scaled > FPCALC_TIMEOUT_FULL:
        assert two_hours == scaled
    assert fp._full_fingerprint_timeout(7200.0 * 10) == int(72000 * FINGERPRINT_TIMEOUT_FACTOR)


def test_find_matches_passes_scaled_timeout_to_fingerprinting(world, monkeypatch):
    monkeypatch.setattr(world.fp, '_get_audio_duration', lambda p: 100000.0)
    world.fp.find_matches('x.mp3', podcast_id='showA')
    assert world.seen['timeout'] == world.fp._full_fingerprint_timeout(100000.0)
    assert world.seen['timeout'] > FPCALC_TIMEOUT_FULL


def test_cancel_mid_scan_returns_what_it_has(world):
    class FlipAfterFirst:
        def __init__(self):
            self.calls = 0

        def is_set(self):
            self.calls += 1
            return self.calls > 1

    ev = FlipAfterFirst()
    out = world.fp.find_matches('x.mp3', podcast_id='showA', cancel_event=ev)
    assert len(out) == 1
    assert ev.calls == 2


def test_deadline_stops_scan_with_injected_clock(world, monkeypatch):
    now = {'t': 0.0}

    def monotonic():
        now['t'] += 400.0
        return now['t']
    monkeypatch.setattr(af, 'time', SimpleNamespace(
        monotonic=monotonic, time=lambda: now['t'], sleep=lambda s: None))
    out = world.fp.find_matches('x.mp3', podcast_id='showA', timeout=600)
    assert len(out) == 1


def test_whole_file_failure_falls_back_to_per_window_scan(world, monkeypatch):
    fp = world.fp
    monkeypatch.setattr(fp, '_generate_full_fingerprint', lambda *a, **k: None)
    monkeypatch.setattr(fp, '_get_audio_duration', lambda p: 40.0)
    monkeypatch.setattr(fp, 'generate_fingerprint',
                        lambda path, start, duration: AudioFingerprint('win', duration))
    monkeypatch.setattr(fp, 'compare_fingerprints',
                        lambda chunk, known: 0.9 if known == 'FPG' else 0.1)
    out = fp.find_matches('x.mp3', podcast_id='showA')
    assert out
    assert all(isinstance(m, FingerprintMatch) for m in out)
    assert all(m.pattern_id == world.ids.g for m in out)
    assert all(m.similarity == m.confidence == 0.9 and m.coverage == 1.0 for m in out)


def test_corrupt_stored_fingerprint_is_skipped(world, caplog):
    pid = world.db.create_ad_pattern(scope='global', text_template='read ' * 12)
    world.db.create_audio_fingerprint(pid, b'EMPTY', 5.0)
    world.pats[b'EMPTY'] = []
    with caplog.at_level('DEBUG', logger='podcast.fingerprint'):
        out = world.fp.find_matches('x.mp3', podcast_id='showA')
    assert world.ids.a in _pids(out)
    assert pid not in _pids(out)
    skipped = [r for r in caplog.records
               if r.levelname == 'DEBUG' and str(pid) in r.getMessage() and 'kipping' in r.getMessage()]
    assert len(skipped) == 1


def test_load_returns_scope_fields(world):
    rows = world.fp._load_fingerprints_from_db()
    row = next(r for r in rows if r['pattern_id'] == world.ids.n)
    assert (row['scope'], row['network_id']) == ('network', 'net1')


def test_detector_call_site_passes_text_matcher_scope_values():
    calls = {}

    class Fp:
        def is_available(self):
            return True

        def find_matches(self, audio_path, **kwargs):
            calls.update(kwargs)
            return []

    detector = AdDetector(api_key='test-key')
    detector.audio_fingerprinter = Fp()
    detector.text_pattern_matcher = None
    detector.pattern_service = None
    detector.db = None
    segments = [{'start': 0.0, 'end': 10.0, 'text': 'show talk'}]
    with patch.object(detector, 'initialize_client'), \
         patch.object(detector, 'detect_ads', return_value={'ads': [], 'status': 'success'}):
        detector.process_transcript(
            segments, 'Example Podcast', 'Episode One', slug='example-podcast',
            episode_id='a1b2c3d4e5f6', podcast_id='example-podcast', skip_patterns=False,
            audio_path='x.mp3', dai_differential=None, keep_content=False,
            podcast_tags={'tech'})
    assert calls['podcast_id'] == 'example-podcast'
    assert calls['podcast_tags'] == {'tech'}
    assert calls.get('network_id') is None


def test_add_pattern_match_records_similarity_and_coverage():
    detector = AdDetector(api_key='test-key')
    all_ads, regions = [], []
    match = FingerprintMatch(pattern_id=3, start=10.0, end=40.0, confidence=0.8,
                             sponsor=None, category=None, similarity=0.8, coverage=0.9)
    detector._add_pattern_match(match, 'fingerprint', 'fingerprint', all_ads, regions, 'ep1')
    assert all_ads[0]['fingerprint_similarity'] == 0.8
    assert all_ads[0]['fingerprint_coverage'] == 0.9
    assert all_ads[0]['fingerprint_match_start'] == 10.0
    assert all_ads[0]['fingerprint_match_end'] == 40.0
