/**
 * Component tests for the dashboard per-feed status summary pills (#466).
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import FeedStatusSummary from './FeedStatusSummary';
import type { EpisodeStatusCounts } from '../api/types';

function makeCounts(overrides: Partial<EpisodeStatusCounts> = {}): EpisodeStatusCounts {
  return {
    discovered: 0,
    pending: 0,
    processing: 0,
    completed: 0,
    failed: 0,
    permanently_failed: 0,
    deferred: 0,
    ...overrides,
  };
}

describe('FeedStatusSummary', () => {
  it('renders a pill per non-zero status with count and short label', () => {
    render(<FeedStatusSummary counts={makeCounts({ discovered: 10, completed: 4, failed: 2 })} />);
    expect(screen.getByText('10 Disc')).toBeDefined();
    expect(screen.getByText('4 Comp')).toBeDefined();
    expect(screen.getByText('2 Fail')).toBeDefined();
  });

  it('skips zero-count statuses', () => {
    render(<FeedStatusSummary counts={makeCounts({ completed: 4 })} />);
    expect(screen.queryByText(/Disc/)).toBeNull();
    expect(screen.queryByText(/Pend/)).toBeNull();
  });

  it('renders nothing when counts are missing or all zero', () => {
    const { container, rerender } = render(<FeedStatusSummary />);
    expect(container.firstChild).toBeNull();
    rerender(<FeedStatusSummary counts={makeCounts()} />);
    expect(container.firstChild).toBeNull();
  });

  it('uses the shared badge color classes', () => {
    render(<FeedStatusSummary counts={makeCounts({ completed: 1 })} />);
    const pill = screen.getByText('1 Comp');
    expect(pill.className).toContain('bg-green-500/20');
  });
});
