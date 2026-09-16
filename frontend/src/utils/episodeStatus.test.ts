import { describe, it, expect } from 'vitest';
import {
  displayStatusColor,
  displayStatusLabel,
  EPISODE_STATUS_COLORS,
  EPISODE_STATUS_LABELS,
} from './episodeStatus';

describe('displayStatusLabel', () => {
  it('returns "queued" when jobState is queued, regardless of the underlying status', () => {
    expect(displayStatusLabel('pending', 'queued')).toBe('queued');
    expect(displayStatusLabel('processing', 'queued')).toBe('queued');
  });

  it('returns the mapped status label when jobState is not queued', () => {
    expect(displayStatusLabel('pending', 'idle')).toBe('pending');
    expect(displayStatusLabel('pending', undefined)).toBe('pending');
    expect(displayStatusLabel('completed')).toBe('completed');
    expect(displayStatusLabel('permanently_failed')).toBe('permanently failed');
  });

  it('falls back to the raw status for an unknown status', () => {
    expect(displayStatusLabel('unknown_status')).toBe('unknown_status');
  });
});

describe('displayStatusColor', () => {
  it('uses the queued color when jobState is queued', () => {
    expect(displayStatusColor('pending', 'queued')).toBe(EPISODE_STATUS_COLORS.queued);
  });

  it('uses the status color otherwise', () => {
    expect(displayStatusColor('pending', 'idle')).toBe(EPISODE_STATUS_COLORS.pending);
  });
});

describe('EPISODE_STATUS_LABELS', () => {
  it('has a distinct "queued" entry that does not overload "pending"', () => {
    expect(EPISODE_STATUS_LABELS.queued).toBe('queued');
    expect(EPISODE_STATUS_LABELS.pending).toBe('pending');
  });
});
