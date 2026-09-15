import { describe, expect, it } from 'vitest';
import type { OriginalSegment } from '../api/feeds';
import type { AdSegment, EpisodeDetail } from '../api/types';
import {
  boundsFromSelectedIndexes,
  buildSegmentReviewRows,
  contiguousIndexGroups,
  ensureMinTextTemplate,
  findOverlappingMarker,
  overlaps,
  rangeIndexes,
  selectionOverlapsKept,
  snapToWords,
} from './segmentReviewModel';

const segments: OriginalSegment[] = [
  { start: 0, end: 5, text: 'Hello world this is content.', words: [
    { word: 'Hello', start: 0, end: 0.5 },
    { word: 'world', start: 0.5, end: 1 },
  ] },
  { start: 5, end: 12, text: 'Squarespace dot com slash show promo here today.', words: [
    { word: 'Squarespace', start: 5, end: 6 },
    { word: 'promo', start: 10, end: 11 },
  ] },
  { start: 12, end: 20, text: 'Back to the show after the break.', words: [] },
];

const baseEpisode: Pick<
  EpisodeDetail,
  'adMarkers' | 'pendingReviewMarkers' | 'rejectedAdMarkers' | 'keptMarkers' | 'corrections'
> = {
  adMarkers: [{
    start: 5,
    end: 12,
    confidence: 0.9,
    reason: 'Squarespace',
  }],
  pendingReviewMarkers: [],
  rejectedAdMarkers: [],
  keptMarkers: [],
  corrections: [],
};

describe('segmentReviewModel', () => {
  it('overlaps detects partial overlap', () => {
    expect(overlaps(4, 6, { start: 5, end: 12 })).toBe(true);
    expect(overlaps(12, 14, { start: 5, end: 12 })).toBe(false);
  });

  it('buildSegmentReviewRows labels cut and content segments', () => {
    const rows = buildSegmentReviewRows(segments, baseEpisode, [
      { start: 5, end: 12 },
    ]);
    expect(rows[0].label).toBe('content');
    expect(rows[1].label).toBe('cut');
    expect(rows[2].label).toBe('content');
  });

  it('false positive corrections relabel as content', () => {
    const rows = buildSegmentReviewRows(segments, {
      ...baseEpisode,
      corrections: [{
        id: 1,
        correction_type: 'false_positive',
        original_bounds: { start: 5, end: 12 },
        created_at: '2026-01-01T00:00:00Z',
      }],
    }, [{ start: 5, end: 12 }]);
    expect(rows[1].label).toBe('content');
  });

  it('contiguousIndexGroups merges adjacent indexes', () => {
    const groups = contiguousIndexGroups(new Set([0, 1, 3]), segments);
    expect(groups).toEqual([[0, 1], [3]]);
  });

  it('contiguousIndexGroups splits consecutive indexes across large time gaps', () => {
    const gapped: OriginalSegment[] = [
      { start: 0, end: 5, text: 'One' },
      { start: 5, end: 10, text: 'Two' },
      { start: 12.5, end: 18, text: 'Three' },
    ];
    const groups = contiguousIndexGroups(new Set([0, 1, 2]), gapped);
    expect(groups).toEqual([[0, 1], [2]]);
  });

  it('rangeIndexes builds inclusive range', () => {
    expect([...rangeIndexes(2, 4)]).toEqual([2, 3, 4]);
  });

  it('snapToWords snaps to nearest word boundaries', () => {
    const snapped = snapToWords(5.1, 11.05, segments);
    expect(snapped.start).toBe(5);
    expect(snapped.end).toBe(11);
  });

  it('boundsFromSelectedIndexes returns snapped groups', () => {
    const rows = buildSegmentReviewRows(segments, baseEpisode);
    const bounds = boundsFromSelectedIndexes(new Set([1]), rows, segments);
    expect(bounds).toHaveLength(1);
    expect(bounds[0].start).toBe(5);
    expect(bounds[0].text).toContain('Squarespace');
  });

  it('ensureMinTextTemplate pads short text from adjacent segments', () => {
    const text = ensureMinTextTemplate('short', segments, 5, 12);
    expect(text.length).toBeGreaterThanOrEqual(50);
  });

  it('findOverlappingMarker matches overlapping ad marker', () => {
    const marker: AdSegment = { start: 5, end: 12, confidence: 0.8 };
    expect(findOverlappingMarker(6, 10, [marker])?.start).toBe(5);
  });

  it('selectionOverlapsKept detects kept overlap', () => {
    const kept: AdSegment = { start: 1, end: 4, confidence: 1, actionApplied: 'keep' };
    expect(selectionOverlapsKept({ start: 2, end: 3 }, [kept])).toBe(true);
  });
});
