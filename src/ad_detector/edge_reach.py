"""Sentences just outside a candidate ad that the reviewer may name as part of the read (pure functions)."""
from ad_detector.boundaries import EDGE_SLACK_S, strong_splice_events, timed_line_segments
from text_pattern_matcher import _sentences
from utils.text import is_sparse_line

LEAD_IN_MAX_SENTENCES = 6
LEAD_IN_WINDOW_S = 30.0
TAIL_MAX_SENTENCES = 3
TAIL_WINDOW_S = 15.0
MAX_SENTENCE_S = 15.0
MAX_SENTENCE_GAP_S = 2.0


def strong_splices(events) -> list[tuple[float, float]]:
    """(start, end) of the strong digital-silence splices, in time order."""
    return sorted((float(e['time']), float(e['end_time'])) for e in strong_splice_events(events))


def build_sentences(segments: list[dict]) -> list[dict]:
    """Whole sentences from word-timed lines: {start, end, text, words, sparse}, overlaps dropped.
    A line without letters (music notes) is not speech and separates sentences."""
    lines = sorted(timed_line_segments(segments or []),
                   key=lambda line: (line['start'], line['end']))
    kept = []
    for line in lines:
        length = line['end'] - line['start']
        if kept and length > 0:
            shared = min(line['end'], kept[-1]['end']) - max(line['start'], kept[-1]['start'])
            if shared > 0.5 * length:
                continue
        kept.append(line)
    runs, run = [], []
    for line in kept:
        text = line.get('text', '').strip()
        if any(ch.isalpha() for ch in text):
            run.append({'start': line['start'], 'end': line['end'], 'text': text})
        elif run:
            runs.append(run)
            run = []
    if run:
        runs.append(run)
    sentences = []
    for run in runs:
        for sentence in _sentences(run, max_gap_s=MAX_SENTENCE_GAP_S, max_s=MAX_SENTENCE_S):
            words = len(sentence['text'].split())
            sentences.append(dict(sentence, words=words, sparse=is_sparse_line(
                sentence['end'] - sentence['start'], words)))
    return sentences


def edge_check_sentences(segments: list[dict], start: float, end: float):
    """(before, after): B1 is closest to the start; A1 closest to the end. Each carries its label."""
    sentences = build_sentences(segments)
    before = [s for s in sentences
              if s['end'] <= start + EDGE_SLACK_S
              and s['start'] >= start - LEAD_IN_WINDOW_S - EDGE_SLACK_S]
    before = [dict(s, label=f'B{i}') for i, s in
              enumerate(reversed(before[-LEAD_IN_MAX_SENTENCES:]), 1)]
    after = [s for s in sentences
             if s['start'] >= end - EDGE_SLACK_S
             and s['end'] <= end + TAIL_WINDOW_S + EDGE_SLACK_S]
    after = [dict(s, label=f'A{i}') for i, s in enumerate(after[:TAIL_MAX_SENTENCES], 1)]
    return before, after


def _sentence_line(sentence: dict) -> str:
    span = sentence['end'] - sentence['start']
    text = (f"({span:.0f} s of audio, transcript unreliable)" if sentence['sparse']
            else sentence['text'])
    return f"{sentence['label']} [{sentence['start']:.1f}-{sentence['end']:.1f}] {text}"


def format_edge_check(before: list[dict], after: list[dict], splices, start: float,
                      end: float) -> str:
    """The EDGE CHECK block for the reviewer's user prompt; '' when there is nothing to check."""
    if not before and not after:
        return ''
    lines = ["EDGE CHECK (sentences just outside the candidate; the closest ones touch its edges):"]
    if before:
        lines.append("Before the start (B1 is closest):")
        marks = [(t0, t1) for t0, t1 in splices if start - LEAD_IN_WINDOW_S <= t1 <= start + 1.0]
        lines.extend(_sentence_line(s) for s in before)
        lines.extend(f"[{t0:.1f}-{t1:.1f}] (digital silence: likely an ad insertion point)"
                     for t0, t1 in marks)
    if after:
        lines.append("After the end (A1 is closest):")
        marks = [(t0, t1) for t0, t1 in splices if end - 1.0 <= t0 <= end + TAIL_WINDOW_S]
        lines.extend(_sentence_line(s) for s in after)
        lines.extend(f"[{t0:.1f}-{t1:.1f}] (digital silence: likely an ad insertion point)"
                     for t0, t1 in marks)
    lines.append(
        'Add "lead_in_from" to your answer: the label of the EARLIEST B sentence that belongs '
        'to the same promotional read (a host lead-in that sets up the sponsor without naming it, '
        'a story that turns into the pitch), or null. Add "tail_to": the label of the LAST A '
        'sentence that belongs to it (call to action, URL or code repeat, sponsor sign-off), or '
        'null. The chosen sentences must run without a gap from the edge; answer null at the '
        'first sentence that is show content, a different speaker or a new topic. '
        'The labels belong only in those two fields. In your written explanation use plain '
        'words: describe a sentence by what it says or by its time, call a promotion of the '
        'host\'s own show "the host\'s own promotion" and an advertiser\'s read a "sponsor '
        'read", and do not write sentence labels, category names with underscores or any '
        'step names.')
    return '\n'.join(lines) + '\n'
