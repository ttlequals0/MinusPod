import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ScopeBadge } from './ScopeBadge';
import { CommunityBadge } from './CommunityBadge';

const network = { scope: 'network' as const, network_id: 'example-network-with-a-long-name', podcast_name: null };

describe('ScopeBadge', () => {
  it('truncates the text in an inner span and keeps the full text in the title', () => {
    render(<ScopeBadge pattern={network} truncate />);
    const badge = screen.getByTitle('Network: example-network-with-a-long-name');
    expect(badge.className).toContain('max-w-full');
    expect(badge.firstElementChild?.className).toContain('truncate');
  });

  it('wraps instead of truncating when truncation is not requested', () => {
    render(<ScopeBadge pattern={network} />);
    const badge = screen.getByTitle('Network: example-network-with-a-long-name');
    expect(badge.className).toContain('whitespace-normal!');
    expect(badge.querySelector('.truncate')).toBeNull();
  });

  it('titles the global and podcast variants', () => {
    render(<><ScopeBadge pattern={{ scope: 'global', network_id: null, podcast_name: null }} truncate /><ScopeBadge pattern={{ scope: 'podcast', network_id: null, podcast_name: 'Example Podcast' }} truncate /></>);
    expect(screen.getByTitle('Global')).toBeDefined();
    expect(screen.getByTitle('Example Podcast')).toBeDefined();
  });
});

describe('CommunityBadge', () => {
  it('titles the full text and truncates it in an inner span', () => {
    render(<CommunityBadge communityId="abc-123" version={3} protected />);
    const button = screen.getByTitle('Community v3 - abc - protected');
    expect(button.firstElementChild?.className).toContain('truncate');
  });
});
