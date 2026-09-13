import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { Feed } from '../../api/types';
import RecentsFeedPanel from './RecentsFeedPanel';

vi.mock('../../api/feeds', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/feeds')>()),
  updateFeed: vi.fn(),
  uploadFeedArtwork: vi.fn(),
}));

function makeFeed(overrides: Partial<Feed> = {}) {
  return {
    slug: 'recents',
    title: 'Recents',
    description: 'Running list of new episodes.',
    createdAt: '2026-09-07T00:00:00Z',
    ...overrides,
  } as unknown as Feed;
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui = (feed: Feed) => (
    <QueryClientProvider client={client}>
      <MemoryRouter><RecentsFeedPanel feed={feed} slug="recents" /></MemoryRouter>
    </QueryClientProvider>
  );
  const view = render(ui(makeFeed()));
  return { ...view, rerenderWith: (feed: Feed) => view.rerender(ui(feed)) };
}

describe('RecentsFeedPanel', () => {
  it('starts collapsed with the title visible', () => {
    localStorage.clear();
    renderPanel();
    const header = screen.getByRole('button', { name: /Recents feed/ });
    expect(header.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByLabelText('Feed title')).toBeNull();
  });

  it('keeps an edited title through a background refetch', async () => {
    localStorage.clear();
    const user = userEvent.setup();
    const { rerenderWith } = renderPanel();
    await user.click(screen.getByRole('button', { name: /Recents feed/ }));
    const input = screen.getByLabelText('Feed title');
    await user.clear(input);
    await user.type(input, 'New episodes');

    rerenderWith(makeFeed());

    expect((screen.getByLabelText('Feed title') as HTMLInputElement).value)
      .toBe('New episodes');
  });
});
