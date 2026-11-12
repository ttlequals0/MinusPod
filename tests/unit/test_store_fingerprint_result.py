"""store_fingerprint reports why nothing was stored and never raises."""
import logging
import random

import acoustid
import pytest

import audio_fingerprinter as af
from audio_fingerprinter import AudioFingerprint, AudioFingerprinter, FingerprintStoreResult
from config import FINGERPRINT_MIN_AD_LENGTH_S


@pytest.fixture
def pattern(temp_db):
    return temp_db.create_ad_pattern(scope='podcast', podcast_id='example-podcast',
                                     text_template='acme read ' * 12)


@pytest.fixture
def audio(tmp_path):
    path = tmp_path / 'original.wav'
    path.write_bytes(b'RIFF')
    return str(path)


@pytest.fixture
def fp(temp_db, monkeypatch):
    fingerprinter = AudioFingerprinter.__new__(AudioFingerprinter)
    fingerprinter.db = temp_db
    fingerprinter._fpcalc_path = '/usr/bin/fpcalc'
    calls = []

    def generate(path, start=0, duration=None):
        calls.append((path, start, duration))
        return AudioFingerprint(fingerprint='AQAAfake', duration=duration)
    monkeypatch.setattr(fingerprinter, 'generate_fingerprint', generate)
    fingerprinter.calls = calls
    return fingerprinter


def test_stored_result_and_source_columns(fp, temp_db, pattern, audio):
    result = fp.store_fingerprint(pattern, audio, 10.0, 40.0,
                                  source_podcast_slug='example-podcast',
                                  source_episode_id='a1b2c3d4e5f6')
    assert result == FingerprintStoreResult(stored=True, skipped_reason=None)
    row = temp_db.get_audio_fingerprint(pattern)
    assert (row['duration'], row['source_podcast_slug'], row['source_episode_id'],
            row['source_start'], row['source_end']) == (30.0, 'example-podcast', 'a1b2c3d4e5f6', 10.0, 40.0)
    assert fp.calls == [(audio, 10.0, 30.0)]


def test_short_segment_reason(fp, pattern, audio):
    result = fp.store_fingerprint(pattern, audio, 0.0, FINGERPRINT_MIN_AD_LENGTH_S - 0.1)
    assert result == FingerprintStoreResult(False, 'segment_too_short')
    assert fp.calls == []


def test_minimum_length_comes_from_the_setting(fp, temp_db, pattern, audio):
    temp_db.set_setting('fingerprint_min_ad_length_s', '12')
    assert fp.store_fingerprint(pattern, audio, 0.0, 10.0).skipped_reason == 'segment_too_short'
    assert fp.store_fingerprint(pattern, audio, 0.0, 12.0).stored is True


@pytest.mark.parametrize('path', [None, '', 'missing.wav'])
def test_original_not_retained_reason(fp, pattern, path, tmp_path):
    given = str(tmp_path / path) if path else path
    assert fp.store_fingerprint(pattern, given, 0.0, 30.0) == \
        FingerprintStoreResult(False, 'original_not_retained')


def test_audio_removed_blocks_unless_forced(fp, temp_db, pattern, audio):
    temp_db.set_pattern_audio_removed(pattern, True)
    assert fp.store_fingerprint(pattern, audio, 0.0, 30.0).skipped_reason == 'audio_removed'
    assert temp_db.get_audio_fingerprint(pattern) is None
    assert fp.store_fingerprint(pattern, audio, 0.0, 30.0, force=True).stored is True
    assert temp_db.get_audio_fingerprint(pattern) is not None


def test_generation_failure_reason(fp, pattern, audio, monkeypatch):
    monkeypatch.setattr(fp, 'generate_fingerprint', lambda *a, **k: None)
    assert fp.store_fingerprint(pattern, audio, 0.0, 30.0) == \
        FingerprintStoreResult(False, 'fingerprint_failed')


def test_empty_fingerprint_reason(fp, pattern, audio, monkeypatch):
    monkeypatch.setattr(fp, 'generate_fingerprint',
                        lambda *a, **k: AudioFingerprint(fingerprint='', duration=30.0))
    assert fp.store_fingerprint(pattern, audio, 0.0, 30.0).skipped_reason == 'fingerprint_failed'


def test_fpcalc_missing_is_a_failure(fp, pattern, audio):
    fp._fpcalc_path = None
    assert fp.store_fingerprint(pattern, audio, 0.0, 30.0).skipped_reason == 'fingerprint_failed'


def test_database_error_never_raises(fp, temp_db, pattern, audio, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError('disk full')
    monkeypatch.setattr(temp_db, 'create_audio_fingerprint', boom)
    assert fp.store_fingerprint(pattern, audio, 0.0, 30.0) == \
        FingerprintStoreResult(False, 'fingerprint_failed')


def test_unexpected_error_never_raises(fp, pattern, audio, monkeypatch):
    def boom(*a, **k):
        raise ValueError('bad audio')
    monkeypatch.setattr(fp, 'generate_fingerprint', boom)
    assert fp.store_fingerprint(pattern, audio, 0.0, 30.0).skipped_reason == 'fingerprint_failed'


def test_rebuild_keeps_an_off_fingerprint_off(fp, temp_db, pattern, audio):
    fp.store_fingerprint(pattern, audio, 0.0, 30.0)
    temp_db.set_fingerprint_enabled(pattern, False)
    assert fp.store_fingerprint(pattern, audio, 5.0, 40.0, force=True).stored is True
    row = temp_db.get_audio_fingerprint(pattern)
    assert row['enabled'] == 0 and row['source_start'] == 5.0


def test_a_failed_store_leaves_the_existing_fingerprint(fp, temp_db, pattern, audio, monkeypatch):
    fp.store_fingerprint(pattern, audio, 0.0, 30.0)
    before = temp_db.get_audio_fingerprint(pattern)['fingerprint']
    monkeypatch.setattr(fp, 'generate_fingerprint', lambda *a, **k: None)
    fp.store_fingerprint(pattern, audio, 0.0, 30.0, force=True)
    assert temp_db.get_audio_fingerprint(pattern)['fingerprint'] == before


def test_module_exposes_the_result_type():
    assert af.FingerprintStoreResult is FingerprintStoreResult


class _Matcher:
    def __init__(self, pieces):
        self.pieces = pieces

    def create_patterns_from_ad(self, **kwargs):
        return self.pieces


def _detector(temp_db, fp, pieces):
    from ad_detector import AdDetector
    detector = AdDetector(api_key='test-key')
    detector.db = temp_db
    detector.audio_fingerprinter = fp
    detector.text_pattern_matcher = _Matcher(pieces)
    return detector


def test_automatic_creation_stores_with_source_and_respects_audio_removed(fp, temp_db, audio):
    kept = temp_db.create_ad_pattern(scope='podcast', podcast_id='example-podcast',
                                     text_template='kept read ' * 12)
    removed = temp_db.create_ad_pattern(scope='podcast', podcast_id='example-podcast',
                                        text_template='removed read ' * 12)
    temp_db.set_pattern_audio_removed(removed, True)
    pieces = [{'id': kept, 'start': 10.0, 'end': 40.0}, {'id': removed, 'start': 50.0, 'end': 80.0}]
    detector = _detector(temp_db, fp, pieces)
    ad = {'start': 10.0, 'end': 80.0, 'sponsor': 'Acme'}

    assert detector._create_pattern_and_fingerprint(
        ad, [], 'Acme', 'example-podcast', 'a1b2c3d4e5f6', audio) is True

    row = temp_db.get_audio_fingerprint(kept)
    assert (row['source_podcast_slug'], row['source_episode_id'], row['source_start'],
            row['source_end']) == ('example-podcast', 'a1b2c3d4e5f6', 10.0, 40.0)
    assert temp_db.get_audio_fingerprint(removed) is None


def test_short_span_is_reported_even_without_fpcalc(fp, pattern, audio):
    fp._fpcalc_path = None
    assert fp.store_fingerprint(pattern, audio, 0.0, 1.0).skipped_reason == 'segment_too_short'
    assert fp.store_fingerprint(pattern, audio, 0.0, 30.0).skipped_reason == 'fingerprint_failed'


def test_audio_removed_is_reported_before_a_missing_original(fp, temp_db, pattern, tmp_path):
    temp_db.set_pattern_audio_removed(pattern, True)
    result = fp.store_fingerprint(pattern, str(tmp_path / 'gone.wav'), 0.0, 30.0)
    assert result.skipped_reason == 'audio_removed'


def test_stored_span_is_clamped_to_the_decoded_audio(fp, temp_db, pattern, audio, monkeypatch):
    monkeypatch.setattr(fp, '_get_audio_duration', lambda path: 20.0)
    assert fp.store_fingerprint(pattern, audio, 10.0, 40.0).stored is True
    row = temp_db.get_audio_fingerprint(pattern)
    assert (row['duration'], row['source_end']) == (10.0, 20.0)
    assert fp.calls == [(audio, 10.0, 10.0)]


def test_span_starting_past_the_decoded_audio_is_too_short(fp, pattern, audio, monkeypatch):
    monkeypatch.setattr(fp, '_get_audio_duration', lambda path: 20.0)
    assert fp.store_fingerprint(pattern, audio, 25.0, 40.0).skipped_reason == 'segment_too_short'


@pytest.mark.parametrize('raw,short,long', [
    ('1', 2.9, 3.0), ('100', 29.9, 30.0), ('nan', 4.9, 5.0), ('junk', 4.9, 5.0), ('12', 11.9, 12.0),
])
def test_minimum_length_setting_is_clamped_to_3_to_30_seconds(fp, temp_db, pattern, audio, raw, short, long):
    temp_db.set_setting('fingerprint_min_ad_length_s', raw)
    assert fp.store_fingerprint(pattern, audio, 0.0, short).skipped_reason == 'segment_too_short'
    assert fp.store_fingerprint(pattern, audio, 0.0, long).stored is True


def _chromaprint(ints):
    return acoustid.chromaprint.encode_fingerprint(ints, 1, True).decode('ascii')


def _generating(fp, monkeypatch, ints):
    monkeypatch.setattr(fp, 'generate_fingerprint',
                        lambda path, start=0, duration=None: AudioFingerprint(_chromaprint(ints), duration))


def test_non_distinctive_audio_is_refused(fp, temp_db, pattern, audio, monkeypatch):
    _generating(fp, monkeypatch, [7] * 400)
    assert fp.store_fingerprint(pattern, audio, 0.0, 30.0) == FingerprintStoreResult(False, 'not_distinctive')
    assert temp_db.get_audio_fingerprint(pattern) is None


def test_force_does_not_bypass_not_distinctive(fp, temp_db, pattern, audio, monkeypatch):
    _generating(fp, monkeypatch, [7] * 400)
    assert fp.store_fingerprint(pattern, audio, 0.0, 30.0, force=True).skipped_reason == 'not_distinctive'


def test_distinctive_audio_is_stored(fp, temp_db, pattern, audio, monkeypatch):
    rng = random.Random(3)
    _generating(fp, monkeypatch, [rng.getrandbits(32) for _ in range(400)])
    assert fp.store_fingerprint(pattern, audio, 0.0, 30.0).stored is True


def test_minimum_coverage_setting_decides_distinctiveness(fp, temp_db, pattern, audio, monkeypatch):
    rng = random.Random(3)
    _generating(fp, monkeypatch, [rng.getrandbits(32) for _ in range(300)] + [7] * 100)
    assert fp.store_fingerprint(pattern, audio, 0.0, 30.0).stored is True
    temp_db.set_setting('fingerprint_min_coverage', '0.9')
    assert fp.store_fingerprint(pattern, audio, 0.0, 30.0).skipped_reason == 'not_distinctive'


def test_undecodable_fingerprint_is_stored_with_a_warning(fp, temp_db, pattern, audio, caplog):
    with caplog.at_level(logging.WARNING, logger=af.logger.name):
        assert fp.store_fingerprint(pattern, audio, 0.0, 30.0).stored is True
    warnings = [r for r in caplog.records if r.name == af.logger.name and r.levelno == logging.WARNING]
    assert len(warnings) == 1 and str(pattern) in warnings[0].getMessage()


def test_several_patterns_of_one_original_probe_its_duration_once(fp, pattern, audio, temp_db, monkeypatch):
    other = temp_db.create_ad_pattern(scope='podcast', podcast_id='example-podcast',
                                      text_template='other read ' * 12)
    probes = []
    monkeypatch.setattr(fp, '_get_audio_duration', lambda path: probes.append(path) or 300.0)
    assert fp.store_fingerprint(pattern, audio, 10.0, 40.0).stored
    assert fp.store_fingerprint(other, audio, 40.0, 70.0).stored
    assert probes == [audio]


def test_a_failed_duration_probe_is_not_remembered(fp, pattern, audio, monkeypatch):
    results = iter([0.0, 300.0])
    probes = []
    monkeypatch.setattr(fp, '_get_audio_duration', lambda path: probes.append(path) or next(results))
    fp.store_fingerprint(pattern, audio, 10.0, 40.0, force=True)
    fp.store_fingerprint(pattern, audio, 10.0, 40.0, force=True)
    assert len(probes) == 2
