/**
 * Component tests for AddFeed's local-feed mode: the mode toggle, the local
 * form fields, and the addLocalFeed submit path. The existing subscribe-mode
 * (URL/search/OPML) behavior is exercised elsewhere; these tests cover only
 * what Task 12 adds.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import AddFeed from './AddFeed';

const mockNavigate = vi.fn();

vi.mock('react-router', () => ({
  useNavigate: () => mockNavigate,
  Link: ({ children, to }: { children: React.ReactNode; to: string }) => (
    <a href={to}>{children}</a>
  ),
}));

const mockAddFeed = vi.fn();
const mockAddLocalFeed = vi.fn();
const mockUploadFeedArtwork = vi.fn();
const mockImportOpml = vi.fn();
const mockGetFeedsResponse = vi.fn();

vi.mock('../api/feeds', () => ({
  addFeed: (...a: unknown[]) => mockAddFeed(...a),
  addLocalFeed: (...a: unknown[]) => mockAddLocalFeed(...a),
  uploadFeedArtwork: (...a: unknown[]) => mockUploadFeedArtwork(...a),
  importOpml: (...a: unknown[]) => mockImportOpml(...a),
  feedsQueryOptions: {
    queryKey: ['feeds'],
    queryFn: (...a: unknown[]) => mockGetFeedsResponse(...a),
  },
}));

const mockSearchPodcasts = vi.fn();
vi.mock('../api/podcastSearch', () => ({
  searchPodcasts: (...a: unknown[]) => mockSearchPodcasts(...a),
}));

const mockGetSettings = vi.fn();
vi.mock('../api/settings', () => ({
  getSettings: (...a: unknown[]) => mockGetSettings(...a),
}));

function makeClient() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

function renderAddFeed() {
  mockGetSettings.mockResolvedValue({ podcastIndexApiKeyConfigured: false });
  mockGetFeedsResponse.mockResolvedValue({ feeds: [], lastRefreshCompletedAt: null });
  return render(
    <QueryClientProvider client={makeClient()}>
      <AddFeed />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('AddFeed: mode toggle', () => {
  it('starts in subscribe mode with the URL input and OPML section visible', async () => {
    renderAddFeed();
    await waitFor(() => {
      expect(screen.getByLabelText('Podcast RSS Feed URL')).toBeDefined();
    });
    expect(screen.getByText('Import from OPML')).toBeDefined();
    expect(screen.queryByLabelText('Title')).toBeNull();
  });

  it('switches to local mode and hides the OPML section', async () => {
    const user = userEvent.setup();
    renderAddFeed();
    await waitFor(() => {
      expect(screen.getByLabelText('Podcast RSS Feed URL')).toBeDefined();
    });

    await user.click(screen.getByRole('button', { name: 'Create local feed' }));

    expect(screen.getByLabelText('Title')).toBeDefined();
    expect(screen.getByLabelText('Slug')).toBeDefined();
    expect(screen.queryByText('Import from OPML')).toBeNull();
    expect(screen.queryByLabelText('Podcast RSS Feed URL')).toBeNull();
  });
});

describe('AddFeed: local feed form', () => {
  async function openLocalMode() {
    const user = userEvent.setup();
    renderAddFeed();
    await waitFor(() => {
      expect(screen.getByLabelText('Podcast RSS Feed URL')).toBeDefined();
    });
    await user.click(screen.getByRole('button', { name: 'Create local feed' }));
    return user;
  }

  it('auto-fills the slug from the title until the slug is edited by hand', async () => {
    const user = await openLocalMode();
    const titleInput = screen.getByLabelText('Title') as HTMLInputElement;
    const slugInput = screen.getByLabelText('Slug') as HTMLInputElement;

    await user.type(titleInput, 'My Archive Show');
    expect(slugInput.value).toBe('my-archive-show');

    await user.clear(slugInput);
    await user.type(slugInput, 'custom-slug');
    await user.type(titleInput, '!');
    expect(slugInput.value).toBe('custom-slug');
  });

  it('submits addLocalFeed with feedType local and navigates to the new feed', async () => {
    mockAddLocalFeed.mockResolvedValue({
      slug: 'my-archive-show',
      feedType: 'local',
      feedUrl: 'https://minuspod.example.com/feeds/my-archive-show.xml',
      message: 'created',
    });

    const user = await openLocalMode();
    await user.type(screen.getByLabelText('Title'), 'My Archive Show');
    await user.click(screen.getByRole('button', { name: 'Create feed' }));

    await waitFor(() => {
      expect(mockAddLocalFeed).toHaveBeenCalledWith(
        expect.objectContaining({
          title: 'My Archive Show',
          slug: 'my-archive-show',
        }),
      );
    });
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/feeds/my-archive-show');
    });
    expect(mockUploadFeedArtwork).not.toHaveBeenCalled();
  });

  it('shows the addLocalFeed error message on failure', async () => {
    mockAddLocalFeed.mockRejectedValue(new Error('title is required'));
    const user = await openLocalMode();
    await user.type(screen.getByLabelText('Title'), 'Show');
    await user.click(screen.getByRole('button', { name: 'Create feed' }));

    await waitFor(() => {
      expect(screen.getByText('title is required')).toBeDefined();
    });
  });
});
