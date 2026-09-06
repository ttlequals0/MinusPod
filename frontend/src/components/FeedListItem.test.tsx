// Cover art links to the feed (#718) without adding a second focus stop or
// a second accessible name, matching FeedCard's grid equivalent.
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import FeedListItem from './FeedListItem';
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

function renderItem(feed: Feed) {
  return render(
    <MemoryRouter>
      <FeedListItem feed={feed} onRefresh={vi.fn()} onDelete={vi.fn()} />
    </MemoryRouter>,
  );
}

describe('FeedListItem artwork link', () => {
  it('wraps the artwork in a link to the feed that is hidden from keyboard and a11y tree', () => {
    renderItem(makeFeed());
    const links = screen.getAllByRole('link', { hidden: true }).filter(
      (el) => el.getAttribute('href') === '/feeds/example-podcast',
    );
    const artworkLink = links.find((el) => el.getAttribute('aria-hidden') === 'true');
    expect(artworkLink).toBeDefined();
    expect(artworkLink?.getAttribute('tabindex')).toBe('-1');
  });

  it('keeps exactly one accessible link to the feed destination', () => {
    renderItem(makeFeed());
    const links = screen.getAllByRole('link', { name: 'Example Podcast' });
    expect(links).toHaveLength(1);
  });
});
