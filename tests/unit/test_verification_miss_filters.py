"""Tests for the 2.5.13 filter parity in PatternService.record_verification_misses.

Pre-2.5.13 the verification-miss path called TextPatternMatcher.create_pattern_from_ad
with zero filtering. The first-pass learner at
ad_detector._ad_passes_learning_filters enforced confidence >= 0.85 (0.92 if
duration > 90s), was_cut == True, and a clean sponsor name. The asymmetry let
Pattern #354 (drink-champs, Modelo, host conversation about meeting Nas) ship.

These tests pin the new filters at the same thresholds so the two auto-pattern
paths share one trust model.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'src'))

from database import Database
from pattern_service import (
    PatternService,
    VERIFICATION_MIN_CONFIDENCE,
    VERIFICATION_MIN_CONFIDENCE_LONG,
)


@pytest.fixture
def db(tmp_path):
    Database._instance = None
    if hasattr(Database, '_initialized'):
        Database._initialized = False
    inst = Database(data_dir=str(tmp_path))
    yield inst
    Database._instance = None


def _modelo_window():
    """Pattern #354 source text - host conversation, brand mentioned once."""
    return (
        "This is a fucking performance. Yo, how you get the big, Modelo? "
        "No, no, no, later, later, later. I'm good. We shootin'. Let's go. "
        "Next question. You ever meet Pac, though? No. Never met Pac. Biggie. "
        "Met Biggie. I got a Nas story."
    )


def _real_ad_window():
    return (
        "BetterHelp therapy can help you live a more empowered life. "
        "Visit them online to start with a licensed therapist today. "
        "BetterHelp matches you in 24 hours."
    )


def _segments(text, start=0.0, end=37.0):
    return [{'start': start, 'end': end, 'text': text}]


def test_low_confidence_verification_miss_is_dropped(db):
    """A verification miss below the first-pass confidence floor never reaches
    create_pattern_from_ad. The Modelo false positive came in with no
    confidence floor enforcement at all."""
    svc = PatternService(db=db)
    svc.record_verification_misses(
        'drink-champs', '30c9a2d49f13',
        [{
            'sponsor': 'Modelo', 'start': 0.0, 'end': 37.0,
            'confidence': VERIFICATION_MIN_CONFIDENCE - 0.01,
            'reason': 'Modelo sponsor mention',
        }],
        segments=_segments(_modelo_window()),
    )
    # Nothing should have been created (db is empty of patterns).
    assert db.get_ad_patterns(active_only=True) == []


def test_long_verification_miss_needs_higher_confidence(db):
    """For long (>90s) verification misses, the floor is 0.92, not 0.85.
    A 100s ad at confidence 0.86 should be rejected."""
    svc = PatternService(db=db)
    long_text = _real_ad_window() * 5  # padded to avoid hallucination filter
    svc.record_verification_misses(
        'some-show', 'abc',
        [{
            'sponsor': 'BetterHelp', 'start': 0.0, 'end': 100.0,
            'confidence': VERIFICATION_MIN_CONFIDENCE_LONG - 0.01,
            'reason': 'BetterHelp ad',
        }],
        segments=_segments(long_text, end=100.0),
    )
    assert db.get_ad_patterns(active_only=True) == []


def test_reasoning_sentence_in_reason_is_dropped(db):
    """If the LLM put its rationale in the `reason` field (instead of a real
    ad description), reject. Mirrors the 2.5.11 sponsor-field guard."""
    svc = PatternService(db=db)
    svc.record_verification_misses(
        'some-show', 'abc',
        [{
            'sponsor': 'Modelo', 'start': 0.0, 'end': 37.0,
            'confidence': 0.95,
            'reason': 'Inferred from a 26-second silence in transcript',
        }],
        segments=_segments(_modelo_window()),
    )
    assert db.get_ad_patterns(active_only=True) == []


def test_sponsor_mentioned_once_in_window_is_dropped(db):
    """The Modelo shape exactly: high confidence, no reasoning prefix, sponsor
    appears once in the transcript window. Real sponsor reads repeat the brand,
    so the second-line gate at this layer prevents the pattern from being
    created even if confidence passes."""
    svc = PatternService(db=db)
    svc.record_verification_misses(
        'drink-champs', '30c9a2d49f13',
        [{
            'sponsor': 'Modelo', 'start': 0.0, 'end': 37.0,
            'confidence': 0.95,
            'reason': 'Modelo sponsor read',
        }],
        segments=_segments(_modelo_window()),
    )
    assert db.get_ad_patterns(active_only=True) == []


def test_real_ad_passes_all_filters(db):
    """A high-confidence ad whose brand appears twice in the window must still
    reach create_pattern_from_ad and produce a row."""
    svc = PatternService(db=db)
    svc.record_verification_misses(
        'some-show', 'abc',
        [{
            'sponsor': 'BetterHelp', 'start': 0.0, 'end': 60.0,
            'confidence': 0.95,
            'reason': 'BetterHelp host-read',
        }],
        segments=_segments(_real_ad_window(), end=60.0),
    )
    assert len(db.get_ad_patterns(active_only=True)) == 1


def test_existing_pattern_is_still_boosted_when_filters_pass(db):
    """If a pattern already exists for the sponsor and the verification miss
    passes the filters, boost confirmation_count instead of creating a new
    row. Pre-existing behaviour must not regress."""
    existing = db.get_known_sponsor_by_name('BetterHelp')
    sp_id = existing['id'] if existing else db.create_known_sponsor(
        name='BetterHelp', aliases=[], category=None
    )
    pid = db.create_ad_pattern(
        scope='podcast',
        text_template='BetterHelp ad. Try BetterHelp today.',
        sponsor_id=sp_id,
        intro_variants=[],
        outro_variants=[],
        podcast_id='some-show',
    )
    svc = PatternService(db=db)
    svc.record_verification_misses(
        'some-show', 'abc',
        [{
            'sponsor': 'BetterHelp', 'start': 0.0, 'end': 60.0,
            'confidence': 0.95,
            'reason': 'BetterHelp host-read',
        }],
        segments=_segments(_real_ad_window(), end=60.0),
    )
    row = db.get_ad_pattern_by_id(pid)
    assert row['confirmation_count'] >= 1
