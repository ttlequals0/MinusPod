import { describe, it, expect } from 'vitest';
import { formatClock, parseClock } from './transcriptTime';

describe('parseClock', () => {
  it('parses seconds, mm:ss and h:mm:ss', () => {
    expect(parseClock('90')).toBe(90);
    expect(parseClock('1:30')).toBe(90);
    expect(parseClock('1:01:05')).toBe(3665);
    expect(parseClock(' 12:05.5 ')).toBe(725.5);
  });

  it('rejects junk and blanks', () => {
    expect(parseClock('')).toBeNull();
    expect(parseClock('abc')).toBeNull();
    expect(parseClock('1:60')).toBeNull();
    expect(parseClock('1:2:3:4')).toBeNull();
  });
});

describe('formatClock', () => {
  it('renders whole seconds as m:ss or h:mm:ss', () => {
    expect(formatClock(0)).toBe('0:00');
    expect(formatClock(725.5)).toBe('12:05');
    expect(formatClock(3665)).toBe('1:01:05');
    expect(parseClock(formatClock(3665))).toBe(3665);
  });
});
