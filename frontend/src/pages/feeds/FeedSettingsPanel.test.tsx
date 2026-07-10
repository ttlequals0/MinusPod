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

const TOGGLE_NAME = 'Fetch each episode twice to find inserted ads';

describe('FeedSettingsPanel cross-fetch differential toggle', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockUpdateFeed.mockResolvedValue(makeFeed());
  });

  it('renders unchecked when differentialFetchEnabled is unset', () => {
    renderPanel(makeFeed());
    const toggle = screen.getByRole('switch', { name: TOGGLE_NAME });
    expect(toggle.getAttribute('aria-checked')).toBe('false');
  });

  it('enabling fires updateFeed with differentialFetchEnabled true', async () => {
    renderPanel(makeFeed());
    await userEvent.click(screen.getByRole('switch', { name: TOGGLE_NAME }));
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { differentialFetchEnabled: true });
  });

  it('disabling an enabled feed fires differentialFetchEnabled false', async () => {
    renderPanel(makeFeed({ differentialFetchEnabled: true }));
    const toggle = screen.getByRole('switch', { name: TOGGLE_NAME });
    expect(toggle.getAttribute('aria-checked')).toBe('true');
    await userEvent.click(toggle);
    expect(mockUpdateFeed).toHaveBeenCalledWith('test-feed', { differentialFetchEnabled: false });
  });

  it('shows the DAI-likely badge and hint only when daiLikely is true', () => {
    const { unmount } = renderPanel(makeFeed({ daiLikely: true }));
    expect(screen.getByText('DAI likely')).toBeDefined();
    expect(screen.getByText(/looks like this feed uses dynamic ad insertion/i)).toBeDefined();
    unmount();
    renderPanel(makeFeed());
    expect(screen.queryByText('DAI likely')).toBeNull();
    expect(screen.queryByText(/looks like this feed uses dynamic ad insertion/i)).toBeNull();
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
