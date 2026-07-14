/**
 * Component tests for the cross-fetch differential toggle and DAI-likely hint
 * in FeedSettingsPanel.tsx.
 *
 * Covers:
 *   - Toggle renders unchecked when differentialFetchEnabled is unset.
 *   - Enabling fires updateFeed with { differentialFetchEnabled: true }.
 *   - Disabling an enabled feed fires { differentialFetchEnabled: false }.
 *   - DAI-likely badge + hint render only when feed.daiLikely is true.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import FeedSettingsPanel from './FeedSettingsPanel';
import type { Feed } from '../../api/types';

// CollapsibleSection defaults closed; render children unconditionally.
vi.mock('../../components/CollapsibleSection', () => ({
  default: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

const mockUpdateFeed = vi.fn();

vi.mock('../../api/feeds', () => ({
  getNetworks: vi.fn().mockResolvedValue([]),
  updateFeed: (...args: unknown[]) => mockUpdateFeed(...args),
  CUE_SCORE_MIN: 0.30,
  CUE_SCORE_MAX: 0.99,
}));

vi.mock('../../api/settings', () => ({
  getSettings: vi.fn().mockResolvedValue({}),
}));

// FeedTagsEditor queries api/community internally; not under test here.
vi.mock('../../components/FeedTagsEditor', () => ({
  FeedTagsEditor: () => null,
}));

function makeFeed(overrides: Partial<Feed> = {}): Feed {
  return {
    slug: 'test-feed',
    title: 'Test Feed',
    sourceUrl: 'https://example.com/feed.xml',
    feedUrl: 'https://example.com/modified.xml',
    episodeCount: 3,
    ...overrides,
  };
}

function renderPanel(feed: Feed) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <FeedSettingsPanel feed={feed} slug={feed.slug} />
    </QueryClientProvider>,
  );
}

const SELECT_NAME = 'Fetch each episode twice to find inserted ads';

describe('FeedSettingsPanel cross-fetch differential control', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUpdateFeed.mockResolvedValue(makeFeed());
  });

  it('renders Auto when differentialFetchEnabled is unset', () => {
    renderPanel(makeFeed());
    const select = screen.getByRole('combobox', { name: SELECT_NAME }) as HTMLSelectElement;
    expect(select.value).toBe('');
  });

  it('selecting On fires updateFeed with differentialFetchEnabled true', async () => {
    renderPanel(makeFeed());
    await userEvent.selectOptions(screen.getByRole('combobox', { name: SELECT_NAME }), 'true');
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { differentialFetchEnabled: true });
  });

  it('selecting Off fires updateFeed with differentialFetchEnabled false', async () => {
    renderPanel(makeFeed({ differentialFetchEnabled: true }));
    const select = screen.getByRole('combobox', { name: SELECT_NAME }) as HTMLSelectElement;
    expect(select.value).toBe('true');
    await userEvent.selectOptions(select, 'false');
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { differentialFetchEnabled: false });
  });

  it('selecting Auto restores null so DAI feeds auto-enable again', async () => {
    renderPanel(makeFeed({ differentialFetchEnabled: false }));
    await userEvent.selectOptions(screen.getByRole('combobox', { name: SELECT_NAME }), '');
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { differentialFetchEnabled: null });
  });

  it('shows the effective state resolved by the server', () => {
    const { unmount } = renderPanel(makeFeed({ differentialFetchEffective: true }));
    expect(screen.getByText('Runs on this feed')).toBeDefined();
    unmount();
    renderPanel(makeFeed({ differentialFetchEffective: false }));
    expect(screen.getByText('Not running')).toBeDefined();
  });

  it('shows the DAI-likely badge only when daiLikely is true', () => {
    const { unmount } = renderPanel(makeFeed({ daiLikely: true }));
    expect(screen.getByText('DAI likely')).toBeDefined();
    unmount();
    renderPanel(makeFeed());
    expect(screen.queryByText('DAI likely')).toBeNull();
  });
});

describe('FeedSettingsPanel source URL row (#484)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUpdateFeed.mockResolvedValue(makeFeed());
  });

  it('renders the source URL with a copy button in read mode', () => {
    renderPanel(makeFeed());
    expect(screen.getByText('https://example.com/feed.xml')).toBeDefined();
    expect(screen.getByRole('button', { name: 'Copy source URL' })).toBeDefined();
  });

  it('saving a changed URL calls updateFeed with sourceUrl', async () => {
    renderPanel(makeFeed());
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
    const input = screen.getByPlaceholderText('https://example.com/feed.xml');
    await userEvent.clear(input);
    await userEvent.type(input, 'https://example.com/new-feed.xml');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', {
      sourceUrl: 'https://example.com/new-feed.xml',
    });
  });

  it('saving an unchanged URL exits edit mode without calling updateFeed', async () => {
    renderPanel(makeFeed());
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(mockUpdateFeed).not.toHaveBeenCalled();
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
  });

  it('saving an empty URL shows an inline error without calling updateFeed', async () => {
    renderPanel(makeFeed());
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await userEvent.clear(screen.getByPlaceholderText('https://example.com/feed.xml'));
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(mockUpdateFeed).not.toHaveBeenCalled();
    expect(screen.getByText('Source URL cannot be empty')).toBeDefined();
  });

  it('a rejected save surfaces the backend message and stays in edit mode', async () => {
    mockUpdateFeed.mockRejectedValue(new Error('Could not fetch a valid RSS feed from this URL'));
    renderPanel(makeFeed());
    await userEvent.click(screen.getByRole('button', { name: 'Edit' }));
    const input = screen.getByPlaceholderText('https://example.com/feed.xml');
    await userEvent.clear(input);
    await userEvent.type(input, 'https://example.com/broken.xml');
    await userEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('Could not fetch a valid RSS feed from this URL')).toBeDefined();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDefined();
  });
});
