// Cover art links to the feed (#718) without adding a second focus stop or
// a second accessible name; footer alignment stays a full-height flex column.
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

describe('FeedCard footer alignment', () => {
  it('stretches the root to full height with a flex column so the footer pins to the bottom', () => {
    const { container } = renderCard(makeFeed({ statusCounts: undefined }));
    const root = container.firstChild as HTMLElement;
    expect(root.className).toContain('h-full');
    expect(root.className).toContain('flex');
    expect(root.className).toContain('flex-col');
  });

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
      expect(footer.className).toContain('bg-secondary/50');
      expect(footer.textContent).toContain('Copy URL');
    }
  });
});
