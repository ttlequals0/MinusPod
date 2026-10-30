import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  RATE_STORAGE_KEY, clampSeek, currentChapterIndex, loadRate, nextChapterTarget, nextRate,
  parseChapters, prevChapterTarget, saveRate,
} from './useEpisodePlayer';
import { formatClock } from '../utils/format';

const chapters = [
  { startTime: 0, title: 'Intro' },
  { startTime: 100, title: 'Middle' },
  { startTime: 250.5, title: 'End' },
];

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
});

describe('nextRate', () => {
  it('cycles through every preset and back to 1x', () => {
    const seen = [1];
    for (let i = 0; i < 6; i++) seen.push(nextRate(seen[seen.length - 1]));
    expect(seen).toEqual([1, 1.25, 1.5, 1.75, 2, 0.8, 1]);
  });

  it('restarts at 1x for an unknown rate', () => {
    expect(nextRate(3)).toBe(1);
  });
});

describe('rate persistence', () => {
  it('defaults to 1x and round-trips a saved rate', () => {
    expect(loadRate()).toBe(1);
    saveRate(1.5);
    expect(localStorage.getItem(RATE_STORAGE_KEY)).toBe('1.5');
    expect(loadRate()).toBe(1.5);
  });

  it('ignores a stored value outside the presets', () => {
    localStorage.setItem(RATE_STORAGE_KEY, '9');
    expect(loadRate()).toBe(1);
    localStorage.setItem(RATE_STORAGE_KEY, 'abc');
    expect(loadRate()).toBe(1);
  });

  it('falls back to 1x when storage throws', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
    expect(loadRate()).toBe(1);
    expect(() => saveRate(2)).not.toThrow();
  });
});

describe('formatClock', () => {
  it.each([
    [0, '0:00'],
    [5.9, '0:05'],
    [65, '1:05'],
    [3599, '59:59'],
    [3600, '1:00:00'],
    [3725, '1:02:05'],
    [-4, '0:00'],
    [NaN, '0:00'],
    [Infinity, '0:00'],
  ])('formats %s as %s', (s, expected) => {
    expect(formatClock(s)).toBe(expected);
  });
});

describe('clampSeek', () => {
  it('clamps to 0 and duration', () => {
    expect(clampSeek(-15, 600)).toBe(0);
    expect(clampSeek(900, 600)).toBe(600);
    expect(clampSeek(300, 600)).toBe(300);
  });

  it('only clamps at 0 when duration is unknown', () => {
    expect(clampSeek(900, 0)).toBe(900);
    expect(clampSeek(-1, 0)).toBe(0);
  });
});

describe('currentChapterIndex', () => {
  it('picks the last chapter at or before the playhead', () => {
    expect(currentChapterIndex(chapters, 0)).toBe(0);
    expect(currentChapterIndex(chapters, 99.9)).toBe(0);
    expect(currentChapterIndex(chapters, 100)).toBe(1);
    expect(currentChapterIndex(chapters, 9999)).toBe(2);
  });

  it('is -1 before the first chapter and for no chapters', () => {
    expect(currentChapterIndex([{ startTime: 10, title: 'A' }], 5)).toBe(-1);
    expect(currentChapterIndex([], 5)).toBe(-1);
  });
});

describe('chapter navigation', () => {
  it('previous restarts the current chapter after 3 s', () => {
    expect(prevChapterTarget(chapters, 104)).toBe(100);
  });

  it('previous goes to the earlier chapter within 3 s', () => {
    expect(prevChapterTarget(chapters, 103)).toBe(0);
    expect(prevChapterTarget(chapters, 100)).toBe(0);
  });

  it('previous is unavailable within 3 s of the first chapter', () => {
    expect(prevChapterTarget(chapters, 2)).toBeNull();
    expect(prevChapterTarget(chapters, 4)).toBe(0);
    expect(prevChapterTarget([], 4)).toBeNull();
  });

  it('next goes to the following chapter and ends at the last', () => {
    expect(nextChapterTarget(chapters, 50)).toBe(100);
    expect(nextChapterTarget(chapters, 120)).toBe(250.5);
    expect(nextChapterTarget(chapters, 300)).toBeNull();
    expect(nextChapterTarget([], 0)).toBeNull();
  });

  it('next from before the first chapter goes to the first', () => {
    expect(nextChapterTarget([{ startTime: 10, title: 'A' }], 2)).toBe(10);
  });
});

describe('parseChapters', () => {
  it('keeps valid entries sorted by start time', () => {
    const json = { version: '1.2.0', chapters: [{ startTime: 30, title: 'B', img: 'x' }, { startTime: 0, title: 'A' }, { startTime: 'x', title: 'bad' }, { title: 'none' }] };
    expect(parseChapters(json)).toEqual([{ startTime: 0, title: 'A' }, { startTime: 30, title: 'B' }]);
  });

  it('returns an empty list for malformed input', () => {
    expect(parseChapters(null)).toEqual([]);
    expect(parseChapters({})).toEqual([]);
    expect(parseChapters({ chapters: 'no' })).toEqual([]);
  });
});
