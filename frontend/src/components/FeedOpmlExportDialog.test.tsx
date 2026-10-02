import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { FeedOpmlExportDialog } from './FeedOpmlExportDialog';
import type { Feed } from '../api/types';

function feed(slug: string, title: string): Feed {
  return { slug, title, sourceUrl: `https://example.com/${slug}.xml`, feedUrl: `https://example.com/${slug}`, episodeCount: 1 };
}

const FEEDS = [feed('alpha-show', 'Alpha Show'), feed('bravo-show', 'Bravo Show'), feed('charlie-show', 'Charlie Show')];

vi.mock('../api/feeds', () => ({
  feedsQueryOptions: {
    queryKey: ['feeds'],
    queryFn: async () => ({ feeds: FEEDS, lastRefreshCompletedAt: null, total: 3, totalPages: 1, page: 1, limit: 3 }),
  },
}));

const mockExportOpml = vi.fn<(mode?: string, slugs?: string[]) => Promise<void>>(async () => {});
vi.mock('../api/settings', () => ({
  exportOpml: (mode?: string, slugs?: string[]) => mockExportOpml(mode, slugs),
}));

function renderDialog(onClose = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <FeedOpmlExportDialog open onClose={onClose} />
    </QueryClientProvider>,
  );
  return onClose;
}

describe('FeedOpmlExportDialog', () => {
  beforeEach(() => vi.clearAllMocks());

  it('starts with every feed selected and exports all of them without a slug filter', async () => {
    const onClose = renderDialog();
    await screen.findByText('Charlie Show');
    expect(screen.getByText('3 of 3 selected')).toBeDefined();

    await userEvent.click(screen.getByRole('button', { name: 'Download 3 feeds' }));

    await waitFor(() => expect(mockExportOpml).toHaveBeenCalledWith('modified', undefined));
    expect(onClose).toHaveBeenCalled();
  });

  it('exports only the checked feeds', async () => {
    renderDialog();
    await screen.findByText('Bravo Show');

    await userEvent.click(screen.getByRole('checkbox', { name: 'Include Bravo Show' }));
    expect(screen.getByText('2 of 3 selected')).toBeDefined();
    await userEvent.click(screen.getByRole('button', { name: 'Download 2 feeds' }));

    await waitFor(() => expect(mockExportOpml).toHaveBeenCalledWith('modified', ['alpha-show', 'charlie-show']));
  });

  it('disables download once everything is deselected', async () => {
    renderDialog();
    await screen.findByText('Alpha Show');

    await userEvent.click(screen.getByRole('checkbox', { name: 'Deselect all' }));

    expect(screen.getByText('0 of 3 selected')).toBeDefined();
    expect((screen.getByRole('button', { name: 'Download 0 feeds' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('keeps the dialog open and shows the error when the export fails', async () => {
    mockExportOpml.mockRejectedValueOnce(new Error('slugs matched no feeds'));
    const onClose = renderDialog();
    await screen.findByText('Alpha Show');

    await userEvent.click(screen.getByRole('button', { name: 'Download 3 feeds' }));

    expect(await screen.findByText('slugs matched no feeds')).toBeDefined();
    expect(onClose).not.toHaveBeenCalled();
  });
});
