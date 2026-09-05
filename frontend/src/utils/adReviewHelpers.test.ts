import { describe, it, expect } from 'vitest';
import { parseTimeInput } from './adReviewHelpers';

describe('parseTimeInput', () => {
  it.each(['90', '1:30', '1:01:05', '12:05.5'])('parses %s', (input) => {
    expect(parseTimeInput(input)).not.toBeNull();
  });

  it.each(['1:60', '2:75', '1:', ':', '0x10', '1.5:30'])('rejects %s', (input) => {
    expect(parseTimeInput(input)).toBeNull();
  });

  it('computes seconds correctly for each accepted form', () => {
    expect(parseTimeInput('90')).toBe(90);
    expect(parseTimeInput('1:30')).toBe(90);
    expect(parseTimeInput('1:01:05')).toBe(3665);
    expect(parseTimeInput('12:05.5')).toBe(725.5);
  });
});
