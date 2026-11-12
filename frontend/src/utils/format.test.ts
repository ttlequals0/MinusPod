import { describe, it, expect } from 'vitest';
import { formatDurationHoursMinutes, keyLabel, formatPercent, formatElapsed, sentenceCase, cacheHit, formatTimestamp } from './format';

describe('formatDurationHoursMinutes', () => {
  it.each([
    [0, ''],
    [undefined, ''],
    [59.4, '0m'],
    [60, '1m'],
    [3599, '59m'],
    [3600, '1h 0m'],
    [90061, '25h 1m'],
  ])('formats %s as %s', (seconds, expected) => {
    expect(formatDurationHoursMinutes(seconds)).toBe(expected);
  });

  it('formats null as empty string', () => {
    expect(formatDurationHoursMinutes(null as unknown as undefined)).toBe('');
  });
});

describe('keyLabel', () => {
  it('falls back for an empty label', () => {
    expect(keyLabel({ label: '' })).toBe('Unlabeled subscriber');
    expect(keyLabel({ label: 'Kitchen' })).toBe('Kitchen');
  });
});

describe('formatPercent', () => {
  it('treats values <= 1 as fractions', () => {
    expect(formatPercent(0.99)).toBe('99%');
    expect(formatPercent(0)).toBe('0%');
    expect(formatPercent(1)).toBe('100%');
  });
  it('accepts 0..100 values and rounds to an integer', () => {
    expect(formatPercent(99)).toBe('99%');
    expect(formatPercent(87.6)).toBe('88%');
  });
});

describe('formatElapsed', () => {
  it('formats seconds, minutes, and hours', () => {
    expect(formatElapsed(45)).toBe('45s');
    expect(formatElapsed(125)).toBe('2m 5s');
    expect(formatElapsed(3725)).toBe('1h 2m');
    expect(formatElapsed(7200)).toBe('2h');
    expect(formatElapsed(120)).toBe('2m');
  });
  it('rounds fractions and clamps bad input to 0s', () => {
    expect(formatElapsed(59.9)).toBe('1m');
    expect(formatElapsed(59.4)).toBe('59s');
    expect(formatElapsed(-3)).toBe('0s');
    expect(formatElapsed(NaN)).toBe('0s');
  });
});

describe('sentenceCase', () => {
  it('upper-cases the first letter only', () => {
    expect(sentenceCase('queued (offline)')).toBe('Queued (offline)');
    expect(sentenceCase('')).toBe('');
  });
});

describe('cacheHit', () => {
  it('reports reads over input as a whole percent', () => {
    const hit = cacheHit(800, 100, 1000);
    expect(hit.text).toBe('80%');
    expect(hit.title).toMatch(/^800 read, 100 write of /);
  });
  it('caps the rate at 100% when reads exceed input', () => {
    expect(cacheHit(1500, 0, 1000).text).toBe('100%');
  });
  it('is blank without input or without cache traffic', () => {
    expect(cacheHit(10, 0, 0)).toEqual({ text: '' });
    expect(cacheHit(0, 0, 1000)).toEqual({ text: '' });
  });
});

describe('formatTimestamp', () => {
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
    expect(formatTimestamp(s)).toBe(expected);
  });
});
