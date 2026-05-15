"""Realign start/end timestamps in benchmark truth.txt files against segments.json.

Walks every candidate dir under ``benchmarks/llm/data/candidates/`` and rewrites the
``start:`` / ``end:`` values of each ad block to match where the (possibly hand-edited)
``text:`` payload actually lands in the episode's word-level whisper output.

Usage:
    .venv/bin/python benchmarks/llm/scripts/realign_truth.py            # dry-run
    .venv/bin/python benchmarks/llm/scripts/realign_truth.py --write    # apply
    .venv/bin/python benchmarks/llm/scripts/realign_truth.py --only ep-tosh-show-...
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "benchmarks" / "llm" / "src"))

from benchmark.truth_parser import parse as parse_truth, TruthParseError  # noqa: E402

CANDIDATES_DIR = REPO_ROOT / "benchmarks" / "llm" / "data" / "candidates"
WORD_RE = re.compile(r"[a-z0-9]+")

START_RE = re.compile(r"^(\s*start\s*:\s*)(.*?)(\r?\n)?$", re.IGNORECASE)
END_RE = re.compile(r"^(\s*end\s*:\s*)(.*?)(\r?\n)?$", re.IGNORECASE)
SEP_RE = re.compile(r"^\s*---\s*$")
COMMENT_RE = re.compile(r"^\s*#")


def _norm_words(text: str) -> list[str]:
    return WORD_RE.findall(text.lower())


def _build_word_stream(segments: list[dict]) -> list[tuple[str, float, float]]:
    stream: list[tuple[str, float, float]] = []
    for seg in segments:
        words = seg.get("words") or []
        if words:
            for w in words:
                norms = _norm_words(w.get("word", ""))
                if not norms:
                    continue
                ws = float(w["start"])
                we = float(w["end"])
                if len(norms) == 1:
                    stream.append((norms[0], ws, we))
                else:
                    span = (we - ws) / len(norms)
                    for i, n in enumerate(norms):
                        stream.append((n, ws + i * span, ws + (i + 1) * span))
        else:
            norms = _norm_words(seg.get("text", ""))
            if not norms:
                continue
            ss = float(seg["start"])
            se = float(seg["end"])
            span = (se - ss) / max(len(norms), 1)
            for i, n in enumerate(norms):
                stream.append((n, ss + i * span, ss + (i + 1) * span))
    return stream


def _first_stream_index_after(stream, min_time: float) -> int:
    for i, (_, _, e) in enumerate(stream):
        if e >= min_time:
            return i
    return len(stream)


def _find_run(stream_words: list[str], needle: list[str], *, start: int, stop: int) -> int:
    """Return earliest index in stream_words[start:stop] where ``needle`` appears contiguously.

    Returns -1 if no exact contiguous run is found.
    """
    if not needle:
        return -1
    n = len(needle)
    last = min(stop, len(stream_words)) - n
    for i in range(start, last + 1):
        if stream_words[i:i + n] == needle:
            return i
    return -1


def _anchor_prefix(stream_words: list[str], truth_words: list[str], *, search_from: int) -> int:
    """Find the start-of-ad anchor: earliest contiguous match for the first K truth words.

    Tries K = 6, 5, 4, 3 in order so we still find a hit when the user trimmed leading words
    or transcription noise corrupted them. Returns the stream index or -1.
    """
    for k in (6, 5, 4, 3):
        if len(truth_words) < k:
            continue
        idx = _find_run(stream_words, truth_words[:k], start=search_from, stop=len(stream_words))
        if idx >= 0:
            return idx
    return -1


def _anchor_suffix(
    stream_words: list[str],
    truth_words: list[str],
    *,
    start_offset: int,
    max_span: int,
) -> int:
    """Find the end-of-ad anchor: latest contiguous match for the last K truth words,
    constrained to stream[start_offset : start_offset + max_span].

    Returns the stream index of the START of the matched suffix run, or -1.
    """
    stop = min(len(stream_words), start_offset + max_span)
    for k in (6, 5, 4, 3):
        if len(truth_words) < k:
            continue
        needle = truth_words[-k:]
        # Walk forward and remember the LAST hit -- the suffix is more likely to be
        # the boundary if the same phrase recurs (e.g. duplicated ad copy).
        last_hit = -1
        for i in range(start_offset, stop - k + 1):
            if stream_words[i:i + k] == needle:
                last_hit = i
        if last_hit >= 0:
            return last_hit + k - 1  # index of the last word of the suffix run
    return -1


def _align(truth_words, stream, *, search_from):
    """Return (start_idx, end_idx) inclusive into ``stream`` for this ad's span, or None.

    Strategy:
      1. Anchor the START at the earliest contiguous run of the first K truth words after
         ``search_from``. Falls back from K=6 down to K=3.
      2. Anchor the END at the latest contiguous run of the last K truth words within a
         window of ``2 * len(truth_words)`` stream tokens after the start anchor (so we
         don't run away when the same ad copy recurs later in the episode).
    """
    stream_words = [t[0] for t in stream]
    if not stream_words:
        return None
    start_idx = _anchor_prefix(stream_words, truth_words, search_from=search_from)
    if start_idx < 0:
        return None
    max_span = max(len(truth_words) * 2, 60)
    end_idx = _anchor_suffix(
        stream_words, truth_words, start_offset=start_idx, max_span=max_span
    )
    if end_idx < 0 or end_idx < start_idx:
        return None
    return start_idx, end_idx


def _fmt_mscc(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    minutes = int(seconds // 60)
    remainder = seconds - minutes * 60
    return f"{minutes}:{remainder:05.2f}"


def realign_dir(dir_path: Path):
    truth_path = dir_path / "truth.txt"
    segs_path = dir_path / "segments.json"
    if not truth_path.is_file() or not segs_path.is_file():
        return None

    raw = truth_path.read_text()
    try:
        truth = parse_truth(truth_path)
    except TruthParseError as exc:
        print(f"[skip] {dir_path.name}: {exc}")
        return [], raw
    if not truth.ads:
        return [], raw

    segments = json.loads(segs_path.read_text())
    stream = _build_word_stream(segments)
    if not stream:
        raise RuntimeError(f"{dir_path.name}: no words in segments.json")

    changes = []
    new_starts: list[float] = []
    new_ends: list[float] = []
    prev_end_t = 0.0
    for i, ad in enumerate(truth.ads, start=1):
        t_words = _norm_words(ad.text)
        if not t_words:
            raise RuntimeError(f"{dir_path.name}: ad #{i} text has no words")
        search_from = _first_stream_index_after(stream, prev_end_t)
        result = _align(t_words, stream, search_from=search_from)
        if result is None:
            raise RuntimeError(
                f"{dir_path.name}: ad #{i} text could not be aligned to segments.json "
                f"(prev_end={prev_end_t:.2f}s); check that the edited text still resembles the transcript"
            )
        start_idx, end_idx = result
        new_start = stream[start_idx][1]
        new_end = stream[end_idx][2]
        if new_start < prev_end_t:
            new_start = prev_end_t
        if new_end <= new_start:
            raise RuntimeError(
                f"{dir_path.name}: ad #{i} aligned to zero-length span ({new_start:.2f}..{new_end:.2f})"
            )
        new_starts.append(new_start)
        new_ends.append(new_end)
        changes.append((ad.start, ad.end, new_start, new_end))
        prev_end_t = new_end

    lines = raw.splitlines(keepends=True)
    out_lines: list[str] = []
    block_n = 0
    saw_start = False
    saw_end = False
    for line in lines:
        stripped = line.strip()
        if SEP_RE.match(stripped):
            out_lines.append(line)
            if saw_start or saw_end:
                block_n += 1
                saw_start = saw_end = False
            continue
        if not stripped or COMMENT_RE.match(line):
            out_lines.append(line)
            continue
        m_start = START_RE.match(line)
        if m_start and not saw_start and block_n < len(new_starts):
            prefix = m_start.group(1)
            trailing = m_start.group(3) or ""
            out_lines.append(f"{prefix}{_fmt_mscc(new_starts[block_n])}{trailing}")
            saw_start = True
            continue
        m_end = END_RE.match(line)
        if m_end and not saw_end and block_n < len(new_ends):
            prefix = m_end.group(1)
            trailing = m_end.group(3) or ""
            out_lines.append(f"{prefix}{_fmt_mscc(new_ends[block_n])}{trailing}")
            saw_end = True
            continue
        out_lines.append(line)

    return changes, "".join(out_lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", help="apply changes (default: dry-run)")
    parser.add_argument("--only", help="limit to a single candidate ep_id")
    args = parser.parse_args()

    dirs = sorted(d for d in CANDIDATES_DIR.iterdir() if d.is_dir())
    if args.only:
        dirs = [d for d in dirs if d.name == args.only]
        if not dirs:
            print(f"no candidate dir matches {args.only!r}", file=sys.stderr)
            sys.exit(2)

    any_changes_pending = False
    for d in dirs:
        result = realign_dir(d)
        if result is None:
            continue
        changes, new_text = result
        if not changes:
            print(f"[skip] {d.name}: no ad blocks (no-ads or empty)")
            continue
        print(f"\n=== {d.name} ===")
        for i, (os_, oe_, ns_, ne_) in enumerate(changes, start=1):
            ds = ns_ - os_
            de = ne_ - oe_
            changed = abs(ds) >= 0.01 or abs(de) >= 0.01
            mark = "* " if changed else "  "
            print(
                f"  {mark}#{i}: start {_fmt_mscc(os_)} -> {_fmt_mscc(ns_)} ({ds:+.2f}s)"
                f"   end {_fmt_mscc(oe_)} -> {_fmt_mscc(ne_)} ({de:+.2f}s)"
            )
            if changed:
                any_changes_pending = True
        current = (d / "truth.txt").read_text()
        if args.write:
            if new_text != current:
                (d / "truth.txt").write_text(new_text)
                print(f"  [wrote] {d / 'truth.txt'}")
            else:
                print("  [unchanged]")

    if not args.write and any_changes_pending:
        print("\n(dry-run; rerun with --write to apply)")


if __name__ == "__main__":
    main()
