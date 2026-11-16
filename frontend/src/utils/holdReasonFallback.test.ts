import { describe, it, expect } from 'vitest';
import { HOLD_REASON_LABELS, HOLD_REASON_TITLES, holdReasonLabel, holdReasonTitle } from './holdReason';

describe('hold reason fallbacks', () => {
  it('returns the mapped label and title for a known reason', () => {
    expect(holdReasonLabel('max_duration')).toBe(HOLD_REASON_LABELS.max_duration);
    expect(holdReasonTitle('max_duration')).toBe(HOLD_REASON_TITLES.max_duration);
  });

  it('humanises an unknown reason', () => {
    expect(holdReasonLabel('some_new_reason')).toBe('Some new reason');
    expect(holdReasonTitle('some_new_reason')).toBe('Held for manual review');
  });

  it('falls back to Held when there is no reason', () => {
    expect(holdReasonLabel(undefined)).toBe('Held');
    expect(holdReasonLabel(null)).toBe('Held');
    expect(holdReasonLabel('')).toBe('Held');
    expect(holdReasonTitle(null)).toBe('Held for manual review');
  });
});
