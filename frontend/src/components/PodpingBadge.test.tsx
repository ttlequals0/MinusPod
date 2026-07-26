/**
 * PodpingBadge collapses the API's five coverage states into three lines: the
 * time of the last ping, a publisher opt-in with nothing received yet, or
 * never. It renders nothing at all when the listener is disabled.
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

  it('shows the opt-in when the feed declares podping but has no ping yet', () => {
    render(<PodpingBadge coverage="declared" lastPodpingAt={null} />);
    expect(screen.getByText('Podping: enabled, none received yet')).toBeDefined();
  });

  // host_active and unseen are host-level inference, and declined is an opt-out.
  // None of them tells the reader anything actionable about this feed.
  it.each(['host_active', 'unseen', 'declined'] as const)(
    'says never for the %s state', (coverage) => {
      render(<PodpingBadge coverage={coverage} lastPodpingAt={null} />);
      expect(screen.getByText('Podping: never')).toBeDefined();
    });

  it('says never when received but the timestamp is missing', () => {
    render(<PodpingBadge coverage="received" lastPodpingAt={null} />);
    expect(screen.getByText('Podping: never')).toBeDefined();
  });

  it('uses the short date in compact mode', () => {
    render(<PodpingBadge coverage="received" lastPodpingAt="2026-07-20T12:00:00Z" compact />);
    expect(screen.getByText(/^Podping: \d/)).toBeDefined();
  });

  it('shortens the declared copy in compact mode', () => {
    render(<PodpingBadge coverage="declared" lastPodpingAt={null} compact />);
    expect(screen.getByText('Podping: enabled')).toBeDefined();
  });

  it('says never in compact mode too', () => {
    render(<PodpingBadge coverage="unseen" lastPodpingAt={null} compact />);
    expect(screen.getByText('Podping: never')).toBeDefined();
  });

  it('explains the declared state on hover', () => {
    render(<PodpingBadge coverage="declared" lastPodpingAt={null} />);
    expect(screen.getByText('Podping: enabled, none received yet').getAttribute('title'))
      .toContain('declares that it uses Podping');
  });

  it('explains the schedule fallback on hover when there is no ping', () => {
    render(<PodpingBadge coverage="unseen" lastPodpingAt={null} />);
    expect(screen.getByText('Podping: never').getAttribute('title'))
      .toContain('refresh schedule');
  });
});
