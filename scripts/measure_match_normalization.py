#!/usr/bin/env python3
"""Measure whether position-preserving text normalization at match time would
recover fuzzy pattern matches that today's `.lower()`-only scoring misses.
Read-only; does not change detection behaviour.

Reimplements `required_fuzzy_score` locally instead of importing it from
text_pattern_matcher.py, which this measurement task must not touch.
"""
import argparse
import json
import re
import sqlite3
from itertools import combinations
from pathlib import Path

from rapidfuzz import fuzz

from config import FUZZY_MATCH_THRESHOLD

# Mirrors text_pattern_matcher.FUZZY_DISCRIMINATIVE_LENGTH: below this
# length a phrase is not discriminative on its own.
FUZZY_DISCRIMINATIVE_LENGTH = 60

DEFAULT_DB_PATH = Path("/app/data/podcast.db")
DEFAULT_JSON_FALLBACK = Path("/tmp/pats.json")

NON_ALNUM_SPACE = re.compile(r'[^a-z0-9\s]')
ARTIFACT_RE = re.compile(r'[^a-zA-Z0-9\s]|\s{2,}')


def required_fuzzy_score(phrase_len: int) -> float:
    """Score a phrase of this length must reach to count as a match."""
    base = FUZZY_MATCH_THRESHOLD * 100
    return min(98.0, base + max(0, FUZZY_DISCRIMINATIVE_LENGTH - phrase_len) * 0.5)


def normalize_preserve_positions(text: str) -> str:
    """Lowercase and blank non-alnum/space chars one-for-one, so length and
    character offsets are unchanged from the input."""
    return NON_ALNUM_SPACE.sub(' ', text.lower())


def load_patterns_from_db(db_path: Path) -> list:
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute(
            "SELECT id, podcast_id, text_template FROM ad_patterns "
            "WHERE text_template IS NOT NULL AND text_template != ''"
        ).fetchall()
    finally:
        conn.close()
    return [{"id": r[0], "podcast_id": r[1], "text_template": r[2]} for r in rows]


def load_patterns_from_json(json_path: Path) -> list:
    data = json.loads(json_path.read_text())
    patterns = data.get("patterns", data) if isinstance(data, dict) else data
    return [
        {"id": p.get("id"), "podcast_id": p.get("podcast_id"), "text_template": p.get("text_template")}
        for p in patterns
        if p.get("text_template")
    ]


def load_patterns(args) -> list:
    if args.patterns:
        return load_patterns_from_json(Path(args.patterns))
    try:
        return load_patterns_from_db(Path(args.db_path))
    except sqlite3.OperationalError as e:
        if DEFAULT_JSON_FALLBACK.exists():
            print(f"DB unreadable ({e}); falling back to {DEFAULT_JSON_FALLBACK}")
            return load_patterns_from_json(DEFAULT_JSON_FALLBACK)
        raise


def measure_pairs(patterns: list) -> dict:
    # podcast_id is null for global-scope patterns; those don't share a
    # podcast, so exclude them rather than treating null as one giant group.
    by_podcast = {}
    for p in patterns:
        if p["podcast_id"] is not None:
            by_podcast.setdefault(p["podcast_id"], []).append(p)

    total_pairs = 0
    newly_crossing = []
    for group in by_podcast.values():
        if len(group) < 2:
            continue
        for a, b in combinations(group, 2):
            ta, tb = a["text_template"], b["text_template"]
            total_pairs += 1
            score_lower = fuzz.partial_ratio(ta.lower(), tb.lower())
            score_norm = fuzz.partial_ratio(
                normalize_preserve_positions(ta), normalize_preserve_positions(tb)
            )
            threshold = required_fuzzy_score(min(len(ta), len(tb)))
            if score_lower < threshold <= score_norm:
                newly_crossing.append((a["id"], b["id"], score_lower, score_norm, threshold))

    return {
        "total_pairs": total_pairs,
        "newly_crossing": newly_crossing,
    }


def measure_artifacts(patterns: list) -> int:
    return sum(1 for p in patterns if ARTIFACT_RE.search(p["text_template"]))


def measure_hyphen_hypothesis():
    """Test the reported failure mode: an operator's stored pattern has a
    spaced hyphen ('best -in-class') the transcript reads without."""
    stored = "the best -in-class widget review show"
    transcript = "the best-in-class widget review show"

    score_lower = fuzz.partial_ratio(stored.lower(), transcript.lower())
    score_norm = fuzz.partial_ratio(
        normalize_preserve_positions(stored), normalize_preserve_positions(transcript)
    )
    norm_stored = normalize_preserve_positions(stored)
    norm_transcript = normalize_preserve_positions(transcript)

    return {
        "stored": stored,
        "transcript": transcript,
        "score_lower": score_lower,
        "score_norm": score_norm,
        "norm_stored": norm_stored,
        "norm_transcript": norm_transcript,
        "lengths_equal": len(stored) == len(transcript),
        "equalized": norm_stored == norm_transcript,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--patterns", help="JSON export path (overrides DB)")
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH), help="SQLite DB path")
    args = parser.parse_args()

    patterns = load_patterns(args)
    print(f"Loaded {len(patterns)} patterns with a text_template")

    artifact_count = measure_artifacts(patterns)
    print(f"Patterns with punctuation or double-space artifacts: {artifact_count} / {len(patterns)}")

    result = measure_pairs(patterns)
    print(f"Same-podcast pattern pairs compared: {result['total_pairs']}")
    print(
        f"Pairs crossing required_fuzzy_score under position-preserving "
        f"normalization (newly recovered matches / newly possible false "
        f"merges, same count viewed both ways): {len(result['newly_crossing'])}"
    )
    for pid_a, pid_b, lo, norm, thr in result["newly_crossing"]:
        print(f"  pattern {pid_a} vs {pid_b}: lower={lo:.2f} norm={norm:.2f} threshold={thr:.2f}")

    print()
    print("Hyphen-spacing hypothesis (spaced vs unspaced hyphen):")
    h = measure_hyphen_hypothesis()
    print(f"  stored:     {h['stored']!r}")
    print(f"  transcript: {h['transcript']!r}")
    print(f"  score under today's .lower() only:       {h['score_lower']:.2f}")
    print(f"  score under position-preserving normalize: {h['score_norm']:.2f}")
    print(f"  same length: {h['lengths_equal']}")
    print(f"  normalized forms equal (fully equalized): {h['equalized']}")
    if not h["equalized"]:
        print(f"    normalized stored:     {h['norm_stored']!r}")
        print(f"    normalized transcript: {h['norm_transcript']!r}")


if __name__ == "__main__":
    main()
