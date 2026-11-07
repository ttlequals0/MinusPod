import { afterEach, describe, expect, test, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { FeedTagsEditor } from './FeedTagsEditor';
import { DEMO_WRITE_TOOLTIP } from '../demo/copy';

vi.mock('../demo/flag', () => ({ IS_DEMO: true }));
vi.mock('../api/community', () => ({
  getFeedTags: vi.fn(async () => ({ rss: [], episode: [], user: ['technology'], effective: ['technology'] })),
  setFeedUserTags: vi.fn(),
  getTagVocabulary: vi.fn(async () => ({
    vocabulary_version: 1, all_tags: ['technology', 'news'], podcast_genres: [],
    sponsor_industries: [], special_tags: [],
  })),
}));

afterEach(cleanup);

describe('FeedTagsEditor in demo', () => {
  test('disables Add tag and tag removal with the write tooltip', async () => {
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <FeedTagsEditor slug="example-podcast" />
      </QueryClientProvider>,
    );
    const add = await screen.findByRole('button', { name: /add tag/i }) as HTMLButtonElement;
    expect(add.disabled).toBe(true);
    expect(add.parentElement?.getAttribute('title')).toBe(DEMO_WRITE_TOOLTIP);

    const remove = screen.getByRole('button', { name: /remove/i }) as HTMLButtonElement;
    expect(remove.disabled).toBe(true);
    expect(remove.closest('[title]')?.getAttribute('title')).toBe(DEMO_WRITE_TOOLTIP);
  });
});
