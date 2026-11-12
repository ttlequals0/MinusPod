"""AudioFingerprinter.find_matches: scope, settings, timeout scaling, cancel, deadline, fallback."""
import json
import random
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import audio_fingerprinter as af
from audio_fingerprinter import AudioFingerprint, AudioFingerprinter, FingerprintMatch
from config import (
    FINGERPRINT_MATCH_STRICTNESS, FINGERPRINT_MIN_COVERAGE, FINGERPRINT_SETTING_RANGES, FINGERPRINT_TIMEOUT_FACTOR,
    FPCALC_TIMEOUT_FULL,
)
from ad_detector import AdDetector
from utils.pattern_scope import pattern_passes_scope

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


def test_fallback_never_accepts_a_window_below_the_strictness_floor(world, monkeypatch):
    fp = world.fp
    world.db.set_setting('fingerprint_match_strictness', '0.5')
    monkeypatch.setattr(fp, '_generate_full_fingerprint', lambda *a, **k: None)
    monkeypatch.setattr(fp, '_get_audio_duration', lambda p: 40.0)
    monkeypatch.setattr(fp, 'generate_fingerprint',
                        lambda path, start, duration: AudioFingerprint('win', duration))
    monkeypatch.setattr(fp, 'compare_fingerprints', lambda chunk, known: 0.6)
    assert fp.find_matches('x.mp3', podcast_id='showA') == []
    floor = FINGERPRINT_SETTING_RANGES['fingerprint_match_strictness'][1]
    monkeypatch.setattr(fp, 'compare_fingerprints', lambda chunk, known: floor)
    assert fp.find_matches('x.mp3', podcast_id='showA')


def test_fallback_match_reports_only_the_compared_window_as_measured(world, monkeypatch):
    fp = world.fp
    monkeypatch.setattr(fp, '_generate_full_fingerprint', lambda *a, **k: None)
    monkeypatch.setattr(fp, '_get_audio_duration', lambda p: 44.0)
    monkeypatch.setattr(fp, 'generate_fingerprint',
                        lambda path, start, duration: AudioFingerprint('win', duration))
    monkeypatch.setattr(fp, 'compare_fingerprints',
                        lambda chunk, known: 0.9 if known == 'LONG' else 0.1)
    out = fp.find_matches('x.mp3', known_fingerprints=[(7, 'LONG', 40.0, None, None)])
    assert [(m.start, m.end, m.measured_end) for m in out] == [(0.0, 40.0, 10.0)]
    assert out[0].coverage == 0.25
    detector = AdDetector(api_key='test-key')
    all_ads, regions = [], []
    detector._add_pattern_match(out[0], 'fingerprint', 'fingerprint', all_ads, regions, 'ep1')
    assert (all_ads[0]['start'], all_ads[0]['end']) == (0.0, 40.0)
    assert all_ads[0]['fingerprint_match_end'] == 10.0
    assert all_ads[0]['fingerprint_coverage'] == 0.25


def test_fallback_merge_keeps_only_contiguous_measured_spans(world):
    fp = world.fp
    a = FingerprintMatch(1, 0.0, 30.0, 0.9, similarity=0.9, coverage=1 / 3, measured_end=10.0)
    b = FingerprintMatch(2, 30.0, 60.0, 0.8, similarity=0.8, coverage=1 / 3, measured_end=40.0)
    c = FingerprintMatch(3, 5.0, 15.0, 0.8, similarity=0.8, coverage=1.0, measured_end=15.0)
    merged = fp._merge_overlapping_matches([a, b])
    assert [(m.start, m.end, m.measured_end, m.coverage) for m in merged] == [(0.0, 60.0, 10.0, 1 / 3)]
    merged = fp._merge_overlapping_matches([a, c])
    assert [(m.start, m.end, m.measured_end) for m in merged] == [(0.0, 30.0, 15.0)]


def test_successful_scan_logs_one_info_line(world, caplog):
    with caplog.at_level('INFO', logger='podcast.fingerprint'):
        out = world.fp.find_matches('x.mp3', podcast_id='showA')
    infos = [r.getMessage() for r in caplog.records
             if r.name == 'podcast.fingerprint' and r.levelname == 'INFO']
    assert len(out) == 2
    assert len(infos) == 1
    assert '2 patterns in scope' in infos[0] and '2 matches' in infos[0]
    assert 'candidates measured' in infos[0]


def _community_pattern_with_tags(world, tags, active):
    sid = world.db.create_known_sponsor(f'Acme {active}', tags=tags)
    if not active:
        world.db.update_known_sponsor(sid, is_active=0)
    pid = world.db.create_ad_pattern(scope='global', text_template='read ' * 12,
                                     sponsor_id=sid, source='community')
    world.db.create_audio_fingerprint(pid, b'FPA', 10.0)
    return pid, sid


def test_scope_uses_tags_of_active_sponsors_only(world):
    active_pid, _ = _community_pattern_with_tags(world, ['sports'], active=True)
    rows = world.fp._in_scope_fingerprints('showC', None, {'tech'})
    assert active_pid not in [r[0] for r in rows]
    inactive_pid, _ = _community_pattern_with_tags(world, ['sports'], active=False)
    rows = world.fp._in_scope_fingerprints('showC', None, {'tech'})
    assert inactive_pid in [r[0] for r in rows]


def _scope_reference(world, podcast_id, network_id, podcast_tags):
    """Scope filtering as a separate sponsor-tag lookup computes it."""
    tags_map = {sid: set(tags) for sid, tags in world.db.get_sponsor_tags_map().items()}
    return [
        (r['pattern_id'], r['fingerprint'], r['duration'], r['sponsor'], r.get('category'))
        for r in world.fp._load_fingerprints_from_db()
        if pattern_passes_scope(
            r['scope'], r['network_id'], r['podcast_id'], r['source'],
            tags_map.get(r['sponsor_id'], set()),
            podcast_id=podcast_id, network_id=network_id, podcast_tag_set=set(podcast_tags or ()))
    ]


def test_scope_matches_a_separate_sponsor_tag_lookup(world):
    conn = world.db.get_connection()
    cases = [(['sports'], True), (['sports'], False), (['tech', 'news'], True),
             ('"sports"', True), ('', True), (['tech'], False)]
    for i, (tags, active) in enumerate(cases):
        sid = world.db.create_known_sponsor(f'Acme {i}')
        world.db.update_known_sponsor(sid, is_active=int(active))
        raw = tags if isinstance(tags, str) else json.dumps(tags)
        conn.execute("UPDATE known_sponsors SET tags = ? WHERE id = ?", (raw, sid))
        pid = world.db.create_ad_pattern(scope='global', text_template='read ' * 12,
                                         sponsor_id=sid, source='community')
        world.db.create_audio_fingerprint(pid, b'FPA', 10.0)
    conn.commit()
    for podcast_id in ('showA', 'showB', 'showC'):
        for network_id in (None, 'net1'):
            for podcast_tags in (None, {'tech'}, {'sports'}, {'tech', 'news', 'sports'}):
                assert (world.fp._in_scope_fingerprints(podcast_id, network_id, podcast_tags)
                        == _scope_reference(world, podcast_id, network_id, podcast_tags))


def test_scope_ignores_tags_that_are_not_a_list(world):
    pid, sid = _community_pattern_with_tags(world, ['sports'], active=True)
    conn = world.db.get_connection()
    conn.execute("UPDATE known_sponsors SET tags = ? WHERE id = ?", ('"sports"', sid))
    conn.commit()
    rows = world.fp._in_scope_fingerprints('showC', None, {'tech'})
    assert pid in [r[0] for r in rows]


@pytest.mark.parametrize('key,raw,expected', [
    ('fingerprint_match_strictness', '0.1', 0.65),
    ('fingerprint_match_strictness', '0.5', 0.65),
    ('fingerprint_match_strictness', '1.5', 0.95),
    ('fingerprint_match_strictness', 'nan', FINGERPRINT_MATCH_STRICTNESS),
    ('fingerprint_match_strictness', 'abc', FINGERPRINT_MATCH_STRICTNESS),
    ('fingerprint_min_coverage', '0', 0.10),
    ('fingerprint_min_coverage', '2', 1.00),
    ('fingerprint_min_coverage', 'inf', FINGERPRINT_MIN_COVERAGE),
    ('fingerprint_gap_tolerance_s', '-3', 0.0),
    ('fingerprint_gap_tolerance_s', '99', 15.0),
    ('fingerprint_gap_tolerance_s', '7.5', 7.5),
])
def test_matching_settings_are_clamped(world, key, raw, expected):
    world.db.set_setting(key, raw)
    names = ('fingerprint_match_strictness', 'fingerprint_min_coverage', 'fingerprint_gap_tolerance_s')
    assert world.fp._matching_settings()[names.index(key)] == expected


@pytest.mark.parametrize('raw,expected', [('12', 12.0), ('99', 30.0), ('abc', 5.0)])
def test_scan_gets_the_clamped_minimum_ad_length(world, monkeypatch, raw, expected):
    seen = {}

    def scan(*args, **kwargs):
        seen.update(kwargs)
        return [], 0
    monkeypatch.setattr(af, '_scan_fingerprints', scan)
    world.db.set_setting('fingerprint_min_ad_length_s', raw)
    world.fp.find_matches('x.mp3', podcast_id='showA')
    assert seen['min_ad_length_s'] == expected


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
