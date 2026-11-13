"""The two fingerprint response fields come from per-pattern outcomes by a fixed precedence."""
import pytest

from api.patterns import FINGERPRINT_SKIP_PRECEDENCE, fingerprint_response_fields
from audio_fingerprinter import FingerprintStoreResult

STORED = FingerprintStoreResult(True)


def _skipped(reason):
    return FingerprintStoreResult(False, reason)


def test_any_stored_pattern_means_stored_and_no_reason():
    outcomes = [(1, _skipped('segment_too_short')), (2, STORED)]
    assert fingerprint_response_fields(outcomes) == {'fingerprintStored': True}


def test_no_outcomes_omits_both_fields():
    assert fingerprint_response_fields([]) == {}


@pytest.mark.parametrize('present,winner', [
    (['fingerprint_failed', 'not_distinctive'], 'not_distinctive'),
    (['not_distinctive', 'segment_too_short'], 'segment_too_short'),
    (['segment_too_short', 'original_not_retained'], 'original_not_retained'),
    (['original_not_retained', 'span_covers_multiple_patterns'], 'span_covers_multiple_patterns'),
    (['span_covers_multiple_patterns', 'fingerprint_exists'], 'fingerprint_exists'),
    (['fingerprint_exists', 'audio_removed'], 'audio_removed'),
    (list(reversed(FINGERPRINT_SKIP_PRECEDENCE)), 'audio_removed'),
])
def test_the_first_reason_by_precedence_wins(present, winner):
    outcomes = [(i, _skipped(reason)) for i, reason in enumerate(present)]
    assert fingerprint_response_fields(outcomes) == {
        'fingerprintStored': False, 'fingerprintSkippedReason': winner}


def test_the_precedence_lists_every_reason_once():
    assert FINGERPRINT_SKIP_PRECEDENCE == (
        'audio_removed', 'fingerprint_exists', 'span_covers_multiple_patterns', 'span_not_found',
        'original_not_retained', 'segment_too_short', 'not_distinctive', 'fingerprint_failed')
