import { describe, it, expect } from 'vitest';
import { formatDurationHoursMinutes, formatDurationPrecise, formatDurationWhole, keyLabel, formatPercent, formatElapsed } from './format';

// Characterization tests: outputs captured from the three formatters' prior
// per-component copies (EpisodeList, HistoryPage, ProcessingJobProgress)
// before they were consolidated here.

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

describe('formatDurationPrecise', () => {
  it.each([
    [0, '0.0s'],
    [null, '-'],
    [undefined, '-'],
    [59.4, '59.4s'],
    [60, '1m 0s'],
    [3599, '59m 59s'],
    [3600, '60m 0s'],
    [90061, '1501m 1s'],
  ])('formats %s as %s', (seconds, expected) => {
    expect(formatDurationPrecise(seconds)).toBe(expected);
  });
});

describe('formatDurationWhole', () => {
  it.each([
    [0, '0s'],
    [null, '0s'],
    [undefined, '0s'],
    [59.4, '59s'],
    [60, '1m 0s'],
    [3599, '59m 59s'],
    [3600, '60m 0s'],
    [90061, '1501m 1s'],
  ])('formats %s as %s', (seconds, expected) => {
    expect(formatDurationWhole(seconds as unknown as number)).toBe(expected);
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
    expect(formatElapsed(3725)).toBe('1h 2m 5s');
  });
  it('floors fractions and clamps bad input to 0s', () => {
    expect(formatElapsed(59.9)).toBe('59s');
    expect(formatElapsed(-3)).toBe('0s');
    expect(formatElapsed(NaN)).toBe('0s');
  });
});
