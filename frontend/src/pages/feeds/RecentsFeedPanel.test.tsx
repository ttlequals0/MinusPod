import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { Feed } from '../../api/types';
import RecentsFeedPanel from './RecentsFeedPanel';

vi.mock('../../api/feeds', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../api/feeds')>()),
  updateFeed: vi.fn(),
  uploadFeedArtwork: vi.fn(),
}));

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const feed = {
    slug: 'recents',
    title: 'Recents',
    description: 'Running list of new episodes.',
    createdAt: '2026-09-07T00:00:00Z',
  } as unknown as Feed;
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter><RecentsFeedPanel feed={feed} slug="recents" /></MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('RecentsFeedPanel', () => {
  it('starts collapsed with the title visible', () => {
    localStorage.clear();
    renderPanel();
    const header = screen.getByRole('button', { name: /Recents feed/ });
    expect(header.getAttribute('aria-expanded')).toBe('false');
    expect(screen.queryByLabelText('Feed title')).toBeNull();
  });
});
