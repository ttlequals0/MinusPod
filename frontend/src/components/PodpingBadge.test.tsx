/**
 * PodpingBadge renders the three podping coverage states, and nothing at all
 * when the listener is disabled.
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

  it('shows the timestamp when a podping was received', () => {
    render(<PodpingBadge coverage="received" lastPodpingAt="2026-07-20T12:00:00Z" />);
    expect(screen.getByText(/^Last podping:/)).toBeDefined();
  });

  it('says the host pings when this feed has not', () => {
    render(<PodpingBadge coverage="host_active" lastPodpingAt={null} />);
    expect(screen.getByText('Podping: host sends, none for this feed yet')).toBeDefined();
  });

  it('says polling when the host is not seen pinging', () => {
    render(<PodpingBadge coverage="unseen" lastPodpingAt={null} />);
    expect(screen.getByText('Podping: not seen from this host')).toBeDefined();
  });

  it('shortens the copy in compact mode', () => {
    render(<PodpingBadge coverage="host_active" lastPodpingAt={null} compact />);
    expect(screen.getByText('Podping host')).toBeDefined();
  });

  it('falls back when received has no timestamp', () => {
    render(<PodpingBadge coverage="received" lastPodpingAt={null} />);
    expect(screen.getByText('Podping: received')).toBeDefined();
  });

  it('keeps the timestamp reachable on hover in compact mode', () => {
    render(<PodpingBadge coverage="received" lastPodpingAt="2026-07-20T12:00:00Z" compact />);
    expect(screen.getByText('Podping').getAttribute('title')).toContain('Last podping:');
  });
});
