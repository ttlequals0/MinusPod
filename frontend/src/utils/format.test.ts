import { describe, it, expect } from 'vitest';
import { formatDurationHoursMinutes, formatDurationPrecise, formatDurationWhole } from './format';

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
