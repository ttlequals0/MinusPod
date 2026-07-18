"""_find_matches_fast vectorized scan vs the old scalar reference.

The fast-path scan was rewritten to compute per-pattern similarity with
vectorized numpy popcounts. The reference below mirrors the old scalar
logic verbatim (per-position _calculate_similarity loop); results must be
bit-identical: same similarity values, same threshold decisions, same
match list in the same order.
"""
import random

import audio_fingerprinter as af
from audio_fingerprinter import (
    AudioFingerprinter,
    FingerprintMatch,
    FINGERPRINT_CHUNK_SIZE,
    MIN_SEGMENT_DURATION,
    SLIDING_STEP_SIZE,
)


def _ref_similarity(fp1, fp2, fp1_start, fp1_end):
    # Verbatim copy of the scalar _calculate_similarity comparison loop.
    if not fp1 or not fp2:
        return 0.0
    if fp1_end == 0:
        fp1_end = len(fp1)
    min_len = min(fp1_end - fp1_start, len(fp2))
    if min_len <= 0:
        return 0.0
    total_bits = 0
    matching_bits = 0
    for i in range(min_len):
        xor = (fp1[fp1_start + i] ^ fp2[i]) & 0xFFFFFFFF
        matching_bits += 32 - xor.bit_count()
        total_bits += 32
    return matching_bits / total_bits if total_bits > 0 else 0.0


def _ref_find_matches_fast(fp, raw_ints, fp_duration, decoded_known,
                           total_duration, threshold):
    # Mirror of the pre-vectorization _find_matches_fast greedy scan.
    matches = []
    ints_per_second = len(raw_ints) / fp_duration if fp_duration > 0 else 8.0
    position = 0.0
    while position < total_duration - MIN_SEGMENT_DURATION:
        start_idx = int(position * ints_per_second)
        end_idx = int((position + FINGERPRINT_CHUNK_SIZE) * ints_per_second)
        end_idx = min(end_idx, len(raw_ints))
        if end_idx - start_idx < 4:
            position += SLIDING_STEP_SIZE
            continue
        matched = False
        for pattern_id, known_ints, known_duration, sponsor in decoded_known:
            similarity = _ref_similarity(raw_ints, known_ints, start_idx, end_idx)
            if similarity >= threshold:
                matches.append(FingerprintMatch(
                    pattern_id=pattern_id,
                    start=position,
                    end=position + known_duration,
                    confidence=similarity,
                    sponsor=sponsor,
                ))
                position += known_duration
                matched = True
                break
        if not matched:
            position += SLIDING_STEP_SIZE
    return fp._merge_overlapping_matches(matches)


def _rand_ints(rng, n):
    # fpcalc -raw emits signed 32-bit ints; include negatives.
    return [rng.getrandbits(32) - (1 << 31) for _ in range(n)]


def _plant(episode, pattern, idx, rng, flips_per_int):
    # Copy the pattern into the episode with a few flipped bits per int.
    for j, v in enumerate(pattern):
        noisy = v
        for _ in range(flips_per_int):
            noisy ^= 1 << rng.randrange(32)
        episode[idx + j] = noisy


def _fp():
    return AudioFingerprinter.__new__(AudioFingerprinter)


def _run_both(fp, raw_ints, fp_duration, decoded_known, total_duration):
    expected = _ref_find_matches_fast(
        fp, raw_ints, fp_duration, decoded_known, total_duration,
        af.MATCH_THRESHOLD)
    got = fp._find_matches_fast(
        raw_ints, fp_duration, decoded_known, total_duration,
        timeout=600, cancel_event=None)
    return got, expected


def test_planted_patterns_identical_to_scalar_reference():
    rng = random.Random(1234)
    ips = 8.0
    total_duration = 240.0
    n = int(total_duration * ips)
    raw_ints = _rand_ints(rng, n)

    # Durations are multiples of the 2s step so post-match jumps keep the
    # scan grid aligned with the planted offsets.
    short_pat = _rand_ints(rng, 48)   # 6s, shorter than the 10s chunk
    long_pat = _rand_ints(rng, 160)   # 20s, longer than the chunk
    noisy_pat = _rand_ints(rng, 64)   # 8s
    decoded_known = [
        (1, short_pat, len(short_pat) / ips, 'sponsor-a'),
        (2, long_pat, len(long_pat) / ips, 'sponsor-b'),
        (3, noisy_pat, len(noisy_pat) / ips, None),
    ]

    _plant(raw_ints, short_pat, int(30 * ips), rng, flips_per_int=2)
    _plant(raw_ints, long_pat, int(90 * ips), rng, flips_per_int=2)
    # Heavy noise lands nearer the threshold.
    _plant(raw_ints, noisy_pat, int(150 * ips), rng, flips_per_int=8)
    # Long-pattern prefix at the file tail exercises the truncated-slice
    # branch (end_idx == len(raw_ints), slice shorter than the pattern).
    _plant(raw_ints, long_pat[:80], n - 80, rng, flips_per_int=2)

    fp = _fp()
    got, expected = _run_both(fp, raw_ints, total_duration, decoded_known,
                              total_duration)
    assert got == expected
    assert len(expected) >= 3  # the planted patterns were actually found


def test_fuzz_identical_to_scalar_reference(monkeypatch):
    # Threshold just above random-background similarity (~0.5) so noise
    # occasionally crosses it: exercises near-boundary comparisons, first-
    # pattern-wins ordering, and off-grid positions after duration jumps.
    monkeypatch.setattr(af, 'MATCH_THRESHOLD', 0.52)
    for seed in range(5):
        rng = random.Random(seed)
        total_duration = 120.0
        # Non-integer ints-per-second varies the chunk length by +-1 int
        # across positions (int truncation), like real fpcalc output.
        ips = rng.uniform(7.8, 8.2)
        n = int(total_duration * ips)
        raw_ints = _rand_ints(rng, n)
        decoded_known = [
            (10, _rand_ints(rng, 40), 5.0, 'a'),
            (11, _rand_ints(rng, 120), 15.0, 'b'),
            (12, _rand_ints(rng, rng.randrange(30, 100)), 7.3, None),
            (13, [], 4.0, 'empty'),      # scalar guard: empty pattern -> 0.0
            (14, _rand_ints(rng, 2), 1.0, 'tiny'),
        ]
        _plant(raw_ints, decoded_known[1][1], int(40 * ips), rng,
               flips_per_int=3)

        fp = _fp()
        got, expected = _run_both(fp, raw_ints, total_duration, decoded_known,
                                  total_duration)
        assert got == expected, f"seed={seed}"


def test_empty_episode_and_no_patterns():
    fp = _fp()
    got, expected = _run_both(fp, [], 0.0, [(1, [1, 2, 3, 4], 2.0, 's')], 60.0)
    assert got == expected == []
    rng = random.Random(7)
    raw = _rand_ints(rng, 480)
    got, expected = _run_both(fp, raw, 60.0, [], 60.0)
    assert got == expected == []
