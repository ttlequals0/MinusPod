import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  clampSeek, currentChapterIndex, nextChapterTarget, nextRate, parseChapters, prevChapterTarget, validRate,
} from './useEpisodePlayer';

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

describe('validRate', () => {
  it('accepts presets and falls back to 1x otherwise', () => {
    expect(validRate(1.5)).toBe(1.5);
    expect(validRate(0.8)).toBe(0.8);
    expect(validRate(9)).toBe(1);
    expect(validRate('abc')).toBe(1);
    expect(validRate(null)).toBe(1);
  });
});

const prev = (t: number, list = chapters) => prevChapterTarget(list, currentChapterIndex(list, t), t);
const next = (t: number, list = chapters) => nextChapterTarget(list, currentChapterIndex(list, t));

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
    expect(prev(104)).toBe(100);
  });

  it('previous goes to the earlier chapter within 3 s', () => {
    expect(prev(103)).toBe(0);
    expect(prev(100)).toBe(0);
  });

  it('previous is unavailable within 3 s of the first chapter', () => {
    expect(prev(2)).toBeNull();
    expect(prev(4)).toBe(0);
    expect(prev(4, [])).toBeNull();
  });

  it('next goes to the following chapter and ends at the last', () => {
    expect(next(50)).toBe(100);
    expect(next(120)).toBe(250.5);
    expect(next(300)).toBeNull();
    expect(next(0, [])).toBeNull();
  });

  it('next from before the first chapter goes to the first', () => {
    expect(next(2, [{ startTime: 10, title: 'A' }])).toBe(10);
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
