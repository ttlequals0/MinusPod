/**
 * Component tests for AddFeed: the mode toggle, podcast search gating, the
 * local form fields, and the addLocalFeed submit path.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import AddFeed from './AddFeed';

const mockNavigate = vi.fn();

vi.mock('react-router', () => ({
  useNavigate: () => mockNavigate,
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

const defaultSettings = {
  podcastIndexApiKeyConfigured: false,
  podcastSearchProvider: { value: 'itunes', isDefault: true },
};

function renderAddFeed(settings: unknown = defaultSettings) {
  mockGetSettings.mockResolvedValue(settings);
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
      expect(screen.getByLabelText('Search podcasts or enter RSS URL')).toBeDefined();
    });
    expect(screen.getByText('Import from OPML')).toBeDefined();
    expect(screen.queryByLabelText('Title')).toBeNull();
  });

  it('switches to local mode and hides the OPML section', async () => {
    const user = userEvent.setup();
    renderAddFeed();
    await waitFor(() => {
      expect(screen.getByLabelText('Search podcasts or enter RSS URL')).toBeDefined();
    });

    await user.click(screen.getByRole('button', { name: 'Create local feed' }));

    expect(screen.getByLabelText('Title')).toBeDefined();
    expect(screen.getByLabelText('Slug')).toBeDefined();
    expect(screen.queryByText('Import from OPML')).toBeNull();
    expect(screen.queryByLabelText('Search podcasts or enter RSS URL')).toBeNull();
  });
});

describe('AddFeed: podcast search gating', () => {
  it('searches through iTunes with no PodcastIndex credentials and shows no banner', async () => {
    const user = userEvent.setup();
    mockSearchPodcasts.mockResolvedValue([
      { id: 1, title: 'The Daily Tech Show', description: '', artworkUrl: '', feedUrl: 'https://example.com/feed.xml', author: 'Acme', link: '' },
    ]);
    renderAddFeed();
    await waitFor(() => {
      expect(screen.getByLabelText('Search podcasts or enter RSS URL')).toBeDefined();
    });
    expect(screen.queryByText('Configure PodcastIndex API credentials')).toBeNull();

    await user.type(screen.getByLabelText('Search podcasts or enter RSS URL'), 'daily');

    await waitFor(() => {
      expect(mockSearchPodcasts).toHaveBeenCalledWith('daily', expect.any(AbortSignal));
    });
    await waitFor(() => {
      expect(screen.getByText('The Daily Tech Show')).toBeDefined();
    });
  });

  it('does not search when the input is a URL', async () => {
    const user = userEvent.setup();
    renderAddFeed();
    await waitFor(() => {
      expect(screen.getByLabelText('Search podcasts or enter RSS URL')).toBeDefined();
    });

    await user.type(screen.getByLabelText('Search podcasts or enter RSS URL'), 'https://example.com/feed.xml');

    await new Promise((r) => setTimeout(r, 500));
    expect(mockSearchPodcasts).not.toHaveBeenCalled();
  });

  it('falls back to the URL-only label until settings report a provider', async () => {
    renderAddFeed(new Promise(() => {}));
    expect(screen.getByLabelText('Podcast RSS Feed URL')).toBeDefined();
    expect(screen.queryByText('Configure PodcastIndex API credentials')).toBeNull();
  });
});

describe('AddFeed: local feed form', () => {
  async function openLocalMode() {
    const user = userEvent.setup();
    renderAddFeed();
    await waitFor(() => {
      expect(screen.getByLabelText('Search podcasts or enter RSS URL')).toBeDefined();
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
        expect.objectContaining({ title: 'My Archive Show' }),
      );
    });
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/feeds/my-archive-show', undefined);
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

  it('leaves the slug for the server to derive when the slug field was never edited by hand', async () => {
    mockAddLocalFeed.mockResolvedValue({
      slug: 'my-archive-show', feedType: 'local', feedUrl: 'https://x/my-archive-show.xml', message: 'created',
    });
    const user = await openLocalMode();
    // Typing only in Title auto-fills the Slug preview, but the field was
    // never edited directly -- the submitted payload must omit slug so the
    // server's own slugify rules produce the canonical value.
    await user.type(screen.getByLabelText('Title'), 'My Archive Show');
    await user.click(screen.getByRole('button', { name: 'Create feed' }));

    await waitFor(() => {
      expect(mockAddLocalFeed).toHaveBeenCalledWith(
        expect.objectContaining({ slug: undefined }),
      );
    });
  });

  it('sends the user-edited slug once the slug field has been touched', async () => {
    mockAddLocalFeed.mockResolvedValue({
      slug: 'custom-slug', feedType: 'local', feedUrl: 'https://x/custom-slug.xml', message: 'created',
    });
    const user = await openLocalMode();
    await user.type(screen.getByLabelText('Title'), 'My Archive Show');
    const slugInput = screen.getByLabelText('Slug');
    await user.clear(slugInput);
    await user.type(slugInput, 'custom-slug');
    await user.click(screen.getByRole('button', { name: 'Create feed' }));

    await waitFor(() => {
      expect(mockAddLocalFeed).toHaveBeenCalledWith(
        expect.objectContaining({ slug: 'custom-slug' }),
      );
    });
  });
});

describe('AddFeed: artwork upload after create', () => {
  async function openLocalModeWithArtwork() {
    const user = userEvent.setup();
    renderAddFeed();
    await waitFor(() => {
      expect(screen.getByLabelText('Search podcasts or enter RSS URL')).toBeDefined();
    });
    await user.click(screen.getByRole('button', { name: 'Create local feed' }));
    await user.type(screen.getByLabelText('Title'), 'My Archive Show');
    const file = new File(['data'], 'cover.jpg', { type: 'image/jpeg' });
    await user.upload(screen.getByLabelText('Artwork'), file);
    return user;
  }

  it('still navigates when the artwork upload fails, with a non-blocking notice', async () => {
    mockAddLocalFeed.mockResolvedValue({
      slug: 'my-archive-show', feedType: 'local', feedUrl: 'https://x/my-archive-show.xml', message: 'created',
    });
    mockUploadFeedArtwork.mockRejectedValue(new Error('413 Payload Too Large'));

    const user = await openLocalModeWithArtwork();
    await user.click(screen.getByRole('button', { name: 'Create feed' }));

    await waitFor(() => {
      expect(mockUploadFeedArtwork).toHaveBeenCalledWith('my-archive-show', expect.any(File));
    });
    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/feeds/my-archive-show', {
        state: { notice: 'Feed created. Artwork upload failed. Retry from the feed page.' },
      });
    });
  });

  it('passes the backend artwork warning through as a navigation notice', async () => {
    mockAddLocalFeed.mockResolvedValue({
      slug: 'my-archive-show', feedType: 'local', feedUrl: 'https://x/my-archive-show.xml', message: 'created',
    });
    mockUploadFeedArtwork.mockResolvedValue({
      message: 'ok',
      artworkUrl: '/artwork/my-archive-show.jpg',
      warning: 'Image is smaller than the recommended 1400x1400.',
    });

    const user = await openLocalModeWithArtwork();
    await user.click(screen.getByRole('button', { name: 'Create feed' }));

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/feeds/my-archive-show', {
        state: { notice: 'Feed created. Image is smaller than the recommended 1400x1400.' },
      });
    });
  });

  it('navigates with no notice when the artwork upload succeeds cleanly', async () => {
    mockAddLocalFeed.mockResolvedValue({
      slug: 'my-archive-show', feedType: 'local', feedUrl: 'https://x/my-archive-show.xml', message: 'created',
    });
    mockUploadFeedArtwork.mockResolvedValue({ message: 'ok', artworkUrl: '/artwork/my-archive-show.jpg' });

    const user = await openLocalModeWithArtwork();
    await user.click(screen.getByRole('button', { name: 'Create feed' }));

    await waitFor(() => {
      expect(mockNavigate).toHaveBeenCalledWith('/feeds/my-archive-show', undefined);
    });
  });
});
