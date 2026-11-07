import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import FeedCard from './FeedCard';
import FeedListItem from './FeedListItem';
import { DEMO_WRITE_TOOLTIP } from '../demo/copy';
import type { Feed } from '../api/types';

vi.mock('../demo/flag', () => ({ IS_DEMO: true }));

afterEach(cleanup);

const feed: Feed = {
  slug: 'example-podcast',
  title: 'Example Podcast',
  sourceUrl: 'https://example.com/feed.xml',
  feedUrl: 'https://example.com/feed.xml',
  episodeCount: 3,
};

describe.each([
  ['FeedCard', FeedCard],
  ['FeedListItem', FeedListItem],
])('%s in demo', (_name, Component) => {
  it('disables delete and refresh with the write tooltip', () => {
    render(
      <MemoryRouter>
        <Component feed={feed} onRefresh={vi.fn()} onDelete={vi.fn()} />
      </MemoryRouter>,
    );
    const del = screen.getByRole('button', { name: 'Delete feed' }) as HTMLButtonElement;
    expect(del.disabled).toBe(true);
    expect(del.parentElement?.getAttribute('title')).toBe(DEMO_WRITE_TOOLTIP);

    const refresh = screen.getByRole('button', { name: /refresh/i }) as HTMLButtonElement;
    expect(refresh.disabled).toBe(true);
    expect(refresh.parentElement?.parentElement?.getAttribute('title')).toBe(DEMO_WRITE_TOOLTIP);
  });
});
