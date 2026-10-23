import { describe, it, expect } from 'vitest';
import { gapThreshold, groupParagraphs } from './transcriptParagraphs';

// Builds segments from [duration, gapAfter] pairs.
function build(spec: Array<[number, number, string?]>) {
  let t = 0;
  return spec.map(([dur, gapBefore, text]) => {
    const start = t + gapBefore;
    t = start + dur;
    return { start, end: t, text: text ?? 'word' };
  });
}

describe('gapThreshold', () => {
  it('uses 0.5 s with fewer than 4 gaps', () => {
    expect(gapThreshold(build([[1, 0], [1, 2], [1, 2], [1, 2]]))).toBe(0.5);
    expect(gapThreshold([])).toBe(0.5);
  });

  it('clamps a zero median up to 0.25 s', () => {
    expect(gapThreshold(build(Array.from({ length: 10 }, () => [1, 0] as [number, number])))).toBe(0.25);
  });

  it('clamps a large median down to 1.0 s', () => {
    expect(gapThreshold(build(Array.from({ length: 10 }, () => [1, 3] as [number, number])))).toBe(1);
  });

  it('takes the median after dropping gaps above the 90th percentile', () => {
    // gaps: 0.4 x8, 0.6, 50 -> p90 ~ 0.6 keeps 0.4 x8 and 0.6, median 0.4
    const segs = build([[1, 0], ...Array.from({ length: 8 }, () => [1, 0.4] as [number, number]), [1, 0.6], [1, 50]]);
    expect(gapThreshold(segs)).toBeCloseTo(0.4, 5);
  });
});

describe('groupParagraphs', () => {
  it('returns nothing for no segments', () => {
    expect(groupParagraphs([])).toEqual([]);
  });

  it('breaks when the gap exceeds the threshold', () => {
    const segs = build([[1, 0], [1, 0.1], [1, 2], [1, 0.1]]);
    const p = groupParagraphs(segs, 0.5);
    expect(p.map((x) => x.segments.length)).toEqual([2, 2]);
    expect(p[1].start).toBe(segs[2].start);
  });

  it('does not break on a sentence end before the target length', () => {
    const segs = build([[1, 0, 'Done.'], [1, 0.2, 'Next'], [1, 0, 'said "yes."'], [1, 0, 'tail']]);
    expect(groupParagraphs(segs, 0.5)).toHaveLength(1);
  });

  it('breaks at the first sentence end once the target length is reached', () => {
    const segs = build(Array.from({ length: 30 }, (_, i) => [1, 0, i === 22 ? 'end here.' : 'words'] as [number, number, string]));
    const p = groupParagraphs(segs, 0.5);
    expect(p[0].segments).toHaveLength(23);
    expect(p[0].segments[22].text).toBe('end here.');
  });

  it('never runs past the hard cap even without a sentence end', () => {
    const segs = build(Array.from({ length: 100 }, () => [1, 0, 'words'] as [number, number, string]));
    const p = groupParagraphs(segs, 0.5);
    expect(p.length).toBeGreaterThan(1);
    for (const para of p) expect(para.end - para.start).toBeLessThanOrEqual(45);
  });

  it('keeps every member segment unchanged and in order', () => {
    const segs = build([[1, 0], [1, 0.1], [1, 2], [1, 0.1], [1, 0.1]]);
    const p = groupParagraphs(segs);
    expect(p.flatMap((x) => x.segments)).toEqual(segs);
    expect(p[0].segments[0]).toBe(segs[0]);
    expect(p[0].end).toBe(p[0].segments[p[0].segments.length - 1].end);
  });
});
