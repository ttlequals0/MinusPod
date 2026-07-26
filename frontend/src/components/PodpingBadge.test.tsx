/**
 * PodpingBadge shows the last ping time when one has arrived and "none"
 * otherwise. It renders nothing at all when the listener is disabled.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import PodpingBadge from './PodpingBadge';

describe('PodpingBadge', () => {
  it('renders nothing when the listener is off', () => {
    const { container } = render(<PodpingBadge coverage={null} lastPodpingAt={null} />);
    expect(container.innerHTML).toBe('');
  });

  it('renders nothing when coverage is absent', () => {
    const { container } = render(<PodpingBadge coverage={undefined} lastPodpingAt={null} />);
    expect(container.innerHTML).toBe('');
  });

  it('shows the last ping time when one arrived', () => {
    render(<PodpingBadge coverage="received" lastPodpingAt="2026-07-20T12:00:00Z" />);
    expect(screen.getByText(/^Podping: last ping at /)).toBeDefined();
  });

  // The API reports finer states; the UI deliberately collapses them all to
  // "none" so the line stays a simple yes-or-no.
  it.each(['declared', 'host_active', 'unseen', 'declined'] as const)(
    'says none for the %s state', (coverage) => {
      render(<PodpingBadge coverage={coverage} lastPodpingAt={null} />);
      expect(screen.getByText('Podping: none')).toBeDefined();
    });

  it('says none when received but the timestamp is missing', () => {
    render(<PodpingBadge coverage="received" lastPodpingAt={null} />);
    expect(screen.getByText('Podping: none')).toBeDefined();
  });

  it('uses the short date in compact mode', () => {
    render(<PodpingBadge coverage="received" lastPodpingAt="2026-07-20T12:00:00Z" compact />);
    expect(screen.getByText(/^Podping: \d/)).toBeDefined();
  });

  it('says none in compact mode too', () => {
    render(<PodpingBadge coverage="unseen" lastPodpingAt={null} compact />);
    expect(screen.getByText('Podping: none')).toBeDefined();
  });

  it('explains the schedule fallback on hover when there is no ping', () => {
    render(<PodpingBadge coverage="unseen" lastPodpingAt={null} />);
    expect(screen.getByText('Podping: none').getAttribute('title'))
      .toContain('refresh schedule');
  });
});
