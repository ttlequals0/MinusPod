// Cover art links to the feed (#718) without adding a second focus stop or
// a second accessible name.
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import FeedCard from './FeedCard';
import type { Feed } from '../api/types';

function makeFeed(overrides: Partial<Feed> = {}): Feed {
  return {
    slug: 'example-podcast',
    title: 'Example Podcast',
    sourceUrl: 'https://example.com/feed.xml',
    feedUrl: 'https://example.com/feed.xml',
    episodeCount: 3,
    ...overrides,
  };
}

function renderCard(feed: Feed) {
  return render(
    <MemoryRouter>
      <FeedCard feed={feed} onRefresh={vi.fn()} onDelete={vi.fn()} />
    </MemoryRouter>,
  );
}

describe('FeedCard artwork link', () => {
  it('wraps the artwork in a link to the feed that is hidden from keyboard and a11y tree', () => {
    renderCard(makeFeed());
    const links = screen.getAllByRole('link', { hidden: true }).filter(
      (el) => el.getAttribute('href') === '/feeds/example-podcast',
    );
    const artworkLink = links.find((el) => el.getAttribute('aria-hidden') === 'true');
    expect(artworkLink).toBeDefined();
    expect(artworkLink?.getAttribute('tabindex')).toBe('-1');
  });

  it('keeps exactly one accessible link to the feed destination', () => {
    renderCard(makeFeed());
    const links = screen.getAllByRole('link', { name: 'Example Podcast' });
    expect(links).toHaveLength(1);
  });
});

describe('FeedCard footer', () => {
  it('renders the action row as the last child of the root regardless of statusCounts', () => {
    const withCounts = renderCard(makeFeed({
      statusCounts: {
        discovered: 0, pending: 0, processing: 0, completed: 4,
        failed: 0, permanently_failed: 0, deferred: 0,
      },
    }));
    const withoutCounts = renderCard(makeFeed({ statusCounts: undefined }));

    for (const { container } of [withCounts, withoutCounts]) {
      const root = container.firstChild as HTMLElement;
      const footer = root.lastElementChild as HTMLElement;
      expect(footer.textContent).toContain('Copy URL');
    }
  });
});

describe('FeedCard fixed slots', () => {
  it('shows "Not refreshed yet" when the feed has never been refreshed', () => {
    renderCard(makeFeed({
      lastRefreshed: undefined, podpingCoverage: null, statusCounts: undefined,
    }));
    expect(screen.getByText('Not refreshed yet')).toBeDefined();
  });

  it('exposes the refresh failure without a hover tooltip', () => {
    renderCard(makeFeed({
      lastRefreshed: '2026-09-06T00:00:00Z', lastRefreshError: 'boom',
    }));
    const warning = screen.getByText('Refresh failed');
    expect(warning.tagName).toBe('SUMMARY');
    expect(warning.closest('details')?.textContent).toContain('boom');
  });
});

describe('FeedCard awaiting approval', () => {
  it('shows the count when episodes await approval', () => {
    renderCard(makeFeed({ awaitingApprovalCount: 3 }));
    expect(screen.getByText('3 awaiting approval')).toBeTruthy();
  });

  it('shows nothing when the count is zero', () => {
    renderCard(makeFeed({ awaitingApprovalCount: 0 }));
    expect(screen.queryByText(/awaiting approval/)).toBeNull();
  });
});

describe('FeedCard processed bar and buttons', () => {
  it('shows completed of total as a progressbar', () => {
    renderCard(makeFeed({
      episodeCount: 10,
      statusCounts: {
        discovered: 0, pending: 0, processing: 0, completed: 4,
        failed: 0, permanently_failed: 0, deferred: 0,
      },
    }));
    expect(screen.getByRole('progressbar', { name: 'Episodes processed' }).getAttribute('aria-valuenow')).toBe('4');
    expect(screen.getByText('4 of 10 processed')).toBeDefined();
  });

  it('renders the bar at zero when the feed has no episodes', () => {
    renderCard(makeFeed({ episodeCount: 0 }));
    expect(screen.getByText('0 of 0 processed')).toBeDefined();
    expect(screen.getByRole('progressbar', { name: 'Episodes processed' }).getAttribute('aria-valuenow')).toBe('0');
  });
});
