import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { FeedOpmlExportDialog } from './FeedOpmlExportDialog';
import type { Feed } from '../api/types';

const mocks = vi.hoisted(() => ({ loadFeeds: vi.fn() }));

function feed(slug: string, title: string, titleOverride?: string): Feed {
  return {
    slug,
    title,
    titleOverride,
    sourceUrl: `https://example.com/${slug}.xml`,
    feedUrl: `https://example.com/${slug}`,
    episodeCount: 1,
  };
}

const FEEDS = [feed('alpha-show', 'Alpha Show'), feed('bravo-show', 'Bravo Show'), feed('charlie-show', 'Charlie Show')];

vi.mock('../api/feeds', () => ({
  feedsQueryOptions: {
    queryKey: ['feeds'],
    queryFn: mocks.loadFeeds,
  },
}));

const mockExportOpml = vi.fn<(mode?: string, slugs?: string[]) => Promise<void>>(async () => {});
vi.mock('../api/settings', () => ({
  exportOpml: (mode?: string, slugs?: string[]) => mockExportOpml(mode, slugs),
}));

function response(feeds: Feed[]) {
  return { feeds, lastRefreshCompletedAt: null, total: feeds.length, totalPages: 1, page: 1, limit: feeds.length };
}

function renderDialog(onClose = vi.fn(), client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  render(
    <QueryClientProvider client={client}>
      <FeedOpmlExportDialog open onClose={onClose} />
    </QueryClientProvider>,
  );
  return { onClose, client };
}

describe('FeedOpmlExportDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.loadFeeds.mockResolvedValue(response(FEEDS));
  });

  it('starts with every feed selected and exports all of them without a slug filter', async () => {
    const { onClose } = renderDialog();
    await screen.findByText('Charlie Show');
    expect(screen.getByText('3 of 3 selected')).toBeDefined();
    expect(screen.getByRole('dialog', { name: 'Export OPML' })).toBeDefined();

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

  it('uses feed title overrides in the picker and checkbox name', async () => {
    const feeds = [feed('renamed-show', 'Source title', 'Picker title')];
    mocks.loadFeeds.mockResolvedValueOnce(response(feeds));
    renderDialog();

    expect(await screen.findByText('Picker title')).toBeDefined();
    expect(screen.getByRole('checkbox', { name: 'Include Picker title' })).toBeDefined();
  });

  it('removes deleted feeds from a customized selection and leaves new feeds unselected', async () => {
    const { client } = renderDialog();
    await screen.findByText('Charlie Show');
    await userEvent.click(screen.getByRole('checkbox', { name: 'Include Bravo Show' }));
    await userEvent.click(screen.getByRole('checkbox', { name: 'Include Charlie Show' }));

    const replacement = feed('delta-show', 'Delta Show');
    client.setQueryData(['feeds'], response([FEEDS[0], replacement]));
    expect(await screen.findByText('1 of 2 selected')).toBeDefined();
    expect((screen.getByRole('checkbox', { name: 'Include Delta Show' }) as HTMLInputElement).checked).toBe(false);

    client.setQueryData(['feeds'], response([replacement]));
    expect(await screen.findByText('0 of 1 selected')).toBeDefined();
    expect((screen.getByRole('button', { name: 'Download 0 feeds' }) as HTMLButtonElement).disabled).toBe(true);
  });

  it('shows an initial feed-loading error and retries the request', async () => {
    mocks.loadFeeds.mockRejectedValueOnce(new Error('Feed request failed'));
    renderDialog();

    expect(await screen.findByText('Feed request failed')).toBeDefined();
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText('Charlie Show')).toBeDefined();
  });

  it('keeps cached feeds available and reports a failed refresh with a retry', async () => {
    const { client } = renderDialog();
    await screen.findByText('Charlie Show');
    mocks.loadFeeds.mockRejectedValueOnce(new Error('Refresh failed'));
    await client.refetchQueries({ queryKey: ['feeds'] });

    expect(await screen.findByText('Refresh failed. Showing the last loaded list.')).toBeDefined();
    expect(screen.getByText('Charlie Show')).toBeDefined();
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await waitFor(() => expect(screen.queryByText('Refresh failed. Showing the last loaded list.')).toBeNull());
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
    const { onClose } = renderDialog();
    await screen.findByText('Alpha Show');

    await userEvent.click(screen.getByRole('button', { name: 'Download 3 feeds' }));

    expect(await screen.findByText('slugs matched no feeds')).toBeDefined();
    expect(onClose).not.toHaveBeenCalled();
  });
});
