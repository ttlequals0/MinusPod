"""A fingerprint stage that cannot read the audio leaves the text-pattern stage and the run intact."""
import logging
import stat
from unittest.mock import MagicMock

import pytest

import llm_client
from ad_detector import AdDetector
from audio_fingerprinter import AudioFingerprinter
from text_pattern_matcher import TextMatch

SLUG, EPISODE = 'example-show', 'a1b2c3d4e5f6'


@pytest.fixture
def detector(temp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(llm_client, 'get_effective_provider', lambda: 'none')
    pid = temp_db.create_ad_pattern(text_template='read ' * 12, scope='global')
    temp_db.create_audio_fingerprint(pid, b'AQAA', 10.0)
    script = tmp_path / 'fake-fpcalc'
    script.write_text('#!/bin/sh\necho "decode error" >&2\nexit 3\n')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    fp = AudioFingerprinter.__new__(AudioFingerprinter)
    fp.db = temp_db
    fp._fpcalc_path = str(script)
    monkeypatch.setattr(fp, '_get_audio_duration', lambda p: 100.0)
    det = AdDetector(api_key='test-key')
    det.db = temp_db
    det.audio_fingerprinter = fp
    det.text_pattern_matcher = MagicMock()
    det.text_pattern_matcher.find_matches.return_value = [
        TextMatch(pattern_id=pid, start=10.0, end=40.0, confidence=0.95, sponsor='Acme')]
    return det


def test_text_patterns_still_run_when_the_whole_file_fingerprint_fails(detector, caplog):
    segments = [{'start': 0.0, 'end': 50.0, 'text': 'hello'}]
    with caplog.at_level(logging.WARNING):
        result = detector.process_transcript(
            segments, slug=SLUG, episode_id=EPISODE, audio_path='/media/x.mp3', podcast_id=SLUG)
    stats = result['detection_stats']
    assert stats['fingerprint_matches'] == 0
    assert stats['text_pattern_matches'] == 1
    assert [ad['detection_stage'] for ad in result['ads']] == ['text_pattern']
    fingerprint_warnings = [r.getMessage() for r in caplog.records
                            if r.name == 'podcast.fingerprint' and r.levelno >= logging.WARNING]
    assert len(fingerprint_warnings) == 1 and f'{SLUG}:{EPISODE}' in fingerprint_warnings[0]
