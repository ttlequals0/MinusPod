"""Cross-episode text recurrence (hushpod adoption, spec 2026-08-25).

Formulaic shows repeat spoken boilerplate near-verbatim every episode
(intro spiels, disclaimers, credits) in wording even when the audio is
re-recorded, so audio fingerprints miss it. This module finds spans of the
current transcript whose 8-word shingles appear in at least 2 prior
episodes. Output feeds the pass-1 prompt as a hint only; nothing is ever
cut on text recurrence alone.

Pure functions, no Flask or DB imports (unit tests import directly, same
convention as split_planning.py).
"""
import re

SHINGLE_SIZE = 8
MIN_PRIOR_EPISODES = 2
SEGMENT_COVERAGE = 0.5
MAX_GAP_SEGMENTS = 1
MIN_SPAN_SECONDS = 5.0
MAX_HINT_SPANS = 12
SNIPPET_CHARS = 160

_WORD_RE = re.compile(r'[a-z0-9]+')


def _tokenize(segments):
    """Lowercased words tagged with the index of the segment they came from."""
    tokens = []
    for idx, seg in enumerate(segments):
        for word in _WORD_RE.findall(seg.get('text', '').lower()):
            tokens.append((word, idx))
    return tokens


def _shingle_set(segments):
    """Distinct SHINGLE_SIZE-word shingles in one episode (set-deduped, so
    repetition inside a single episode cannot satisfy the cross-episode
    threshold)."""
    words = [w for w, _ in _tokenize(segments)]
    return {' '.join(words[i:i + SHINGLE_SIZE])
            for i in range(len(words) - SHINGLE_SIZE + 1)}


def find_recurring_spans(segments, prior_segment_lists):
    """Spans of `segments` whose wording recurs in >= MIN_PRIOR_EPISODES of
    `prior_segment_lists`. Fewer than MIN_PRIOR_EPISODES priors: no spans
    (the threshold is never relaxed)."""
    if len(prior_segment_lists) < MIN_PRIOR_EPISODES or not segments:
        return []
    prior_shingles = [_shingle_set(p) for p in prior_segment_lists]

    tokens = _tokenize(segments)
    if len(tokens) < SHINGLE_SIZE:
        return []
    words = [w for w, _ in tokens]
    recurring = [False] * len(tokens)
    for i in range(len(tokens) - SHINGLE_SIZE + 1):
        shingle = ' '.join(words[i:i + SHINGLE_SIZE])
        hits = sum(1 for ps in prior_shingles if shingle in ps)
        if hits >= MIN_PRIOR_EPISODES:
            for j in range(i, i + SHINGLE_SIZE):
                recurring[j] = True

    # Roll token marks up to segments by word coverage.
    # Short segments (< SHINGLE_SIZE words) require 100% coverage to avoid
    # false positives from connector-phrase bleed across segment boundaries.
    # Longer segments use standard SEGMENT_COVERAGE threshold.
    total = [0] * len(segments)
    hit = [0] * len(segments)
    for (_, seg_idx), is_rec in zip(tokens, recurring, strict=True):
        total[seg_idx] += 1
        if is_rec:
            hit[seg_idx] += 1
    seg_recurring = []
    for t, h in zip(total, hit, strict=True):
        if t == 0:
            seg_recurring.append(False)
        elif t < SHINGLE_SIZE:
            # Short segment: must be 100% covered
            seg_recurring.append(h == t)
        else:
            # Long segment: standard coverage threshold
            seg_recurring.append(h / t >= SEGMENT_COVERAGE)

    # Merge adjacent recurring segments, bridging small gaps.
    spans = []
    run_start = None
    gap = 0
    for idx, flag in enumerate(seg_recurring + [False]):
        if flag:
            if run_start is None:
                run_start = idx
            gap = 0
        elif run_start is not None:
            gap += 1
            if gap > MAX_GAP_SEGMENTS or idx >= len(seg_recurring):
                run_end = min(idx - gap, len(seg_recurring) - 1)
                spans.append((run_start, run_end))
                run_start = None
                gap = 0
    result = []
    for lo, hi in spans:
        start = segments[lo]['start']
        end = segments[hi]['end']
        if end - start < MIN_SPAN_SECONDS:
            continue
        text = ' '.join(s.get('text', '') for s in segments[lo:hi + 1]).strip()
        result.append({'start': start, 'end': end, 'text': text})
    return result


def format_recurrence_hint(spans, window_start, window_end):
    """Prompt block listing recurring spans that overlap this window, or ""."""
    in_window = [s for s in spans
                 if s['end'] > window_start and s['start'] < window_end]
    if not in_window:
        return ""
    lines = [
        f"- {s['start']:.1f}s to {s['end']:.1f}s: \"{s['text'][:SNIPPET_CHARS]}\""
        for s in in_window[:MAX_HINT_SPANS]
    ]
    return (
        "\n\nRECURRING BOILERPLATE (wording repeats near-verbatim in previous "
        "episodes of this show, so these spans are almost certainly recurring "
        "intro/outro/credits/housekeeping rather than unique content; if part "
        "of a span is a sponsor read or promotional plug, report that part as "
        "an ad):\n" + "\n".join(lines) + "\n"
    )
